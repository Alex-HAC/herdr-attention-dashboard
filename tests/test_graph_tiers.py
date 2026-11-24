"""Graph chrome uses the same tiers as the list (v0.8.0, PH-1/GR-8). FIXTURE-BASED."""

import copy
import json
import unittest

from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER, run

PROBE = """
await new Promise(res => setTimeout(res, 300));
const badge = document.getElementById('notif-badge');
const rings = [...document.querySelectorAll('svg.graph .alert-ring')].filter(r => r.getAttribute('visibility') === 'visible').length;
graph.panel = 'attention'; graphRefresh(true);
await new Promise(res => setTimeout(res, 300));
const out = {status: document.querySelector('.graph-status').textContent, badgeHidden: badge.hidden,
  badge: badge.textContent, urgent: badge.classList.contains('urgent'), rings,
  heads: [...document.querySelectorAll('.sheet .attention-tier')].map(e => e.textContent)};
document.body.dataset.result = JSON.stringify(out);
"""


def result(page):
    raw = page["dom"].split('data-result="', 1)[1].split('"', 1)[0]
    return json.loads(raw.replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">"))


class WorkspaceTierCounts(unittest.TestCase):
    def test_each_workspace_exposes_its_real_tier_counts(self):
        view = page_fixture()["view"]
        for ws in view["workspaces"]:
            expected = sum(a.get("tier") == "waiting_on_you" for a in view["agents"] if a["workspace_id"] == ws["id"])
            self.assertEqual(ws["counts"]["waiting_on_you"], expected)


@unittest.skipUnless(BROWSER, "no Chromium found")
class GraphTiers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = page_fixture()

    def probe(self, data):
        return result(run(data, "", 390, 844, script=PROBE))

    def test_waiting_on_you_is_red_and_counted(self):
        r = self.probe(self.data)
        self.assertIn("1 waiting on you", r["status"])
        self.assertFalse(r["badgeHidden"])
        self.assertTrue(r["urgent"])
        self.assertEqual(r["rings"], 1)
        # Grouped like the list, in the list's order.
        self.assertEqual([h.split(" · ")[0] for h in r["heads"]],
                         ["Waiting on you", "Might want a look", "Waiting on a coordinator"])

    def test_workspace_badge_counts_tier_not_state(self):
        data = copy.deepcopy(self.data)
        for ws in data["view"]["workspaces"]:
            ws["attention"] = 3
            ws["counts"]["needs_you"] = 3
            ws["counts"]["waiting_on_you"] = 1 if ws["id"] == "w1" else 0
        page = run(data, "/list", 412, 915, script="""
          document.body.dataset.result = JSON.stringify([...document.querySelectorAll('.ws .badge')]
            .map(b => ({count: b.textContent, urgent: b.classList.contains('urgent')})));
        """)
        badges = result(page)
        self.assertEqual(sum(b["urgent"] for b in badges), 1)
        self.assertEqual(next(b["count"] for b in badges if b["urgent"]), "1")

    def test_coordinator_tier_is_not_need_you(self):
        data = copy.deepcopy(self.data)
        for a in data["view"]["agents"]:
            if a.get("tier") == "waiting_on_you":
                a["tier"] = "waiting_on_coordinator"
        r = self.probe(data)
        self.assertNotIn("waiting on you", r["status"])
        self.assertIn("Nothing needs you", r["status"])
        self.assertFalse(r["urgent"])
        self.assertEqual(r["rings"], 0)
        self.assertNotIn("Waiting on you", " ".join(r["heads"]))


QUOTA_PROBE = """
await new Promise(res => setTimeout(res, 300));
const cls = (id) => document.querySelector(`[data-quota-id="${id}"]`).className;
const st = document.documentElement.style;
document.body.dataset.result = JSON.stringify({near: cls("near"), wall: cls("wall"),
  meters: [...document.querySelectorAll('.quota-meter')].map(m => m.className),
  parked: st.getPropertyValue('--parked'), caution: st.getPropertyValue('--caution'),
  needs: st.getPropertyValue('--needs'), waiting: st.getPropertyValue('--waiting'),
  cautionCss: getComputedStyle(document.documentElement).getPropertyValue('--caution').trim()});
"""


@unittest.skipUnless(BROWSER, "no Chromium found")
class CautionColour(unittest.TestCase):
    def test_quota_near_limit_is_caution_and_red_only_at_the_wall(self):
        from herdr_attention.quotas import quota_level
        data = page_fixture()
        # Levels come from the server (quotas.quota_level, Alex's warning levels), as in the real view.
        win = lambda pct: {"title": "Weekly", "used_pct": pct, "level": quota_level(pct)}
        data["view"]["quotas"] = {"accounts": [
            {"id": "near", "label": "Near", "windows": [win(95)], "level": quota_level(95)},
            {"id": "wall", "label": "Wall", "windows": [win(100)], "level": quota_level(100)}]}
        data["view"]["status_colors"] = {"colors": {"idle": "#00ff00", "blocked": "#ff0000"}}
        r = result(run(data, "/list", 390, 844, script=QUOTA_PROBE))
        self.assertIn("alarm", r["near"])
        self.assertNotIn("wall", r["near"])
        self.assertIn("wall", r["wall"])
        self.assertEqual(sorted(m.split()[1] for m in r["meters"]), ["q-high", "q-wall"])
        # Herdr's colours never overwrite the dashboard's state tokens (theme v0.8).
        self.assertEqual(r["parked"], "")
        self.assertEqual(r["caution"], "")
        self.assertEqual(r["needs"], "")
        self.assertEqual(r["waiting"], "")
        self.assertTrue(r["cautionCss"].startswith("#"))


if __name__ == "__main__":
    unittest.main()
