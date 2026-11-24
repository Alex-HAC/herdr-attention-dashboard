"""The shipped graph folds finished hook children and explains live completion rows."""
import copy
import unittest
from urllib.parse import quote

from herdr_attention.content import decorate_subagents, read_action
from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER, run
from tests.test_typing_survives_refresh import reading


def fold_fixture():
    data=page_fixture();view=data['view']
    parent=next(a for a in view['agents'] if a['identity']==data['ids']['ui'])
    for index,status in enumerate(('done','done','working')):
        child=copy.deepcopy(parent)
        child.update(identity=f'claude:fold-{index}',name=f'child-{index}',pane_id=None,terminal_id=None,
                     in_process=True,in_process_parent=parent['identity'],status=status,state=status,kind=status,
                     event_id=None,tier=None,summary=None,question=None,acked=False,metrics={})
        view['agents'].append(child)
        view['links'].append({'from':child['identity'],'to':parent['identity'],'kind':'spawned_by','provenance':['observed-spawn']})
    decorate_subagents(view)
    return data


@unittest.skipUnless(BROWSER,'no Chromium')
class FoldReadUi(unittest.TestCase):
    def test_finished_fold_expands_nodes_and_preserves_open_state_on_poll(self):
        data=fold_fixture()
        r=reading(run(data,'/',390,844,script="""
          const out={};
          out.inTop=!!document.querySelector('.graph-top .subagent-fold');
          graph.panel='view';graphRefresh(true);
          const fold=document.querySelector('.sheet .graph-subagent-folds details');
          out.label=fold.querySelector('summary').textContent;
          out.defaultNodes=[...document.querySelectorAll('.node')].map(n=>n.dataset.id);
          fold.open=true;await new Promise(r=>setTimeout(r,180));
          out.expandedNodes=[...document.querySelectorAll('.node')].map(n=>n.dataset.id);
          FIXTURE.view.version++;await refresh(true);
          out.kept=document.querySelector('.sheet .graph-subagent-folds details')===fold && fold.open;
          out.individuals=fold.querySelectorAll('li a').length;
          fold.open=false;await new Promise(r=>setTimeout(r,180));
          out.collapsedNodes=[...document.querySelectorAll('.node')].map(n=>n.dataset.id);
          document.body.dataset.reading=JSON.stringify(out);
        """))
        self.assertFalse(r['inTop'])   # folds live in the View sheet, not above the canvas
        self.assertIn('Finished subagents · 2',r['label'])
        self.assertIn('claude:fold-2',r['defaultNodes'])
        self.assertNotIn('claude:fold-0',r['defaultNodes'])
        for id in ('claude:fold-0','claude:fold-1','claude:fold-2'):self.assertIn(id,r['expandedNodes'])
        self.assertTrue(r['kept'])
        self.assertEqual(r['individuals'],2)
        self.assertNotIn('claude:fold-0',r['collapsedNodes'])

    def test_graph_hide_subagents_filter_removes_live_and_finished_nodes_and_edges(self):
        r=reading(run(fold_fixture(),'/',390,844,script="""
          graph.panel='view';graphRefresh(true);
          const checkbox=document.querySelector('input[aria-label="Hide subagents"]');
          checkbox.click();await new Promise(r=>setTimeout(r,100));
          const hidden={nodes:[...document.querySelectorAll('.node')].map(n=>n.dataset.id),
            edges:Object.keys(graph.els.edges)};
          FIXTURE.view.version++;await refresh(true);
          const kept=graph.hideSubagents;
          document.querySelector('input[aria-label="Hide subagents"]').click();await new Promise(r=>setTimeout(r,100));
          document.body.dataset.reading=JSON.stringify({hidden,kept,back:!!document.querySelector('.node[data-id="claude:fold-2"]')});
        """))
        self.assertFalse(any('claude:fold-' in id for id in r['hidden']['nodes']))
        self.assertFalse(any('claude:fold-' in id for id in r['hidden']['edges']))
        self.assertTrue(r['kept'])
        self.assertTrue(r['back'])

    def test_overview_and_parent_retain_fold_links_without_individual_stack(self):
        data=fold_fixture()
        parent=data['ids']['ui']
        for route in ('/list','/a/'+quote(parent,safe='')):
            with self.subTest(route=route):
                r=reading(run(data,route,390,844,script="""
                  const fold=document.querySelector('details.subagent-fold');fold.open=true;
                  document.body.dataset.reading=JSON.stringify({label:fold.querySelector('summary').textContent,
                    links:[...fold.querySelectorAll('a')].map(a=>a.textContent),
                    individualCards:document.querySelectorAll('.agent-with-focus a[href*="fold-0"], a.agent[href*="fold-0"]').length});
                """))
                self.assertIn('Finished subagents · 2',r['label'])
                self.assertEqual(r['links'],['child-0','child-1'])
                self.assertEqual(r['individualCards'],0)

    def test_live_completion_has_hint_instead_of_read_after_id_regeneration(self):
        data=page_fixture()
        agent=next(a for a in data['view']['agents'] if a['identity']==data['ids']['ui'])
        agent.update(status='done',kind='completion',tier='waiting_on_coordinator',question=None,acked=False)
        agent['read_action']=read_action(agent)
        data['target']=agent['identity']
        r=reading(run(data,'/list',390,844,script="""
          const findRow=()=>[...document.querySelectorAll('.attention-list li')].find(li=>li.dataset.readingKey.includes(FIXTURE.target));
          const first=findRow(), before=!!first.querySelector('.notif-mark');
          const hint=(first.querySelector('.read-state-hint')||first.closest('.attention-group').querySelector('.section-hint')).textContent;  // 0.9.8.6: once per section when shared
          FIXTURE.view.agents.find(a=>a.identity===FIXTURE.target).event_id=2428;FIXTURE.view.version++;
          await refresh(true);
          document.body.dataset.reading=JSON.stringify({before,after:!!findRow().querySelector('.notif-mark'),hint});
        """))
        self.assertFalse(r['before'])
        self.assertFalse(r['after'])
        self.assertIn('Clears when you open this pane on the laptop',r['hint'])  # phone listener fixture
