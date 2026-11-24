"""Busy phone graph: title, label, dot and toolbar geometry at the reported size."""
import copy
import unittest
from pathlib import Path

from tests.test_typing_survives_refresh import reading
from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER, run


def busy_fixture():
    data = page_fixture()
    view = data['view']
    agents = view['agents']
    ws = view['workspaces'][0]
    names = ['Release PR review', 'HERDR-DASH-DESKTOP-WINDOW', 'HERDR-DASH-UIUX-REVIEW', 'Routing and accounts']
    view['workspaces'] = []
    view['agents'] = []
    view['links'] = []
    for i, name in enumerate(names):
        region = copy.deepcopy(ws)
        region.update(id=f'busy-{i}', label=name, agent_count=4, attention=1, live_agent_count=4)
        view['workspaces'].append(region)
        for j in range(4):
            node = copy.deepcopy(agents[j % len(agents)])
            node.update(identity=f'busy-{i}-{j}', workspace_id=region['id'], workspace_label=name,
                        name=['claude-app-prs', 'coord-astra-recovery', 'reviewer-routing-long', 'thermal-port'][j],
                        present=True, tier='waiting_on_you' if (i, j) == (0, 0) else None,
                        kind='approval' if (i, j) == (0, 0) else 'working',
                        state='needs_you' if (i, j) == (0, 0) else 'working',
                        status='blocked' if (i, j) == (0, 0) else 'working', acked=False,
                        roles={'role': [{'value': 'coordinator' if j == 0 else 'worker'}]})
            view['agents'].append(node)
    return data


GEOMETRY = """
await document.fonts.ready;
graph.autoFit = true; graph.userMoved = false; autoFit();
await new Promise(r => setTimeout(r, 100));
const rect = e => e.getBoundingClientRect();
const overlap = (a,b) => a.left < b.right - .5 && a.right > b.left + .5 && a.top < b.bottom - .5 && a.bottom > b.top + .5;
const all = selector => [...document.querySelectorAll(selector)];
const labels = all('svg.graph .node-label, svg.graph .role-label, svg.graph .region-label').filter(e => e.textContent && rect(e).height && getComputedStyle(e).display !== 'none');
const dots = all('svg.graph .node .body');
const collisions = [];
labels.forEach((a,i) => {
  labels.slice(i+1).forEach(b => { if (overlap(rect(a),rect(b))) collisions.push([a.textContent,b.textContent]); });
  dots.forEach(b => { if (overlap(rect(a),rect(b))) collisions.push([a.textContent,'dot']); });
});
const circles = all('svg.graph circle.region').map(e => ({x:+e.getAttribute('cx'), y:+e.getAttribute('cy'), r:+e.getAttribute('r')}));
const bubbles = circles.some((a,i) => circles.slice(i+1).some(b => Math.hypot(a.x-b.x,a.y-b.y) < a.r+b.r));
const toolbar = rect(document.querySelector('.graph-toolbar'));
const header = document.querySelector('.sh-fresh') || document.querySelector('.sh-sub');
document.body.dataset.reading = JSON.stringify({collisions, bubbles,
  labels: labels.length, roles: labels.filter(e => e.classList.contains('role-label')).length,
  workerRoles: labels.filter(e => e.classList.contains('role-label') && /^worker$/i.test(e.textContent)).length,
  minDot: Math.min(...dots.map(e => rect(e).width)),
  // Phone: the toolbar sits at the bottom (thumb reach); no dot may hide under it or the top block.
  toolbarClear: dots.every(e => !overlap(rect(e), toolbar) && rect(e).top > rect(document.querySelector('.graph-top')).bottom),
  toolbarAtBottom: toolbar.top > window.innerHeight / 2,
  topChrome: rect(document.querySelector('.graph-top')).bottom,
  summaryFits: header.scrollWidth <= header.clientWidth + 1,
  duplicateCount: !!document.querySelector('.sh-count'),
  onTop: document.querySelector('.viewport > g:last-child > g:last-child').classList.contains('s-needs_you')});
"""


@unittest.skipUnless(BROWSER, 'no Chromium found')
class BusyPhoneGraph(unittest.TestCase):
    def test_busy_layout_has_no_label_or_region_collisions(self):
        r = reading(run(busy_fixture(), '', 412, 915, script=GEOMETRY))
        self.assertEqual(r['collisions'], [])
        self.assertFalse(r['bubbles'])
        self.assertGreater(r['labels'], 5)
        # v0.9.5: the default "worker" role is not repeated on every node; the coordinators here
        # have no free slot next to them at this fit (names and roles yield the same way).
        self.assertEqual(r['workerRoles'], 0)
        self.assertGreaterEqual(r['minDot'], 14.5)
        self.assertTrue(r['toolbarClear'])
        self.assertTrue(r['toolbarAtBottom'])
        self.assertTrue(r['summaryFits'])
        self.assertFalse(r['duplicateCount'])
        self.assertTrue(r['onTop'])

    def test_calm_phone_canvas_starts_near_the_top(self):
        # Folds and empty workspaces live in the sheets now, not above the canvas.
        data = busy_fixture()
        view = data['view']
        for a in view['agents']:
            a.update(state='working', status='working', kind='working', tier=None, question=None, awaiting_user=False)
        view['questions'] = []
        view['collector']['herdr']['last_reconcile'] = view.get('generated_at', 0)
        r = reading(run(data, '', 390, 844, script=GEOMETRY))
        self.assertLessEqual(r['topChrome'], 110)
        self.assertTrue(r['toolbarAtBottom'])

    def test_names_roles_titles_and_selected_edges_clear_dots_at_every_zoom(self):
        data=busy_fixture()
        data['view']['notify_tiers']['watch_closely']=['busy-0-0']
        data['view']['links']=[{'from':'busy-0-0','to':'busy-0-1','kind':'reports_to','provenance':['manual']}]
        r=reading(run(data,'/g/busy-0-0',412,915,script="""
          const results=[];
          const overlap=(a,b)=>a.left<b.right-.5 && a.right>b.left+.5 && a.top<b.bottom-.5 && a.bottom>b.top+.5;
          for(const k of [.2,.3,.6,1,2,4]) {
            setTransform({k,x:40,y:100});
            const labels=[...document.querySelectorAll('svg.graph .node-label,svg.graph .role-label,svg.graph .region-label,svg.graph .edge-label')]
              .filter(e=>e.textContent && getComputedStyle(e).display!=='none');
            const dots=[...document.querySelectorAll('svg.graph .body')];
            let collisions=0;
            labels.forEach((a,i)=>{
              const rect=a.getBoundingClientRect();
              labels.slice(i+1).forEach(b=>{if(overlap(rect,b.getBoundingClientRect()))collisions++});
              dots.forEach(b=>{if(overlap(rect,b.getBoundingClientRect()))collisions++});
            });
            results.push({k,collisions});
          }
          document.body.dataset.reading=JSON.stringify(results);
        """))
        self.assertTrue(all(x['collisions']==0 for x in r),r)

    def test_waiting_on_you_paints_above_a_different_selected_node(self):
        r=reading(run(busy_fixture(),'/g/busy-0-1',412,915,script="""
          document.body.dataset.reading=JSON.stringify({
            urgent:graph.layers.nodes.lastElementChild.classList.contains('s-needs_you'),
            selected:graph.selected});
        """))
        self.assertTrue(r['urgent'])
        self.assertEqual(r['selected'],'busy-0-1')


if __name__ == '__main__':
    unittest.main()
