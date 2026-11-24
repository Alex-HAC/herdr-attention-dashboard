"""Graph-first shell: gestures, stability under refresh, layouts. FIXTURE-BASED.

Scripted pointer/touch events on the real page in headless Chromium.
Pointer capture is stubbed in the page because Chromium refuses capture for
synthetic second fingers; the gesture logic under test is unchanged.

The pinch test reproduces the reported bug: on v0.5.0 the zoom went from
1.91 back to 0.64 when one finger lifted and the other moved (it reused the
transform from before the pinch). See docs/design.md.
"""

import json
import unittest
import urllib.parse

from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER, run

PRELUDE = """
const svg = document.querySelector('svg.graph');
svg.setPointerCapture = () => {}; svg.releasePointerCapture = () => {}; svg.hasPointerCapture = () => false;
const R = () => svg.getBoundingClientRect();
// Like a browser: pointerdown hits the element under the finger; later events
// go where capture sends them (the svg).
const ev = (type, id, x, y, kind = 'touch') => {
  const cx = R().left + x, cy = R().top + y;
  const hit = type === 'pointerdown' ? document.elementFromPoint(cx, cy) : null;
  const target = hit && svg.contains(hit) ? hit : svg;
  target.dispatchEvent(new PointerEvent(type, {bubbles: true, cancelable: true, pointerId: id, pointerType: kind,
    isPrimary: id === 11 || id === 1, clientX: cx, clientY: cy}));
};
const T = () => ({...graph.transform});
const wait = (ms) => new Promise(res => setTimeout(res, ms));
const worldAt = (x, y) => { const t = T(); return {x: (x - t.x) / t.k, y: (y - t.y) / t.k}; };
const screenOf = (id) => { const t = T(), n = graph.nodes[id], g = graph.regions[n.ws]; return {x: (g.cx + n.x) * t.k + t.x, y: (g.cy + n.y) * t.k + t.y}; };
const out = {};
await wait(300);
"""
POSTLUDE = "\ndocument.body.dataset.result = JSON.stringify(out);"


def result(page):
    raw = page["dom"].split('data-result="', 1)[1].split('"', 1)[0]
    return json.loads(raw.replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">"))


