"""Empty workspace grouping and pruning of gone nodes in real Chromium."""
import json
import unittest
from tests.helpers import agent
from tests.trace_fixtures import sid
from tests.web_fixture import build
from tests.web_harness import BROWSER, run


@unittest.skipUnless(BROWSER, 'no Chromium found')
class EmptyWorkspaces(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data=build()
        c=data.pop('collector')
        c.client.ws += [{'workspace_id':w,'label':label,'number':n} for n,w,label in
                        [(5,'w5','Old shell one'),(6,'w6','Old shell two'),(8,'w8','Old shell three')]]
        c.settings.set_override('watch.enabled_workspaces',['w1','w2','w3','w5','w6','w8'])
        ghost=agent('w5:p1','working',session=sid('ghost'),name='old-reboot-agent',kind='codex')
        cls.ghost='codex:'+sid('ghost')
        c.client.set(ghost);c.handle_snapshot()
        c.relation_action({'action':'link','subject':cls.ghost,'kind':'reports_to','object':data['ids']['lead']},local=True)
        cls.before=c.view(local=False)
        c.client.remove('w5:p1');c.handle_snapshot()
        data['view']=c.view(local=False)
        data['details'][cls.ghost]=c.agent_detail(cls.ghost)
        cls.after=data['view']
        cls.data=data
        assert next(w for w in cls.after['workspaces'] if w['id']=='w5')['live_agent_count']==0
        assert any(a['identity']==cls.ghost and not a['present'] for a in cls.after['agents'])
        c.store.close()

    def test_phone_and_desktop_list_only_have_full_cards_for_live_workspaces(self):
        for width in (390,1280):
            page=run(self.data,'/list',width,900,script="""
              const group=document.querySelector('.empty-workspaces');
              document.body.dataset.emptyCheck=JSON.stringify({cards:document.querySelectorAll('.grid.two > a.ws, .grid.two > .ws-item > a.ws').length,
                count:group.querySelector('summary').textContent,open:group.open});
            """)
            raw=page['dom'].split('data-empty-check="',1)[1].split('"',1)[0]
            result=json.loads(raw.replace('&quot;','"'))
            self.assertEqual(result,{'cards':3,'count':'3 workspaces without agents','open':False})
            self.assertEqual(page['probe']['overflowing'],[])

    def test_graph_prunes_gone_nodes_and_regions_and_preserves_group_expansion(self):
        for width in (390,1280):
            data={**self.data,'view':self.before,'after':self.after,'ghost':self.ghost}
            page=run(data,'/g',width,900,script="""
              const before=!!graph.nodes[FIXTURE.ghost];
              const inTop=!!document.querySelector('.graph-top .empty-workspaces, .graph-toolbar .empty-workspaces');
              graph.panel='workspaces';graphRefresh(true);
              const group=document.querySelector('.sheet .empty-workspaces');group.open=true;
              FIXTURE.view=FIXTURE.after;await refresh();render(true);
              const after=document.querySelector('.sheet .empty-workspaces');
              document.body.dataset.graphCheck=JSON.stringify({before,inTop,gone:!graph.nodes[FIXTURE.ghost],
                emptyRegion:!!graph.regions.w5,ghostEndpoint:Object.values(graph.hiddenEnds).some(p=>(p.label||'').startsWith('gone:')),
                count:after.querySelector('summary').textContent,open:after.open,same:group===after});
            """)
            raw=page['dom'].split('data-graph-check="',1)[1].split('"',1)[0]
            result=json.loads(raw.replace('&quot;','"'))
            self.assertTrue(result['before'])
            self.assertFalse(result['inTop'])   # the note lives in the Workspaces sheet now
            self.assertTrue(result['gone'])
            self.assertFalse(result['emptyRegion'])
            self.assertFalse(result['ghostEndpoint'])
            self.assertEqual(result['count'],'3 workspaces without agents')
            self.assertTrue(result['open'])
            self.assertTrue(result['same'])
            self.assertEqual(page['probe']['overflowing'],[])

    def test_gone_agent_history_is_still_accessible(self):
        from urllib.parse import quote
        page=run(self.data,'/a/'+quote(self.ghost,safe=''),390,900)
        self.assertIn('This agent is no longer running',page['dom'])
