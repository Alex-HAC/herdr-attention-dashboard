"""v0.8.1 slice 2.2: the graph speaks the list's dashboard states."""

import json
import unittest

from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER, run

SCRIPT = """
await new Promise((ok) => setTimeout(ok, 300));
const base = {present: true, status: 'blocked', state: 'needs_you', kind: 'approval'};
const states = {
  onYou: graphState({...base, tier: 'waiting_on_you'}),
  onCoordinator: graphState({...base, tier: 'waiting_on_coordinator'}),
  acked: graphState({...base, acked: true}),
  working: graphState({present: true, state: 'working', status: 'working'}),
  odd: graphState({present: true, state: 'bogus'}),
};
document.querySelector('[data-tool="view"]').click();
await new Promise((ok) => setTimeout(ok, 200));
const sheet = document.querySelector('.sheet-body');
const chips = [...sheet.querySelectorAll('.chips .chip')].map((c) => c.textContent.trim());
const legend = [...sheet.querySelectorAll('.legend-list')][0];
const rows = [...legend.querySelectorAll('li')].map((li) => li.textContent.trim());
document.body.dataset.reading = JSON.stringify({
  states, chips, rows, regionLegend: !!sheet.querySelector('svg circle.region'),
  sheetText: sheet.textContent,
});
"""


@unittest.skipUnless(BROWSER, "no Chromium found")
class GraphStates(unittest.TestCase):
    def test_states_filters_and_legend_match_the_list(self):
        data = page_fixture()
        page = run(data, "", 1280, 900, script=SCRIPT)
        raw = page["dom"].split('data-reading="', 1)[1].split('"', 1)[0]
        r = json.loads(raw.replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">"))
        self.assertEqual(r["states"], {"onYou": "needs_you", "onCoordinator": "waiting", "acked": "waiting",
                                       "working": "working", "odd": "unknown"})
        names = ["need you", "working", "done", "waiting", "parked", "unclear", "out of quota"]
        self.assertEqual(r["chips"], names)
        self.assertEqual([row.split(" ·")[0][1:].strip() for row in r["rows"][:len(names)]], names,
                         "legend rows: same names, same order as the chips")
        self.assertTrue(r["rows"][0].startswith("?"))
        self.assertIn("waiting on you", r["rows"][0])
        self.assertTrue(r["regionLegend"], "region legend is a filled circle")
        self.assertNotIn("Herdr status colours", r["sheetText"])

    def test_inspector_shows_raw_herdr_status_as_secondary_line(self):
        import urllib.parse
        data = page_fixture()
        page = run(data, "/g/" + urllib.parse.quote(data["ids"]["ui"], safe=""), 1280, 900)
        self.assertIn("Herdr status: blocked", page["dom"])


if __name__ == "__main__":
    unittest.main()