@unittest.skipUnless(BROWSER, "no Chromium found")
class Gestures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = page_fixture()

    def script(self, body, route="", width=390, height=844, data=None):
        return result(run(data or self.data, route, width, height, script=PRELUDE + body + POSTLUDE))

    def test_pinch_zoom_stays_after_fingers_lift_and_through_refresh_and_panels(self):
        for order in ((12, 11), (11, 12)):
            with self.subTest(lift_first=order[0]):
                r = self.script(f"""
                    out.k0 = T().k; out.w0 = worldAt(200, 300);
                    ev('pointerdown', 11, 150, 300); ev('pointerdown', 12, 250, 300);
                    for (let i = 1; i <= 10; i++) {{ ev('pointermove', 11, 150 - 10 * i, 300); ev('pointermove', 12, 250 + 10 * i, 300); }}
                    out.k1 = T().k; out.w1 = worldAt(200, 300);
                    ev('pointerup', {order[0]}, 0, 300); out.kOne = T().k;
                    const rest = {order[1]};
                    for (let i = 1; i <= 6; i++) ev('pointermove', rest, rest === 11 ? 50 + 5 * i : 350 - 5 * i, 300);
                    out.kDrift = T().k;
                    ev('pointerup', rest, 80, 300); out.kUp = T().k;
                    await refresh(true); out.kRefresh = T().k;
                    document.querySelector('[data-tool="view"]').click(); await wait(50);
                    document.querySelector('.sheet-close').click(); await wait(50);
                    out.kPanel = T().k;""")
                self.assertGreater(r["k1"], r["k0"] * 2.5)
                for step in ("kOne", "kDrift", "kUp", "kRefresh", "kPanel"):
                    self.assertAlmostEqual(r[step], r["k1"], places=6, msg=step)
                self.assertAlmostEqual(r["w0"]["x"], r["w1"]["x"], places=3, msg="world point stays under the fingers")
                self.assertAlmostEqual(r["w0"]["y"], r["w1"]["y"], places=3)

    def test_pinch_with_moving_midpoint_keeps_the_point_under_the_fingers(self):
        r = self.script("""
            const w = worldAt(200, 300);
            ev('pointerdown', 11, 160, 300); ev('pointerdown', 12, 240, 300);
            for (let i = 1; i <= 8; i++) { ev('pointermove', 11, 160 - 6 * i + 3 * i, 300 + 4 * i); ev('pointermove', 12, 240 + 6 * i + 3 * i, 300 + 4 * i); }
            const mid = {x: (160 - 24 + 240 + 72) / 2, y: 332};
            out.err = Math.hypot(worldAt(mid.x, mid.y).x - w.x, worldAt(mid.x, mid.y).y - w.y);
            ev('pointerup', 11, 0, 0); ev('pointerup', 12, 0, 0);""")
        self.assertLess(r["err"], 0.01)

    def test_tap_selects_pan_does_not_and_second_finger_is_never_a_tap(self):
        lead = self.data["ids"]["lead"]
        r = self.script(f"""
            const p = screenOf('{lead}');
            ev('pointerdown', 11, p.x, p.y); for (let i = 1; i <= 6; i++) ev('pointermove', 11, p.x + 6 * i, p.y); ev('pointerup', 11, p.x + 36, p.y);
            out.afterPan = location.hash;
            await wait(60);                                   // let the frame with the new transform render
            const q = screenOf('{lead}');
            ev('pointerdown', 11, q.x, q.y); ev('pointerdown', 12, q.x + 80, q.y); ev('pointerup', 12, q.x + 80, q.y); ev('pointerup', 11, q.x, q.y);
            out.afterTwoFingers = location.hash;
            await wait(60);
            ev('pointerdown', 11, q.x, q.y); ev('pointermove', 11, q.x + 3, q.y + 2); ev('pointerup', 11, q.x + 3, q.y + 2);
            await wait(100); out.afterTap = location.hash;""")
        self.assertEqual(r["afterPan"], "")
        self.assertEqual(r["afterTwoFingers"], "")
        self.assertEqual(r["afterTap"], "#/g/" + urllib.parse.quote(lead, safe=""))

    def test_wheel_zoom_is_anchored_at_the_cursor(self):
        r = self.script("""
            const w = worldAt(120, 500);
            for (let i = 0; i < 5; i++) svg.dispatchEvent(new WheelEvent('wheel', {bubbles: true, cancelable: true, deltaY: -120, clientX: R().left + 120, clientY: R().top + 500}));
            const v = worldAt(120, 500); out.err = Math.hypot(v.x - w.x, v.y - w.y); out.k = T().k;""", width=1280, height=800)
        self.assertLess(r["err"], 0.01)

    def test_sustained_updates_while_panning_and_inspecting(self):
        ui = self.data["ids"]["ui"]
        r = self.script(f"""
            out.k0 = T().k;
            const before = {{}}; for (const [id, el] of Object.entries(graph.els.nodes)) before[id] = el.getAttribute('transform');
            const view = FIXTURE.view, extra = JSON.parse(JSON.stringify(view.agents[1]));
            extra.identity = 'codex:00000000-0000-4000-8000-0000000000ee'; extra.name = 'newcomer';
            ev('pointerdown', 11, 100, 400);                          // a pan in progress the whole time
            let x = 100, moved = 0;
            for (let round = 0; round < 12; round++) {{
              for (let i = 0; i < 3; i++) {{ x += 4; moved += 4; ev('pointermove', 11, x, 400); }}
              view.version += 1;
              view.agents[3].status = round % 2 ? 'working' : 'idle';
              view.agents[3].metrics.context = {{available: true, value: 1000 * (round + 1), unit: 'tokens'}};
              if (round === 4) view.agents.push(extra);
              if (round === 8) view.agents = view.agents.filter(a => a.identity !== extra.identity);
              await refresh();
              if (round === 6) out.extraPlaced = !!graph.nodes[extra.identity];
            }}
            ev('pointerup', 11, x, 400);
            out.dx = T().x; out.moved = moved; out.kEnd = T().k;
            out.nodeEls = document.querySelectorAll('svg.graph [data-id]').length;
            out.agents = FIXTURE.view.agents.length;
            out.ids = [...document.querySelectorAll('svg.graph [data-id]')].map(e => e.getAttribute('data-id'));
            const after = {{}}; for (const [id, el] of Object.entries(graph.els.nodes)) after[id] = el.getAttribute('transform');
            out.stable = Object.keys(before).filter(id => id !== FIXTURE.view.agents[3].identity).every(id => before[id] === after[id]);
            // inspecting while refreshing: selection and sheet stay
            location.hash = '#/g/' + encodeURIComponent('{ui}'); await wait(150);
            const tSel = T();
            for (let i = 0; i < 5; i++) {{ view.version += 1; await refresh(); }}
            out.sheetOpen = !document.querySelector('.sheet').hidden;
            out.selected = graph.selected; out.tSame = JSON.stringify(T()) === JSON.stringify(tSel);""")
        self.assertEqual(r["kEnd"], r["k0"], "refreshes never zoom")
        self.assertEqual(r["nodeEls"], r["agents"], "no ghost or duplicate nodes")
        self.assertEqual(len(r["ids"]), len(set(r["ids"])))
        self.assertTrue(r["extraPlaced"])
        self.assertTrue(r["stable"], "unchanged agents never move")
        self.assertTrue(r["sheetOpen"])
        self.assertEqual(r["selected"], ui)
        self.assertTrue(r["tSame"], "refresh never recentres while inspecting")

    def test_newcomers_do_not_overlap(self):
        r = self.script("""
            const view = FIXTURE.view;
            for (let i = 0; i < 6; i++) { const a = JSON.parse(JSON.stringify(view.agents[0])); a.identity = 'claude:new-' + i; a.name = 'n' + i; view.agents.push(a); }
            view.version += 1; await refresh();
            const nodes = Object.values(graph.nodes).filter(n => n.ws === 'w1');
            let minGap = 1e9;
            for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++)
              minGap = Math.min(minGap, Math.hypot(nodes[i].x - nodes[j].x, nodes[i].y - nodes[j].y) - nodes[i].r - nodes[j].r);
            out.minGap = minGap;""")
        self.assertGreaterEqual(r["minGap"], 30)

    def test_mark_read_closes_the_inspector_and_keeps_the_viewport(self):
        ui = self.data["ids"]["ui"]
        r = self.script("""
            ev('pointerdown', 11, 100, 600); ev('pointermove', 11, 140, 560); ev('pointerup', 11, 140, 560);
            location.hash = '#/g/' + encodeURIComponent('%s'); await wait(200);
            const t = T();
            [...document.querySelectorAll('.sheet button')].find(b => b.textContent === 'Mark read').click(); await wait(300);
            out.hash = location.hash; out.same = JSON.stringify(t) === JSON.stringify(T()); out.sheetHidden = document.querySelector('.sheet').hidden;""" % ui)
        self.assertEqual(r["hash"], "#/")
        self.assertTrue(r["same"])
        self.assertTrue(r["sheetHidden"])

    def test_keyboard_escape_and_zoom_keys(self):
        ui = self.data["ids"]["ui"]
        r = self.script("""
            const k0 = T().k;
            document.dispatchEvent(new KeyboardEvent('keydown', {key: '+'})); out.zoomed = T().k > k0;
            location.hash = '#/g/' + encodeURIComponent('%s'); await wait(150);
            document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'})); await wait(150);
            out.hash = location.hash;""" % ui)
        self.assertTrue(r["zoomed"])
        self.assertEqual(r["hash"], "#/")


