"""v0.8.13 notification selection, stable grouping, tiers and gate health."""
import json
import subprocess
import unittest
from unittest.mock import patch

from herdr_attention.notifications import build_items, row_counts
from herdr_attention.notify_gate import NotifyGate
from herdr_attention.store import Store
from tests.helpers import settings, Clock
from tests.test_api import make_collector


class ReadNotifications(unittest.TestCase):
    def setUp(self):
        self.conf = settings()
        self.agents = {'codex:old@term_1': {'identity':'codex:old@term_1', 'name':'Review', 'present':True}}

    def event(self, id=1, **patches):
        row = dict(id=id, at=100+id, identity='codex:old@term_1', workspace_id='w1', pane_id='w1:p1',
                   type='completion', title='Release PR review', evidence={'notify_tier':'fyi_finished'},
                   notify_state='sent', acked_at=None, resolved_at=None)
        row.update(patches)
        return row

    def items(self, events, **patches):
        args = dict(agents=self.agents, links=[], settings=self.conf, now=200)
        args.update(patches)
        return build_items(events, **args)

    def test_skip_and_generic_finish_are_history_only(self):
        quiet = dict(notify_state='dashboard')   # 0.9.8.7: a turn end that never went to the phone
        self.assertEqual(self.items([self.event(**quiet), self.event(2, notify_state='gated_skip')]), [])
        self.assertEqual(self.items([self.event()], gates={1:{'decision':'skip'}}), [])
        self.assertEqual(self.items([self.event(**quiet)], texts={1:{'title':'Review finished a turn', 'body':'Nobody else is waiting on it.', 'source':'template'}}), [])
        # One that did go to the phone keeps its row, so the alert can be found.
        self.assertEqual([n['id'] for n in self.items([self.event()])], [1])

    def test_codex_session_changes_group_by_pane_task_and_keep_best_headline(self):
        events = [self.event(), self.event(2, identity='codex:new@term_1')]
        texts = {1:{'title':'Review found 44 issues', 'body':'Two decisions remain.', 'source':'ai'},
                 2:{'title':'Finished: Release PR review', 'body':'Done', 'source':'template'}}
        items = self.items(events, texts=texts)
        best, = [n for n in items if n['is_group_latest']]
        self.assertEqual(best['what'], 'Review found 44 issues')
        self.assertEqual(best['group_count'], 2)
        self.assertEqual(set(best['group_ids']), {1,2})
        self.assertEqual(row_counts(items), {'unread':1,'read':0})
        # A new task in the same pane is a different row.
        events[1]['title'] = 'Another task'
        self.assertEqual(row_counts(self.items(events, texts=texts))['unread'], 2)

    def test_declared_task_survives_changing_terminal_status_titles(self):
        events = [self.event(title='Working', evidence={'task':'Release review'}),
                  self.event(2, title='Done', identity='codex:new@term_1', evidence={'task':'Release review'})]
        texts = {i:{'title':title, 'body':'Result.', 'source':'ai'} for i,title in ((1,'Review is ready'),(2,'Review has finished'))}
        self.assertEqual(row_counts(self.items(events,texts=texts))['unread'], 1)

    def test_actionable_beats_newer_fyi_and_read_groups_are_separate(self):
        events = [self.event(), self.event(2)]
        gates = {1:{'decision':'send','title':'Review needs your input','body':'Choose a plan.','tier':'waiting_on_you'},
                 2:{'decision':'send','title':'Review is ready','body':'Done.'}}
        items = self.items(events, gates=gates)
        self.assertTrue(items[0]['is_group_latest'])
        self.assertTrue(items[0]['actionable'])
        events[1]['acked_at'] = 150
        self.assertEqual(row_counts(self.items(events,gates=gates)), {'read':1,'unread':1})

    def test_shared_multi_agent_message_is_one_row(self):
        events = [self.event(), self.event(2, identity='codex:another', pane_id='w1:p2', title='Another task')]
        gate = {'decision':'send', 'title':'Three review agents may need attention', 'body':'Check their work.'}
        items = self.items(events, gates={1:gate,2:gate})
        self.assertEqual(row_counts(items)['unread'], 1)
        self.assertEqual(items[0]['group_count'], 2)

    def test_stale_gone_nonactionable_hidden_age_configurable(self):
        self.agents = {}
        gate = {1:{'decision':'send','title':'Review is ready','body':'Done.'}}
        self.assertEqual(self.items([self.event()], gates=gate, now=90000), [])
        self.conf.set_override('notifications.stale_hours', 48)
        self.assertEqual(len(self.items([self.event()], gates=gate, now=90000)), 1)
        self.conf.set_override('notifications.stale_hours', 24)
        gate[1]['tier'] = 'waiting_on_you'
        self.assertTrue(self.items([self.event()], gates=gate, now=90000)[0]['actionable'])

    def test_gate_tier_direct_ask_worker_and_quota(self):
        gate = {1:{'decision':'send','title':'Robot network setup needs your input','body':'Choose a network.'}}
        item, = self.items([self.event()], gates=gate)
        self.assertEqual(item['tier'], 'waiting_on_you')
        self.assertTrue(item['actionable'])
        self.assertEqual(item['next_action']['kind'], 'open_agent')
        links = [{'from':'codex:old@term_1','to':'codex:lead','kind':'reports_to', 'to_name':'lead'}]
        item, = self.items([self.event()], gates=gate, links=links)
        self.assertEqual(item['tier'], 'waiting_on_coordinator')
        self.assertFalse(item['actionable'])
        # An explicit gate tier must not downgrade a rule-based human prompt.
        from herdr_attention.notify_tiers import gate_tier
        self.assertEqual(gate_tier('waiting_on_you', {'decision':'send','tier':'might_look'}), 'waiting_on_you')
        gate[1]['title'] = 'Stack quickstart guide is blocked by usage limit'
        item, = self.items([self.event()], gates=gate)
        self.assertEqual(item['tier_label'], 'Out of quota')
        self.assertEqual(item['tier'], 'might_look')
        gate[1]['title'] = 'Review is out of quota'
        item, = self.items([self.event()], gates=gate)
        self.assertEqual(item['tier_label'], 'Out of quota')

    def test_status_label_is_never_used_as_agent_name(self):
        self.agents = {'codex:old@term_1':{'name':'Out of quota','title':'Out of quota','present':True}}
        item, = self.items([self.event(type='quota_stop', title='Out of quota')])
        self.assertEqual(item['agent']['name'], 'Agent w1:p1')
        self.assertEqual(item['what'], 'Agent w1:p1 is out of quota')

    def test_gate_failure_uses_rules_and_bounded_technical_reason(self):
        event = self.event(type='failure', evidence={'notify_tier':'might_look'})
        gate = {1:{'decision':'fallback','error':'429 password=secret','title':'bad model output'}}
        item, = self.items([event], gates=gate)
        self.assertEqual(item['what'], 'Review stopped unexpectedly')
        self.assertEqual(item['gate'], {'decision':'unavailable','reason':'account or rate limit'})
        self.assertNotIn('secret', json.dumps(item))
        self.assertEqual(self.items([self.event(notify_state='dashboard')],gates=gate), [])
        self.assertEqual(self.items([self.event(notify_state='dashboard')],gates=gate,texts={1:{'title':'Finished: Review','body':'Done.','source':'template'}}), [])

    def test_expired_request_remains_in_agent_history_with_clear_state(self):
        collector = make_collector()
        self.addCleanup(collector.store.close)
        id = collector.store.add_event(at=collector.clock(), identity='claude:a',workspace_id='w1',pane_id='w1:p1',type='question', evidence={'notify_tier':'waiting_on_you'},confidence='high',seen_late=False,notify_state='expired',title='Choose the network')
        collector.store.resolve_event(id,collector.clock())
        self.assertNotIn(id,[n['id'] for n in collector.notifications()['items']])
        event = next(e for e in collector.engine.agent_detail('claude:a')['events'] if e['id']==id)
        self.assertEqual(event['state_label'],'No longer needed')
        self.assertIsNotNone(event['resolved_at'])


