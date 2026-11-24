"""Pane identity when Herdr reports a duplicated native session (including across workspaces)."""
import copy
from pathlib import Path
import tempfile
import unittest
from herdr_attention.collector import Collector
from herdr_attention.engine import Engine
from herdr_attention.store import Store
from herdr_attention.traces import split_identity
from tests.helpers import FakeHerdr, Clock, agent, settings, workspaces
from tests.trace_fixtures import sid


class SessionCollisions(unittest.TestCase):
    def make(self, db=':memory:'):
        c = Collector(db_path=db, settings=settings(watch={'enabled_workspaces':['w4','w7']}),
                      client=FakeHerdr([],workspaces('w4','w7')), clock=Clock())
        self.addCleanup(c.store.close)
        return c

    def records(self):
        return [agent('w4:p45',status='idle',session=sid('collision'),name='codex-k1',kind='codex'),
                agent('w4:p48',status='working',session=sid('collision'),name='night-c2',kind='codex')]

    def current(self,c):
        return {a['pane_id']:a for a in c.view(local=False)['agents'] if a['present']}

    def test_both_live_panes_keep_distinct_state_and_history(self):
        c=self.make()
        first,second=self.records()
        c.client.set(first);c.handle_snapshot()
        base='codex:'+sid('collision')
        c.relation_action({'action':'set','subject':'w4:p45','kind':'role','value':'worker'},local=True)
        c.client.set(second);c.handle_snapshot()
        cards=self.current(c)
        self.assertEqual(set(cards),{'w4:p45','w4:p48'})
        self.assertEqual(cards['w4:p45']['identity'],base)
        child=cards['w4:p48']['identity']
        self.assertEqual(child,base+'@'+second['terminal_id'])
        self.assertEqual(cards['w4:p45']['status'],'idle')
        self.assertEqual(cards['w4:p48']['status'],'working')
        self.assertEqual(cards['w4:p48']['roles']['role'],[])
        self.assertTrue(all(a['session_collision'] for a in cards.values()))
        self.assertEqual(c.resolve_agent('w4:p48'),child)
        self.assertEqual(c.engine.output_targets(),[(child,'w4:p48')])
        c.client.set({**second,'agent_status':'done','state_change_seq':12345})
        c.handle_event({'event':'pane.agent_status_changed','data':{'pane_id':'w4:p48'}})
        self.assertEqual(self.current(c)['w4:p45']['status'],'idle')
        self.assertTrue(all(e['identity']==child for e in c.store.events_for(child,20)))
        self.assertEqual(split_identity(child),('codex',sid('collision')))

    def test_reversed_snapshots_moves_restart_and_other_pane_closing_keep_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            c=self.make(Path(directory)/'state.db')
            first,second=self.records()
            c.client.set(first);c.client.set(second);c.handle_snapshot()
            before={p:a['identity'] for p,a in self.current(c).items()}
            c.client.agents_by_pane=dict(reversed(list(c.client.agents_by_pane.items())))
            c.handle_snapshot()
            self.assertEqual({p:a['identity'] for p,a in self.current(c).items()},before)
            c.client.remove('w4:p45');c.handle_snapshot()
            remaining=self.current(c)['w4:p48']
            self.assertEqual(remaining['identity'],before['w4:p48'])
            self.assertFalse(remaining['session_collision'])
            restarted=self.make(Path(directory)/'state.db')
            restarted.client.set(second);restarted.handle_snapshot()
            self.assertEqual(self.current(restarted)['w4:p48']['identity'],before['w4:p48'])
            moved={**second,'pane_id':'w7:p5','workspace_id':'w7','tab_id':'w7:t1'}
            restarted.client.remove('w4:p48');restarted.client.set(moved);restarted.handle_snapshot()
            self.assertEqual(self.current(restarted)['w7:p5']['identity'],before['w4:p48'])

    def test_incremental_collision_cannot_overwrite_existing_workspace_owner(self):
        c=self.make()
        original,duplicate=self.records()
        original.update(pane_id='w7:p1',workspace_id='w7',tab_id='w7:t1')
        c.client.set(original);c.handle_snapshot()
        c.client.set(duplicate)
        c.handle_event({'event':'pane.agent_status_changed','data':{'pane_id':'w4:p48'}})
        cards=self.current(c)
        self.assertEqual(len(cards),2)
        self.assertEqual(cards['w7:p1']['workspace_id'],'w7')
        self.assertEqual(c.view(local=False)['workspaces'][0]['live_agent_count'],1)
        self.assertNotEqual(cards['w7:p1']['identity'],cards['w4:p48']['identity'])

    def test_shared_transcripts_cannot_infer_a_spawn_or_create_a_summary(self):
        c=self.make()
        for record in self.records():c.client.set(record)
        c.handle_snapshot()
        cards=list(self.current(c).values())
        c.engine.set_trace(cards[0]['identity'],{'links':[{'kind':'spawned_by','target':cards[1]['identity'],'source':'shared trace'}]})
        self.assertEqual(c.view(local=False)['links'],[])
        self.assertIsNone(c.summary_input(cards[0]['identity'],'auto'))
        with self.assertRaisesRegex(ValueError,'ambiguous'):
            c.request_summary(cards[0]['identity'],'refresh')

    def test_parent_tags_are_ignored_when_native_parent_claim_is_ambiguous(self):
        from herdr_attention.linking import session_hash, validated_parent
        first,second=self.records()
        child=agent('w4:p9',session=sid('unique-child'),kind='codex')
        parent='codex:'+sid('collision')
        child['tokens']={'self':session_hash('codex:'+sid('unique-child')), 'parent':'w4:p45',
                         'hat_parent_self':session_hash(parent)}
        self.assertIsNone(validated_parent(child,[first,second,child]))