@unittest.skipUnless(BROWSER, "no Chromium found")
class Layouts(unittest.TestCase):
    SIZES = {"phone": (390, 844), "landscape": (844, 390), "narrow": (320, 640), "desktop": (1280, 800)}

    @classmethod
    def setUpClass(cls):
        cls.data = page_fixture()
        cls.empty = page_fixture(watched=())
        sel = "/g/" + urllib.parse.quote(cls.data["ids"]["ui"], safe="")
        cls.pages = {name: (run(cls.data, "", w, h), run(cls.data, sel, w, h, script=PRELUDE + """
            const sheet = document.querySelector('.sheet').getBoundingClientRect();
            const zoom = document.querySelector('.zoom-tools').getBoundingClientRect();
            out.sheetVisible = !document.querySelector('.sheet').hidden;
            out.overlap = !(zoom.right <= sheet.left || zoom.left >= sheet.right || zoom.bottom <= sheet.top || zoom.top >= sheet.bottom);
            const p = screenOf(graph.selected);
            out.nodeCovered = p.x > sheet.left && p.x < sheet.right && p.y > sheet.top && p.y < sheet.bottom;
            out.pageScroll = document.scrollingElement.scrollHeight > window.innerHeight + 1;""" + POSTLUDE))
            for name, (w, h) in cls.SIZES.items()}
        cls.emptyPage = run(cls.empty, "", 390, 844)
        cls.list = run(cls.data, "/list", 390, 844)

    def test_graph_is_home_at_every_size(self):
        for name, (home, _) in self.pages.items():
            with self.subTest(size=name):
                self.assertIn('class="graph-shell"', home["dom"])
                self.assertEqual(home["probe"]["overflowing"], [])
                self.assertEqual(home["probe"]["smallTargets"], [])
                self.assertIn('data-tool="view"', home["dom"])
                self.assertIn('href="#/list"', home["dom"], "the list stays one tap away")

    def test_selection_sheet_never_covers_the_node_or_the_zoom_buttons(self):
        for name, (_, selected) in self.pages.items():
            with self.subTest(size=name):
                r = result(selected)
                self.assertTrue(r["sheetVisible"])
                self.assertFalse(r["overlap"], "zoom buttons clear of the sheet")
                self.assertFalse(r["nodeCovered"], "the selected node is not under the sheet")
                self.assertFalse(r["pageScroll"], "the page itself does not scroll in graph mode")

    def test_empty_and_list_fallback(self):
        self.assertIn("No workspaces are watched yet.", self.emptyPage["dom"])
        self.assertIn("Choose workspaces", self.emptyPage["dom"])
        main = self.list["dom"].split('<main id="app"', 1)[1]
        self.assertIn('data-tab="graph" href="#/"', self.list["dom"])
        self.assertIn("Website", main)

    def test_dashboard_states_and_symbols_are_used(self):
        home = self.pages["desktop"][0]["dom"]
        for cls in ("node s-needs_you", "node s-working", "node s-done", "node s-waiting"):
            self.assertIn(cls, home)
        self.assertNotIn("node h-", home, "nodes no longer use raw Herdr status classes")
        for glyph in ("?", "◐", "✓", "○"):
            self.assertIn(f">{glyph}<", home)
        self.assertNotIn(">×<", home)
        self.assertNotIn(">✕<", home)


if __name__ == "__main__":
    unittest.main()