class GateHealth(unittest.TestCase):
    def setUp(self):
        self.store=Store(':memory:');self.addCleanup(self.store.close)
        self.clock=Clock(100)
        self.gate=NotifyGate(self.store,settings(),self.clock)

    def job(self, id):
        self.gate.queue([{'id':id,'identity':'codex:test'}], 'test',tiers=['fyi_finished'])
        return self.gate.next_job()

    def test_explicit_model_tier_persists_and_is_available_per_event(self):
        job=self.job(1)
        with patch('herdr_attention.notify_gate.invoke',return_value=({'send':True,'title':'Need a decision','body':'Choose a plan','tier':'waiting_on_you'},{})):
            result=self.gate.run_job(job)
        self.assertEqual(result['tier'],'waiting_on_you')
        self.assertEqual(self.gate.decisions_by_event([1])[1]['tier'],'waiting_on_you')

    def test_health_tracks_continuous_failure_since_and_recovers(self):
        self.gate._finish(self.job(1),'fallback','quota wall')
        self.clock.advance(20)
        self.gate._finish(self.job(2),'fallback','rate limited')
        self.assertEqual(self.gate.status()['health'],{'state':'limited','since':100})
        self.clock.advance(20)
        self.gate._finish(self.job(3),'send','useful',title='Result ready')
        self.assertEqual(self.gate.status()['health'],{'state':'OK','since':None})

    def test_notifier_uses_gate_action_tier_for_delivery(self):
        from tests.test_notify import Rig
        rig=Rig(notify_gate={'enabled':True})
        self.addCleanup(rig.store.close)
        rig.notifier.gate=NotifyGate(rig.store,rig.settings,rig.clock)
        rig.finish('w1:p1','a')
        event=rig.store.recent_events(1)[0]
        rig.notifier.gate.queue([event], 'test', tiers=['fyi_finished'])
        job=rig.notifier.gate.next_job()
        rig.notifier.gate._finish(job,'send','Needs an answer', title='Review needs your input', tier='waiting_on_you')
        self.assertEqual(rig.notifier._tier_of(event),'waiting_on_you')

    def test_timeout_and_restart_are_visible_in_decisions(self):
        job=self.job(1)
        with patch('herdr_attention.notify_gate.invoke',side_effect=subprocess.TimeoutExpired('fake',20)):
            self.gate.run_job(job)
        self.assertEqual(self.gate.status()['health']['state'],'failing')
        self.assertEqual(self.gate.decisions_by_event([1])[1]['decision'],'fallback')
        self.job(2)
        gate=NotifyGate(self.store,self.gate.settings,self.clock)
        self.assertEqual(gate.decisions_by_event([2])[2]['decision'],'fallback')
        self.assertEqual(gate.decision_for([2])['decision'],'fallback')
