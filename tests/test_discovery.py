"""Metadata discovery fixtures; no live panes, transcripts or model processes."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from herdr_attention.collector import Collector
from herdr_attention.herdr import HerdrError
from tests.helpers import Clock, FakeHerdr, agent, settings


class Discovery(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.herdr = FakeHerdr([agent(seq=1)])
        self.c = Collector(db_path=":memory:", client=self.herdr, settings=settings(), clock=self.clock)
        self.addCleanup(self.c.store.close)

    def present(self):
        return {a['identity'] for a in self.c.view(local=False)['agents'] if a['present']}

    def test_missing_events_periodic_and_metadata_only(self):
        self.assertTrue(self.c.reconcile_due(0))
        self.herdr.set(agent('w1:p2', session='new', seq=2))
        self.assertFalse(self.c.reconcile_due(44))
        self.assertTrue(self.c.reconcile_due(45))
        self.assertEqual(self.present(), {'claude:s1', 'claude:new'})
        self.assertFalse(any(c[0] == 'read' for c in self.herdr.calls))
        self.assertEqual(self.c.engine.herdr['last_reconcile'], self.clock())

    def test_real_snake_case_nested_and_incomplete_events_coalesce(self):
        self.c.reconcile_due(0)
        self.herdr.set(agent('w1:p2', session='new'))
        for kind in ('pane_created', 'pane_updated', 'pane_agent_detected', 'pane_moved', 'pane.created'):
            self.c.handle_event({'event': kind, 'data': {'pane': {'pane_id': 'w1:p2'}}})
        self.c.handle_event({'event': 'pane_agent_detected', 'data': {}})
        self.assertFalse(self.c.reconcile_due(1))
        self.assertTrue(self.c.reconcile_due(2))
        self.assertFalse(self.c.reconcile_due(3))
        self.assertEqual(len(self.present()), 2)
        self.assertEqual(sum(x[0] == 'agents' for x in self.herdr.calls), 2)

    def test_out_of_order_removal_reuse_rename_and_shell(self):
        self.c.reconcile_due(0)
        self.herdr.set(agent(session='replacement', seq=3, name='new-name'))
        self.c.handle_event({'event': 'pane_closed', 'data': {'pane_id': 'w1:p1'}})
        self.c.reconcile_due(2)
        self.assertEqual(self.present(), {'claude:replacement'})
        self.herdr.set(agent(session='replacement', seq=3, name='renamed'))
        self.c.reconcile_due(47)
        self.assertEqual(self.present(), {'claude:replacement'})
        self.herdr.set(agent(kind=None, session=None))
        self.c.reconcile_due(92)
        self.assertEqual(self.present(), set())
        self.assertFalse(any(i.startswith('term:') for i in self.c.engine.agents))

    def test_session_identification_preserves_read_state(self):
        info = agent(session=None, status='done', seq=1)
        self.herdr.set(info)
        self.c.reconcile_due(0)
        event = self.c.store.open_events()[0]
        self.c.ack(event['id'])
        info['agent_session'] = {'agent': 'claude', 'value': 'learned'}
        self.herdr.set(info)
        self.c.reconcile_due(45)
        self.assertEqual(self.present(), {'claude:learned'})
        self.assertEqual(len(self.c.store.recent_events(100)), 1)
        self.assertTrue(self.c.store.event(event['id'])['acked_at'])

    def test_new_watch_disable_and_privacy(self):
        self.herdr.set(agent('w2:p1', session='private'))
        self.c.reconcile_due(0)
        self.assertEqual(self.present(), {'claude:s1'})
        self.assertNotIn(('explain', 'w2:p1'), self.herdr.calls)
        self.c.update_settings({'watch.enabled_workspaces': ['w1', 'w2']}, local=True)
        self.c.reconcile_due(2)
        self.assertEqual(self.present(), {'claude:s1', 'claude:private'})
        self.c.update_settings({'watch.enabled_workspaces': ['w1']}, local=True)
        self.c.reconcile_due(4)
        self.assertEqual(self.present(), {'claude:s1'})
        self.assertFalse(any(x[0] == 'read' or x == ('explain', 'w2:p1') for x in self.herdr.calls[:2]))
        self.assertEqual(self.c.engine.trace_targets(), ['claude:s1'])

    def test_backoff_cannot_be_bypassed_by_event_storm(self):
        with patch.object(self.herdr, 'agents', side_effect=HerdrError('unavailable')) as scan:
            self.assertFalse(self.c.reconcile_due(0))
            for t in range(1, 5):
                self.c.handle_event({'event': 'pane_created', 'data': {}})
                self.assertFalse(self.c.reconcile_due(t))
            self.assertEqual(scan.call_count, 1)
            self.c.reconcile_due(5)
            self.assertEqual(scan.call_count, 2)
        self.assertEqual(self.c.engine.herdr['reconcile_failures'], 2)
        self.assertTrue(self.c.reconcile_due(15))
        self.assertIsNone(self.c.engine.herdr['reconcile_error'])

    def test_disconnect_reconnect_and_startup_no_replayed_alert(self):
        self.herdr.set(agent(status='done', seq=4))
        self.c.reconcile_due(0)
        event = self.c.store.open_events()[0]
        self.c.ack(event['id'])
        self.c.engine.set_herdr_status(False, error='disconnected')
        self.herdr.set(agent('w1:p2', session='arrived'))
        self.c._reconcile_requested.set()  # subscription acknowledgement
        self.c.reconcile_due(2)
        self.assertEqual(self.present(), {'claude:s1', 'claude:arrived'})
        self.assertEqual(len(self.c.store.recent_events(100)), 1)
        self.assertTrue(self.c.store.event(event['id'])['acked_at'])
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / 'state.db'
            first = Collector(db_path=db, client=self.herdr, settings=settings())
            first.handle_snapshot()
            event = first.store.open_events()[0]
            first.ack(event['id'])
            first.store.close()
            second = Collector(db_path=db, client=self.herdr, settings=settings())
            try:
                second.reconcile_due(0)
                self.assertEqual(len(second.store.recent_events(100)), 1)
                self.assertTrue(second.store.event(event['id'])['acked_at'])
            finally:
                second.store.close()

    def test_move_and_reassignment_order_preserve_alert(self):
        self.herdr.set(agent(status='done', seq=1))
        self.c.reconcile_due(0)
        event = self.c.store.open_events()[0]
        self.c.ack(event['id'])
        self.herdr.set(agent(session='replacement', seq=2))
        self.herdr.set(agent('w1:p2', status='done', session='s1', seq=1))
        self.c.reconcile_due(45)
        self.assertEqual(self.present(), {'claude:s1', 'claude:replacement'})
        self.assertIsNone(self.c.store.event(event['id'])['resolved_at'])
        self.assertTrue(self.c.store.event(event['id'])['acked_at'])

    def test_null_evidence_history_regression(self):
        self.herdr.set(agent(status='done'))
        self.c.handle_snapshot()
        event = self.c.store.open_events()[0]
        from herdr_attention.store import EVENT_COLUMNS
        columns = ', '.join('NULL AS evidence' if k == 'evidence' else k for k in EVENT_COLUMNS)
        rows = self.c.store._rows(f'SELECT {columns} FROM events WHERE id = ?', (event['id'],))
        self.assertEqual(rows[0]['evidence'], {})

    def test_topology_storm_is_coalesced_before_worker_queue(self):
        for _ in range(1000):
            self.c._on_herdr_event({'event': 'pane_updated', 'data': {'pane': {'pane_id': 'w1:p1'}}})
        self.assertTrue(self.c.queue.empty())
        self.assertTrue(self.c._reconcile_requested.is_set())
        self.assertTrue(self.c.reconcile_due(0))
        self.assertEqual(sum(x[0] == 'agents' for x in self.herdr.calls), 1)
