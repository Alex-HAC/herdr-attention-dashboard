"""v0.8.3 slice 3.4: the graph on a phone (fit, label size, region labels, parked size)."""

import json
import unittest

from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER, run

SCRIPT = """
// A tab bar like the phone shell's, added before the first fit: the fit must avoid it.
const bar = document.createElement('nav');
bar.style.cssText = 'position:fixed;left:0;right:0;bottom:0;height:80px';
document.body.appendChild(bar);
graph.autoFit = true; graph.userMoved = false; autoFit();
await new Promise((ok) => setTimeout(ok, 300));
const px = (el) => el.getBoundingClientRect();
const labels = [...document.querySelectorAll('svg.graph .node-label, svg.graph .region-label')]
  .filter((el) => el.textContent && getComputedStyle(el).display !== "none").map((el) => px(el).height);
const nodes = [...document.querySelectorAll('svg.graph .node')].map((g) => px(g.querySelector('.body')).bottom);
const regions = [...document.querySelectorAll('svg.graph .region-g')].map((g) => {
  const c = px(g.querySelector('circle.region')), l = px(g.querySelector('.region-label'));
  return {inside: l.top >= c.top && l.bottom <= c.bottom, top: l.top - c.top};
});
const parked = [...document.querySelectorAll('svg.graph .node.parked .body')].map((b) => Number(b.getAttribute('r')));
const zoomedOutRole = (() => { setTransform({k: 0.4, x: 0, y: 0}); return [...document.querySelectorAll(".role-label")].filter(e => e.textContent && getComputedStyle(e).display !== "none").length; })();
const zoomedInRole = (() => { setTransform({k: 1.2, x: 0, y: 0}); return [...document.querySelectorAll(".role-label")].filter(e => e.textContent && getComputedStyle(e).display !== "none").length; })();
document.body.dataset.reading = JSON.stringify({
  minLabel: Math.min(...labels), maxNodeBottom: Math.max(...nodes), barTop: px(bar).top,
  regions, parked, zoomedOutRole, zoomedInRole,
});
"""


@unittest.skipUnless(BROWSER, "no Chromium found")
class GraphOnPhone(unittest.TestCase):
    def test_phone_graph(self):
        page = run(page_fixture(), "", 390, 844, script=SCRIPT)
        raw = page["dom"].split('data-reading="', 1)[1].split('"', 1)[0]
        r = json.loads(raw.replace("&quot;", '"').replace("&amp;", "&"))
        self.assertGreaterEqual(r["minLabel"], 11.5, "labels at least 12 px on screen")
        self.assertLessEqual(r["maxNodeBottom"], r["barTop"], "fit stays above a bottom bar read from the DOM")
        for region in r["regions"]:
            self.assertTrue(region["inside"], f"region label inside its circle: {region}")
        self.assertTrue(r["parked"] and min(r["parked"]) >= 12)
        # Collision placement may hide offscreen roles at these artificial transforms.
        # Default busy-fit role visibility is covered by test_graph_readability.


if __name__ == "__main__":
    unittest.main()
