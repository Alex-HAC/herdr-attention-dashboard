"""Finished hook children and acknowledgements of old notification episodes."""
import copy
import json
import unittest

from herdr_attention.content import decorate_subagents, read_action
from herdr_attention.sidebar import placements, Sidebar, SOURCE
from tests.helpers import agent
from tests.test_api import make_collector


def add_hook_children(collector, parent='claude:a', finished=21):
    for i in range(finished+1):
        identity=f'claude:child-{i}'
        collector.store._exec('INSERT INTO hook_agents VALUES (?, ?, ?, ?, ?)',
            (identity,parent,'fixture','done' if i<finished else 'working',collector.clock()+i))
        collector.relations.link(identity,'spawned_by',parent,at=collector.clock(),provenance='observed-spawn',actor='fixture')


class FinishedSubagents(unittest.TestCase):
    def setUp(self):
        self.collector=make_collector();self.addCleanup(self.collector.store.close)
        add_hook_children(self.collector)

    def test_finished_children_fold_under_parent_without_counting_or_fake_panes(self):
        view=self.collector.view(local=False)
        parent=next(a for a in view['agents'] if a['identity']=='claude:a')
        virtual=[a for a in view['agents'] if a.get('in_process')]
        self.assertEqual(len(parent['finished_subagents']),21)
        self.assertEqual(parent['finished_subagents_label'],'Finished subagents · 21')
        self.assertEqual(sum(a['folded'] for a in virtual),21)
        self.assertFalse(next(a for a in virtual if a['status']=='working')['folded'])
        self.assertTrue(all(a['pane_id'] is None and a['event_id'] is None for a in virtual))
        workspace=view['workspaces'][0]
        self.assertEqual(workspace['agent_count'],2)
        self.assertEqual(workspace['folded_subagent_count'],21)
        self.assertEqual(view['counts']['state_done'],1)
        self.assertEqual(len(workspace['tabs'][0]['agents']),2)
        self.assertEqual(len(placements(view)),1)
        self.assertEqual(self.collector.store._exec('SELECT COUNT(*) FROM hook_agents').fetchone()[0],22)
        self.assertIsNotNone(self.collector.agent_detail(virtual[0]['identity']))

    def test_closed_acknowledged_and_nested_children_keep_one_native_parent_fold(self):
        view=self.collector.view(local=False)
        done=next(a for a in view['agents'] if a.get('folded'))
        working=next(a for a in view['agents'] if a.get('in_process') and a['status']=='working')
        nested=copy.deepcopy(working);nested.update(identity='claude:nested',in_process_parent=done['identity'],status='closed',present=False)
        view['agents'].append(nested)
        done.update(status='idle',acked=True)
        working['acked']=True
        decorate_subagents(view)
        self.assertTrue(done['folded'])
        self.assertFalse(working['folded'],'working children stay visible despite an inherited/old ack')
        self.assertEqual(nested['fold_parent'],'claude:a')
        parent=next(a for a in view['agents'] if a['identity']=='claude:a')
        self.assertEqual(len(parent['finished_subagents']),22)

    def test_closed_retained_parent_keeps_children_reachable_without_native_rows(self):
        engine=self.collector.engine
        engine.pane_gone('w1:p1','closed')
        for event in self.collector.store.open_events('claude:a'):
            self.collector.store.resolve_event(event['id'],self.collector.clock())
        self.assertNotIn('claude:a',[a['identity'] for a in engine.view()['agents']])
        view=self.collector.view(local=False)
        parent=next(a for a in view['agents'] if a['identity']=='claude:a')
        self.assertFalse(parent['present'])
        self.assertEqual(len(parent['finished_subagents']),22)
        self.assertEqual(placements(view),[])
        self.assertIsNotNone(self.collector.agent_detail('claude:child-0'))

    def test_parent_sidebar_summary_keeps_ranks_and_role_badges_across_ticks(self):
        info=agent('w1:p1',session='a',name='alpha',status='done')
        class Client:
            def __init__(self):self.calls=[]
            def agent(self,pane):return info
            def call(self,method,params):
                self.calls.append((method,copy.deepcopy(params)))
                return {'active':True,'source':SOURCE}
        client=Client();sidebar=Sidebar(client=client)
        view=self.collector.view(local=False)
        parent=next(a for a in view['agents'] if a['identity']=='claude:a')
        parent['roles']={'role':[{'value':'coordinator'}]}
        before=[(a['identity'],d) for a,d in placements(view)]
        sidebar.update(view);view['agents'].reverse();sidebar.update(view)
        writes=[p for method,p in client.calls if method=='pane.report_metadata']
        self.assertEqual(len(writes),2)
        self.assertEqual(writes[0],writes[1])
        self.assertIn('[coordinator]',writes[0]['display_agent'])
        self.assertIn('Finished subagents · 21',writes[0]['display_agent'])
        self.assertEqual(writes[0]['pane_id'],'w1:p1')
        self.assertEqual(writes[0]['ttl_ms'],10000)
        self.assertEqual(before,[(a['identity'],d) for a,d in placements(view)])


