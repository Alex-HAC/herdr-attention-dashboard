"""Routing/launch contracts: account isolation, durable links, hooks and quota fallback."""
import copy
import json
import os
from pathlib import Path
import shlex
import tempfile
import unittest
import uuid
from unittest.mock import patch

from herdr_attention.routing import load, validate, pick, RoutingError
from herdr_attention.launcher import plan, preview, run, LaunchError
from herdr_attention.linking import Observations, quota_signal, session_hash, validated_parent
from herdr_attention.hook import payload
from herdr_attention.store import Store
from herdr_attention.relations import Registry, RelationError
from tests.helpers import Clock, agent

ROOT = Path(__file__).resolve().parent.parent
PARENT = 'codex:aaaaaaaa-0000-4000-8000-000000000001'
CHILD = 'codex:aaaaaaaa-0000-4000-8000-000000000002'


class Linking(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.store = Store(':memory:')
        self.registry = Registry(self.store)
        self.obs = Observations(self.store, self.registry, self.clock)
        self.run_id = str(uuid.uuid4())
        self.body = {'run_id': self.run_id, 'kind': 'codex', 'parent_session': PARENT,
                     'parent_pane': 'w1:p1', 'role': 'tests', 'sandbox': 'fixture'}
        self.obs.launch(self.body)

    def tearDown(self):
        self.store.close()

    def test_registration_does_not_infer_reports_to_and_uses_caller_actor(self):
        self.obs.bind(self.run_id, CHILD)
        links = self.registry.state().links
        self.assertEqual(list(links), [(CHILD, 'spawned_by', PARENT)])
        self.assertEqual(next(iter(links.values()))['actor'], PARENT)
        self.assertEqual(self.registry.labels_for(CHILD)['sandbox'][0]['value'], 'fixture')
        with self.assertRaises(RelationError):
            self.obs.bind(self.run_id, PARENT)

    def test_hook_ingest_is_idempotent_for_edges_and_validates_run(self):
        hook = {'run_id': self.run_id, 'session': CHILD, 'parent_session': PARENT,
                'parent_pane': 'w1:p1', 'event': 'SubagentStart', 'child_session': 'codex:sub-child'}
        self.obs.hook(hook)
        self.obs.hook(hook)
        self.assertEqual(len(self.registry.state().links), 2)
        self.obs.hook({**hook, 'event': 'SubagentStop'})
        self.assertEqual(self.store._exec('SELECT status FROM hook_agents').fetchone()[0], 'done')
        with self.assertRaises(RelationError):
            self.obs.hook({**hook, 'parent_session': CHILD})
        with self.assertRaises(RelationError):
            self.obs.hook({**hook, 'run_id': str(uuid.uuid4())})

    def test_tags_ignore_replacement_and_parent_reuse(self):
        parent = agent('w1:p1', kind='codex', session=PARENT.split(':')[1])
        child = agent('w1:p2', kind='codex', session=CHILD.split(':')[1])
        child['tokens'] = {'parent': 'w1:p1', 'self': session_hash(CHILD), 'hat_parent_self': session_hash(PARENT)}
        self.assertEqual(validated_parent(child, [parent, child]), PARENT)
        replacement = copy.deepcopy(child)
        replacement['agent_session']['value'] = 'different'
        self.assertIsNone(validated_parent(replacement, [parent, replacement]))
        parent['agent_session']['value'] = 'reused'
        self.assertIsNone(validated_parent(child, [parent, child]))

    def test_quota_timeout_persistence_clear_and_no_poll_extension(self):
        self.obs.wall('codex', CHILD, timeout=60)
        self.clock.advance(30)
        self.obs.wall('codex', CHILD, timeout=60)
        self.clock.advance(31)
        self.assertEqual(self.obs.walls(), [])
        self.obs.wall('cursor', CHILD, premium_only=True, timeout=0)
        self.assertEqual(self.obs.walls()[0]['until'], None)
        self.obs.clear_wall('cursor')
        self.assertEqual(self.obs.walls(), [])

    def test_hook_keeps_only_identifiers(self):
        env = {'HAT_KIND': 'claude', 'HAT_RUN_ID': self.run_id, 'HERDR_PARENT_SESSION': PARENT,
               'HERDR_PARENT_PANE': 'w1:p1'}
        raw = {'session_id': 'session', 'agent_id': 'short-task', 'hook_event_name': 'SubagentStart',
               'last_assistant_message': 'secret text'}
        out = payload(raw, env)
        self.assertEqual(out['child_session'], 'claude:session/subagent/short-task')
        self.assertNotIn('secret', json.dumps(out))
        for text in ("You've hit your usage limit", 'Insufficient credits', 'Model is at capacity',
                     'rate_limit_exceeded', 'You have exceeded the premium request limit'):
            self.assertTrue(quota_signal(text), text)
        self.assertFalse(quota_signal('Build completed successfully'))


class Launch(unittest.TestCase):
    def steps(self, **changes):
        args = dict(name='fixture', kind='codex', env={'HERDR_ENV': '1', 'HERDR_PANE_ID': 'w1:p1',
                    'CODEX_HOME': '/do-not-inherit', 'HERDR_PARENT_SESSION': 'old'}, config_dir=None,
                    direction='right', cwd='/tmp', agent_args=[], caller=agent('w1:p1', kind='codex',
                    session=PARENT.split(':')[1]), selection={'model': 'chosen', 'effort': 'high', 'env': {}, 'account': 'codex-work'})
        args.update(changes)
        return plan(**args)

    def test_env_is_only_on_child_and_dry_run_hides_values(self):
        steps = self.steps()
        self.assertNotIn('--env', steps['split'])
        self.assertEqual(steps['child_env']['CODEX_HOME'], str(Path.home() / '.codex'))
        self.assertEqual(steps['child_env']['HERDR_PARENT_SESSION'], PARENT)
        self.assertNotEqual(self.steps()['run_id'], steps['run_id'])
        self.assertNotIn(str(Path.home() / '.codex'), json.dumps(preview(steps)))
        commands = []
        def runner(cmd):
            commands.append(cmd)
            return json.dumps({'result': {'pane': {'pane_id': 'w1:p2'}}})
        run(steps, name='fixture', register=lambda body: None,
            agent_info=lambda pane: agent(pane, kind='codex', session=(PARENT if pane == 'w1:p1' else CHILD).split(':')[1]),
            role='tests', team=None, task=None, reports_to=None, runner=runner)
        command = next(c for c in commands if c[1:3] == ['pane', 'run'])[-1]
        tokens = shlex.split(command)
        self.assertIn('HERDR_PARENT_SESSION=' + PARENT, tokens)
        self.assertIn('--yolo', tokens)
        self.assertTrue(any(t.startswith('hooks.SubagentStart=') for t in tokens))
        self.assertFalse(any('send-keys' in c or 'prompt' in c for c in commands))

    def test_sandbox_opt_out_and_coordinator_pointer(self):
        steps = self.steps(sandbox='isolated fixture', role='coordinator')
        self.assertNotIn('--yolo', steps['args'])
        self.assertIn('--sandbox', steps['args'])
        self.assertIn('spawn workers with herdr-spawn', steps['args'][-1])
        with self.assertRaises(LaunchError):
            self.steps(sandbox='')
        with self.assertRaises(LaunchError):
            self.steps(agent_args=['--model', 'bypass'])

class CollectorLinking(unittest.TestCase):
    def setUp(self):
        from herdr_attention.collector import Collector
        from tests.helpers import FakeHerdr, settings
        self.parent = agent('w1:p1', kind='codex', session=PARENT.split(':')[1])
        self.child = agent('w1:p2', kind='codex', session=CHILD.split(':')[1])
        self.child['tokens'] = {'parent': 'w1:p1', 'self': session_hash(CHILD), 'hat_parent_self': session_hash(PARENT)}
        self.clock = Clock()
        self.collector = Collector(db_path=':memory:', settings=settings(),
                                   client=FakeHerdr([self.parent, self.child]), clock=self.clock)
        self.collector.handle_snapshot()

    def tearDown(self):
        self.collector.store.close()

    def test_tags_to_registry_and_stale_replacement(self):
        links = self.collector.view(local=False)['links']
        self.assertTrue(any(l['from'] == CHILD and l['to'] == PARENT for l in links))
        self.assertFalse(any(l['kind'] == 'reports_to' for l in links))
        self.child['agent_session']['value'] = 'replacement'
        self.collector.client.set(self.child)
        self.collector.handle_snapshot()
        self.assertFalse(any(l['from'] == 'codex:replacement' for l in self.collector.view(local=False)['links']))

    def test_virtual_hook_agent_appears_and_hides_with_workspace(self):
        obs = self.collector.observations
        run_id = str(uuid.uuid4())
        obs.launch({'run_id': run_id, 'kind': 'codex', 'parent_session': PARENT, 'parent_pane': 'w1:p1'})
        self.collector.hook_event({'run_id': run_id, 'session': CHILD, 'parent_session': PARENT,
                                  'parent_pane': 'w1:p1', 'event': 'SubagentStart', 'child_session': 'codex:hook-child'})
        view = self.collector.view(local=False)
        self.assertEqual(len(view['agents']), 3)
        self.assertTrue(any(l['from'] == 'codex:hook-child' and l['to'] == CHILD for l in view['links']))
        self.assertIsNotNone(self.collector.agent_detail('codex:hook-child'))
        self.collector.settings.set_override('watch.enabled_workspaces', [])
        self.collector.handle_snapshot()
        self.assertEqual(self.collector.view(local=False)['agents'], [])

    def test_idle_agent_quota_detected_without_transcript_or_restart(self):
        self.collector.client.outputs['w1:p2'] = "You've hit your usage limit"
        self.collector.poll_output()
        walls = self.collector.observations.walls()
        self.assertEqual(walls[0]['provider'], 'codex')
        self.assertTrue(next(a for a in self.collector.view(local=False)['agents'] if a['identity'] == CHILD)['quota_wall'])
        self.assertFalse(any(c[0] in ('prompt', 'start') for c in self.collector.client.calls))


class SidebarTests(unittest.TestCase):
    def test_forest_has_children_roles_and_refuses_steering(self):
        from herdr_attention.sidebar import placements, ProjectionClient
        from herdr_attention.herdr import HerdrError
        cards = [{'identity': c, 'pane_id': f'w1:p{i}', 'present': True} for i, c in enumerate(('parent', 'child', 'ambiguous'))]
        view = {'agents': cards, 'links': [
            {'from': 'child', 'to': 'parent', 'kind': 'spawned_by'},
            {'from': 'ambiguous', 'to': 'parent', 'kind': 'spawned_by'},
            {'from': 'ambiguous', 'to': 'child', 'kind': 'spawned_by'}]}
        self.assertEqual([(c['identity'], d) for c, d in placements(view)], [('parent', 0), ('child', 1), ('ambiguous', 0)])
        with self.assertRaises(HerdrError):
            ProjectionClient('/does/not/exist').call('agent.prompt', {})

    def test_projection_does_not_take_a_foreign_view(self):
        from herdr_attention.sidebar import Sidebar
        class Client:
            def __init__(self): self.calls = []
            def call(self, method, params):
                self.calls.append(method)
                return {'active': True, 'source': 'plugin:other'}
        client = Client()
        sidebar = Sidebar(client=client)
        sidebar.update({'agents': [], 'links': []})
        self.assertEqual(client.calls, ['agent.view.clear'])
        self.assertTrue(sidebar.passive)

    def test_state_changes_do_not_change_sidebar_order_but_web_still_changes(self):
        from herdr_attention.sidebar import placements
        from tests.test_engine import make
        from tests.helpers import workspaces
        engine, store, _ = make()
        self.addCleanup(store.close)
        early = agent('w1:p1', 'working', session='early', name='Z early')
        late = agent('w1:p2', 'done', session='late', name='A late')
        early['pane_order'], late['pane_order'] = 1, 2
        engine.set_tabs([{'tab_id':'w1:t1','workspace_id':'w1','number':1}])
        engine.apply_snapshot([late, early], workspaces('w1'))
        before = engine.view()
        early['agent_status'], late['agent_status'] = 'done', 'working'
        early['name'], late['name'] = 'A renamed', 'Z renamed'
        engine.apply_snapshot([early, late])
        after = engine.view()
        self.assertNotEqual([a['identity'] for a in before['agents']], [a['identity'] for a in after['agents']])
        expected = [('claude:early',0),('claude:late',0)]
        for view in (before,after):
            self.assertEqual([(a['identity'],depth) for a,depth in placements(view)], expected)

    def test_new_root_is_inserted_by_position(self):
        from herdr_attention.sidebar import placements
        cards = [dict(identity=i,pane_id=f'w1:p{i}',present=True,workspace_order=1,tab_order=1,pane_order=n)
                 for i,n in (('first',1),('last',3))]
        view = {'agents':cards[::-1],'links':[]}
        self.assertEqual([a['identity'] for a,_ in placements(view)], ['first','last'])
        view['agents'].insert(0,dict(identity='new',pane_id='w1:pnew',present=True,workspace_order=1,tab_order=1,pane_order=2,state='needs_you'))
        self.assertEqual([a['identity'] for a,_ in placements(view)], ['first','new','last'])

    def test_tree_depth_and_sibling_position_survive_state_changes(self):
        from herdr_attention.sidebar import placements
        cards = [dict(identity=i,pane_id=f'w1:p{i}',present=True,workspace_order=1,tab_order=1,pane_order=n)
                 for i,n in (('lead',4),('older',1),('younger',3),('root',5))]
        view = {'agents':cards,'links':[{'from':child,'to':'lead','kind':'spawned_by'} for child in ('older','younger')]}
        expected = [('lead',0),('older',1),('younger',1),('root',0)]
        self.assertEqual([(a['identity'],d) for a,d in placements(view)], expected)
        for card in cards:
            card.update(state='done',acked=True,last_activity_at=123,name='Renamed')
        view['agents'] = cards[::-1]
        self.assertEqual([(a['identity'],d) for a,d in placements(view)], expected)
        view['agents'].append(dict(identity='new',pane_id='w1:pnew',present=True,workspace_order=1,tab_order=1,pane_order=2,state='needs_you'))
        view['links'].append({'from':'new','to':'lead','kind':'spawned_by'})
        self.assertEqual([(a['identity'],d) for a,d in placements(view)], [('lead',0),('older',1),('new',1),('younger',1),('root',0)])

    def test_workspace_tab_and_pane_positions_take_precedence_over_ids(self):
        from herdr_attention.sidebar import placements
        cards = [dict(identity=i,pane_id=p,present=True,workspace_order=w,tab_order=t,pane_order=n)
                 for i,p,w,t,n in [('other-workspace','w1:p0',2,0,0),('other-tab','w1:p1',1,2,0),
                                  ('later-pane','w1:p2',1,1,2),('first','w1:p9',1,1,0)]]
        self.assertEqual([a['identity'] for a,_ in placements({'agents':cards,'links':[]})],
                         ['first','later-pane','other-tab','other-workspace'])

    def test_missing_native_positions_use_numbers_then_stable_ids(self):
        from herdr_attention.sidebar import placements
        cards = [dict(identity=i,pane_id=p,present=True,**fields) for i,p,fields in [
            ('unknown','w9:p1',{}),
            ('tab','w1:p1',{'workspace_number':1,'tab_number':2}),
            ('pane-b','w1:pB',{'workspace_order':None,'workspace_number':1,'tab_number':1}),
            ('pane-a','w1:pA',{'workspace_order':'bad','workspace_number':1,'tab_number':1}),
            ('numbered','w1:pZ',{'workspace_number':1,'tab_number':1,'pane_number':1}),
            ('native','w1:pY',{'workspace_order':0,'workspace_number':9}),
        ]]
        expected = ['native','numbered','pane-a','pane-b','tab','unknown']
        for rows in (cards,cards[::-1]):
            self.assertEqual([a['identity'] for a,_ in placements({'agents':rows,'links':[]})],expected)
        # Identity breaks a remaining pane-id tie; names never determine the order.
        tied = [dict(identity=i,pane_id='w1:p1',present=True,name=name) for i,name in [('z','A'),('a','Z')]]
        self.assertEqual([a['identity'] for a,_ in placements({'agents':tied,'links':[]})],['a','z'])

    def test_cycle_and_ambiguous_parentage_remain_unindented(self):
        from herdr_attention.sidebar import placements
        cards = [dict(identity=i,pane_id=f'w1:p{i}',present=True) for i in ('c','b','a')]
        links = [{'from':'a','to':'b','kind':'spawned_by'},{'from':'b','to':'a','kind':'spawned_by'},
                 {'from':'c','to':'a','kind':'spawned_by'},{'from':'c','to':'b','kind':'spawned_by'}]
        self.assertEqual([(a['identity'],d) for a,d in placements({'agents':cards,'links':links})], [('a',0),('b',0),('c',0)])

    def test_view_is_owned_once_and_metadata_keeps_refreshing_roles_and_ttl(self):
        from herdr_attention.sidebar import Sidebar, SOURCE
        from herdr_attention.engine import identity_of
        infos = [agent('w1:p1',session='lead'),agent('w1:p2',session='worker')]
        class Client:
            def __init__(self): self.calls=[];self.owned=False
            def agent(self,pane):return next(info for info in infos if info['pane_id']==pane)
            def call(self,method,params):
                self.calls.append((method,copy.deepcopy(params)))
                if method=='agent.view.set':self.owned=True
                return {'active':self.owned,'source':SOURCE if self.owned else None}
        client=Client();sidebar=Sidebar(client=client)
        cards=[dict(identity=identity_of(info),pane_id=info['pane_id'],agent='claude',present=True,
                    roles={'role':[{'value':role}]}) for info,role in zip(infos,('coordinator','worker'))]
        view={'agents':cards,'links':[{'from':cards[1]['identity'],'to':cards[0]['identity'],'kind':'spawned_by'}]}
        sidebar.update(view)
        first=[p for method,p in client.calls if method=='pane.report_metadata']
        view['agents'].reverse()
        for card in cards:card['state']='done'
        sidebar.update(view)
        self.assertEqual(sum(method=='agent.view.set' for method,_ in client.calls),1)
        writes=[p for method,p in client.calls if method=='pane.report_metadata']
        self.assertEqual(writes,first+first)
        self.assertEqual([p['tokens']['hat_tree_rank'] for p in first],['000000','000001'])
        self.assertEqual([p['tokens']['hat_tree_branch'] for p in first],['[coordinator] ','  └─ [worker] '])
        self.assertTrue(all(p['ttl_ms']==10000 for p in writes))
        sidebar.clear()
        self.assertFalse(sidebar.owned)
        self.assertEqual(sidebar.last,{})


class HookSessionPublication(unittest.TestCase):
    def test_only_the_launches_current_native_pane_gets_a_session_report(self):
        from herdr_attention.hook import report_session
        env = {'HERDR_PANE_ID': 'w1:p2', 'HAT_KIND': 'codex'}
        body = {'event': 'SessionStart', 'session': CHILD}
        with patch('herdr_attention.hook.HerdrClient') as herdr, patch('herdr_attention.hook.subprocess.run') as command:
            herdr.return_value.agent.return_value = {'agent': 'codex'}
            report_session(body, {}, env)
            self.assertEqual(command.call_args.args[0], ['herdr', 'pane', 'report-agent-session', 'w1:p2',
                             '--source', 'herdr:codex', '--agent', 'codex', '--agent-session-id', CHILD.split(':')[1]])
            command.reset_mock()
            herdr.return_value.agent.return_value = {'agent': 'codex', 'agent_session': {'agent': 'codex', 'value': 'replacement'}}
            report_session(body, {}, env)
            command.assert_not_called()
            herdr.return_value.agent.return_value = {'agent': 'codex'}
            report_session(body, {'agent_id': 'nested'}, env)
            command.assert_not_called()

    def test_sidebar_cannot_decorate_when_native_plugin_is_unregistered(self):
        from herdr_attention.sidebar import Sidebar
        from herdr_attention.herdr import HerdrError
        class Client:
            def __init__(self): self.calls=[]
            def call(self, method, params):
                self.calls.append(method)
                if method=='agent.view.clear': return {'active':False}
                raise HerdrError('plugin not found', code='plugin_not_found')
        client=Client()
        with self.assertRaises(HerdrError):
            Sidebar(client=client).update({'agents':[{'identity':CHILD,'pane_id':'w1:p2','present':True}], 'links':[]})
        self.assertEqual(client.calls,['agent.view.clear','agent.view.set'])
