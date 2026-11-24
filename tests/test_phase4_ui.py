"""Phase 4: drill-in navigation, visible facts, edges and touch selection."""
import unittest
import urllib.parse
from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER, run
from tests.test_typing_survives_refresh import reading


@unittest.skipUnless(BROWSER, 'no Chromium found')
class Phase4(unittest.TestCase):
    def test_workspace_graph_is_scoped_and_main_viewport_is_independent(self):
        r = reading(run(page_fixture(), '/list', 412, 915, script="""
          const wait = () => new Promise(r => setTimeout(r, 120));
          const main = graph, before = JSON.stringify(graph.transform);
          location.hash = '#/w/w1'; await wait();
          const ids = [...document.querySelectorAll('.workspace-graph .node')].map(n=>n.dataset.id);
          const expected = FIXTURE.view.agents.filter(a=>a.present && a.workspace_id==='w1').map(a=>a.identity);
          const back = document.querySelector('.workspace-nav .back').getAttribute('href');
          const isolated = graph === main && JSON.stringify(graph.transform) === before;
          document.querySelector('.workspace-nav .btn').click(); await wait();
          document.body.dataset.reading = JSON.stringify({ids: ids.sort(), expected:expected.sort(), back, isolated,
            opened:route().name==='graph', centered:!!graph.transform && graph.userMoved});
        """))
        self.assertEqual(r['ids'], r['expected'])
        self.assertEqual(r['back'], '#/list')
        for key in ('isolated', 'opened', 'centered'):
            self.assertTrue(r[key], key)

    def test_selected_edges_have_readable_labels_and_keep_distinct_styles(self):
        data = page_fixture()
        selected = data['ids']['ui']
        other = next(a['identity'] for a in data['view']['agents'] if a['identity'] != selected and a['present'])
        data['view']['links'] = [
            {'from': selected, 'to': other, 'kind': 'reports_to', 'inferred': True, 'confidence': .7, 'provenance': ['inferred']},
            {'from': other, 'to': selected, 'kind': 'spawned_by', 'provenance': ['observed-spawn']},
        ]
        r = reading(run(data, '/g/' + urllib.parse.quote(selected, safe=''), 412, 915, script="""
          document.body.dataset.reading=JSON.stringify({
            labels:[...document.querySelectorAll('.edge-label')].map(e=>e.textContent),
            styles:[...document.querySelectorAll('.edge.active')].map(e=>getComputedStyle(e).strokeDasharray),
            size:Math.min(...[...document.querySelectorAll('.edge-label')].map(e=>e.getBoundingClientRect().height))});
        """))
        self.assertIn('reports to · inferred 70%', r['labels'])
        self.assertIn('spawned', r['labels'])
        self.assertEqual(len(set(r['styles'])), 2)
        self.assertGreaterEqual(r['size'], 11.5)

    def test_agent_facts_recent_history_actions_and_approval_hint_are_visible(self):
        data = page_fixture()
        r = reading(run(data, '/a/' + urllib.parse.quote(data['ids']['ui'], safe=''), 412, 915, script="""
          const main = document.querySelector('#app');
          document.body.dataset.reading = JSON.stringify({
            stats: !!main.querySelector('.stat-row:not(details *)'),
            recent: main.querySelectorAll('.recent-timeline .timeline > li').length,
            primary: main.querySelectorAll('.summary .btn.primary').length,
            menu: !!main.querySelector('.summary-menu'),
            watch: !!main.querySelector('.detail-head .watch-toggle[aria-pressed]'),
            hint: main.querySelector('.terminal-hint')?.textContent,
            reply: [...main.querySelectorAll('.summary button')].some(b=>b.textContent==='Reply'),
            folded: !main.querySelector('details.more').open});
        """))
        for key in ('stats', 'menu', 'watch', 'folded'):
            self.assertTrue(r[key], key)
        self.assertEqual(r['recent'], min(3, len(data['details'][data['ids']['ui']]['activity'])))
        self.assertEqual(r['primary'], 1)
        self.assertTrue(r['reply'], 'Reply remains findable at approval screens')

    def test_actual_transcript_selection_stays_docked_through_scroll_and_poll(self):
        data = page_fixture()
        route = '/a/' + urllib.parse.quote(data['ids']['ui'], safe='') + '/conversation'
        r = reading(run(data, route, 412, 915, script="""
          const wait=()=>new Promise(r=>setTimeout(r,100));
          const host=document.querySelector('.msg-text');
          const node=host.firstChild;
          const range=document.createRange();range.setStart(node,0);range.setEnd(node,Math.min(20,node.length));
          const sel=window.getSelection();sel.removeAllRanges();sel.addRange(range);await wait();
          const text=String(sel);
          document.dispatchEvent(new Event('scroll'));await wait();
          const bar=document.querySelector('#select-bar');
          const docked=!bar.hidden && getComputedStyle(bar).position==='fixed';
          FIXTURE.view.version++;await refresh();render(true);await wait();
          const kept=String(sel)===text && sel.anchorNode===node;
          bar.querySelector('button').click();
          const quote=app.composer.quote;
          closeComposer();await wait();
          document.body.dataset.reading=JSON.stringify({docked,kept,text,quote,collapsed:sel.isCollapsed,hidden:bar.hidden});
        """))
        for key in ('docked', 'kept', 'collapsed', 'hidden'):
            self.assertTrue(r[key], key)
        self.assertEqual(r['quote'], r['text'])