class ReadLeftovers(unittest.TestCase):
    def setUp(self):
        self.collector=make_collector();self.addCleanup(self.collector.store.close)

    def event(self,id,kind='completion',**extra):
        store=self.collector.store
        id_created=store.add_event(at=self.collector.clock(),identity='claude:gone',workspace_id='w1',pane_id='w1:p3B',
            type=kind,evidence={'notify_tier':'fyi_finished' if kind=='completion' else 'might_look'},confidence='high',seen_late=False,notify_state='sent',title='Review')
        store._exec('UPDATE events SET id=? WHERE id=?',(id,id_created))
        store.save_notification_text([id],'Review finished' if kind=='completion' else 'Agent w1:p3B is out of quota','Result.','ai',self.collector.clock())
        if extra.get('resolved'):store.resolve_event(id,self.collector.clock())
        return id

    def quota_fixture(self):
        self.event(2341,'quota_stop',resolved=True)
        self.event(2398,'quota_stop',resolved=True)
        self.collector.store._exec('INSERT INTO agent_quota_stops VALUES (?,?,?,?,?,?,?,?,?,?,?)',
            ('claude:gone','claude','claude:work','Work',100,None,'fixture',2398,None,0,0))

    def test_quota_leftover_acks_exact_event_without_acknowledging_newer_episode(self):
        self.quota_fixture()
        self.assertEqual(self.collector.view(local=False)['quota_stops']['groups'],[])
        self.assertTrue(self.collector.ack(2341))
        self.assertIsNotNone(self.collector.store.event(2341)['acked_at'])
        self.assertIsNone(self.collector.store.event(2398)['acked_at'])
        self.assertIsNone(self.collector.quota_stops.get('claude:gone')['acked_at'])
        self.assertTrue(self.collector.ack(2341),'repeated acknowledgement is idempotent')
        unread=[n['id'] for n in self.collector.notifications()['items'] if not n['read']]
        self.assertNotIn(2341,unread)

    def test_read_all_clears_gone_quota_leftover_and_mixed_resolved_groups(self):
        self.quota_fixture()
        for id in (2390,2387,2374,2367,2389,2383,2381,2378,2370,2366,2364):
            self.event(id,resolved=True)
        self.collector.notifications_read_all()
        for id in (2341,2398,2390,2387,2374,2367,2389,2383,2381,2378,2370,2366,2364):
            self.assertIsNotNone(self.collector.store.event(id)['acked_at'])
        self.assertEqual(self.collector.notifications()['unread'],0)
        self.assertEqual(self.collector.notifications()['counts']['unread'],0)

    def test_current_quota_episode_ack_still_hides_the_group(self):
        self.quota_fixture()
        self.collector.store._exec("UPDATE agent_quota_stops SET active=1 WHERE identity='claude:gone'")
        self.assertTrue(self.collector.ack(2398))
        self.assertIsNotNone(self.collector.quota_stops.get('claude:gone')['acked_at'])

    def test_engine_does_not_resolve_quota_event_as_a_completion(self):
        engine=self.collector.engine
        event=self.collector.store.add_event(at=self.collector.clock(),identity='claude:a',workspace_id='w1',pane_id='w1:p1',type='quota_stop',evidence={},confidence='high',seen_late=False,notify_state='sent')
        engine.tick()
        self.assertIsNone(self.collector.store.event(event)['resolved_at'])

    def test_completion_state_has_no_read_after_done_idle_done_regeneration(self):
        current=agent('w1:p1','done',session='a',name='alpha',title='Visible task')
        self.collector.engine.apply_agent(current)
        first=next(a for a in self.collector.view(local=False)['agents'] if a['identity']=='claude:a')
        self.assertFalse(first['read_action']['available'])
        self.assertIn('Clears when',first['read_action']['reason'])
        self.collector.engine.apply_agent({**current,'agent_status':'idle'})
        self.collector.engine.apply_agent(current)
        second=next(a for a in self.collector.view(local=False)['agents'] if a['identity']=='claude:a')
        self.assertNotEqual(first['event_id'],second['event_id'])
        self.assertFalse(second['read_action']['available'])

    def test_acknowledged_completion_episode_stays_read_until_new_work(self):
        engine=self.collector.engine
        current=agent('w1:p1','done',session='a',name='alpha',title='Visible task')
        engine.apply_agent(current)
        id=next(a for a in engine.view()['agents'] if a['identity']=='claude:a')['event_id']
        self.assertTrue(self.collector.ack(id))
        engine.apply_agent({**current,'agent_status':'idle'});engine.apply_agent(current)
        later=next(a for a in engine.view()['agents'] if a['identity']=='claude:a')
        self.assertNotEqual(id,later['event_id'])
        self.assertTrue(later['acked'])
        self.assertEqual(self.collector.store.event(later['event_id'])['notify_state'],'acked')
        engine.apply_agent({**current,'agent_status':'working'});engine.apply_agent(current)
        self.assertFalse(next(a for a in engine.view()['agents'] if a['identity']=='claude:a')['acked'])

    def test_question_read_survives_idle_flicker_and_expired_ack_is_idempotent(self):
        engine=self.collector.engine
        current=agent('w1:p1','blocked',session='a',name='alpha',title='Visible task')
        engine.apply_agent(current);engine.set_blocked_rule('claude:a','mcp_elicitation_prompt')
        id=next(a for a in engine.view()['agents'] if a['identity']=='claude:a')['event_id']
        self.assertTrue(self.collector.ack(id))
        engine.apply_agent({**current,'agent_status':'idle'});engine.apply_agent(current)
        engine.set_blocked_rule('claude:a','mcp_elicitation_prompt')
        later=next(a for a in engine.view()['agents'] if a['identity']=='claude:a')
        self.assertTrue(later['acked'])
        self.collector.store.set_notify_state([id],'expired')
        self.assertTrue(self.collector.ack(id))

    def test_reading_old_resolved_completion_does_not_dismiss_new_work(self):
        engine=self.collector.engine
        current=agent('w1:p1','done',session='a',name='alpha',title='Visible task')
        engine.apply_agent(current)
        old=next(a for a in engine.view()['agents'] if a['identity']=='claude:a')['event_id']
        engine.apply_agent({**current,'agent_status':'working'});engine.apply_agent(current)
        self.assertTrue(self.collector.ack(old))
        engine.apply_agent({**current,'agent_status':'idle'});engine.apply_agent(current)
        self.assertFalse(next(a for a in engine.view()['agents'] if a['identity']=='claude:a')['acked'])

    def test_a_different_question_is_not_suppressed_by_an_old_read(self):
        engine=self.collector.engine
        current=agent('w1:p1','blocked',session='a',name='alpha',title='Visible task')
        engine.apply_agent(current);engine.set_blocked_rule('claude:a','mcp_elicitation_prompt','Choose network A?')
        old=next(a for a in engine.view()['agents'] if a['identity']=='claude:a')['event_id']
        self.assertTrue(self.collector.ack(old))
        engine.apply_agent({**current,'agent_status':'idle'});engine.apply_agent(current)
        engine.set_blocked_rule('claude:a','mcp_elicitation_prompt','Choose network B?')
        self.assertFalse(next(a for a in engine.view()['agents'] if a['identity']=='claude:a')['acked'])
