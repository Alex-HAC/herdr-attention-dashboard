"""Phone page in headless Chromium at phone and desktop widths. FIXTURE-BASED.

The page, CSS and JS are the shipped files; the API is a stub answering from
tests/web_fixture.py (data produced by the real engine from fake agents).
Skipped when no Chromium is installed. Throwaway browser profile.
"""

import unittest
import urllib.parse

from tests.web_fixture import HIDDEN_LABEL, page_fixture
from tests.web_harness import BROWSER, run

WIDTHS = (("phone", 390, 1400), ("desktop", 1280, 900))


@unittest.skipUnless(BROWSER, "no Chromium found")
class Responsive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = page_fixture()
        cls.empty = page_fixture(watched=())
        cls.lead = next(a for a in cls.data["view"]["agents"] if a["name"] == "web-lead")
        cls.pages = {}
        routes = {
            "overview": (cls.data, "/list"),
            "workspace": (cls.data, "/w/w1"),
            "agent": (cls.data, "/a/" + urllib.parse.quote(cls.lead["identity"], safe="")),
            "agent-activity": (cls.data, "/a/" + urllib.parse.quote(cls.lead["identity"], safe="") + "/activity"),
            "agent-status": (cls.data, "/a/" + urllib.parse.quote(cls.lead["identity"], safe="") + "/status"),
            "settings": (cls.data, "/settings"),
            "empty": (cls.empty, "/list"),
        }
        for name, (fixture, route) in routes.items():
            for device, width, height in WIDTHS:
                cls.pages[(name, device)] = run(fixture, route, width, height)

    def main(self, name, device):
        dom = self.pages[(name, device)]["dom"]
        return dom.split('<main id="app"', 1)[1].split("</main>", 1)[0]

    def test_no_horizontal_overflow_and_usable_targets(self):
        for (name, device), page in self.pages.items():
            probe = page["probe"]
            with self.subTest(page=name, device=device):
                self.assertEqual(probe["scrollWidth"], probe["innerWidth"])
                self.assertEqual(probe["overflowing"], [])
                self.assertEqual(probe["smallTargets"], [], "tap targets under 30 px")

    def test_overview_is_workspace_centred(self):
        for device in ("phone", "desktop"):
            html = self.main("overview", device)
            self.assertIn("Website", html)
            self.assertIn("Data pipeline", html)
            self.assertIn("Docs", html)
            self.assertIn('class="status-header urgent', html)
            self.assertIn("waiting on you", html)
            self.assertIn("Waiting on a coordinator", html)
            self.assertIn("Might want a look", html)
            self.assertIn("1</b>waiting", html, "useful with zero alerts: idle work is shown too")
            self.assertIn('class="bar"', html)

    def test_overview_mirrors_agent_quotas(self):
        for device in ("phone", "desktop"):
            html = self.main("overview", device)
            with self.subTest(device=device):
                self.assertIn("Agent quotas", html)
                self.assertIn("From the Omarchy agent viewer", html)
                self.assertIn("Claude Code · Main", html)
                self.assertIn("Claude Code · Personal", html)
                self.assertIn("Team 5x", html)
                self.assertIn("Limits paused", html)
                self.assertIn("16%", html)
                self.assertIn("Business Premium", html)
                self.assertIn("Codex · Work", html)
                self.assertIn("Codex · Personal", html)
                self.assertIn("Plus", html)
                self.assertIn("Antigravity", html)
                self.assertIn("Gemini Enterprise Plus", html)
                self.assertIn("No quota meters", html)
                self.assertIn("data-quota-id=\"claude:personal\"", html)
                self.assertNotIn("example.com", html)
                self.assertNotIn("tok_should_never_leak", html)

    def test_workspace_groups_by_state_and_tab(self):
        html = self.main("workspace", "phone")
        order = [html.index(label) for label in ("Needs you · 1", "Working · 2", "Parked · 1")]
        self.assertEqual(order, sorted(order))
        self.assertIn("How it is arranged", html)
        self.assertNotIn("relationships between these agents are unknown", html)
        self.assertRegex(html, r"Tabs below\. Reporting lines: see graph \(\d+ declared, \d+ inferred\)\."
                               r"|No reporting lines recorded for these agents yet\.")
        for tab in ("Lead", "Builders", "Spikes"):
            self.assertIn(f'<span class="tabname">{tab}</span>', html)
        self.assertIn("possibly stalled", html)

    def test_agent_detail_shows_evidence_timeline_and_unknowns(self):
        html = self.main("agent", "phone")
        self.assertIn("Review the checkout plan", html)
        # Empty facts ("none declared") and "here now" are gone (details review 2026-11-24).
        self.assertNotIn("none declared", html)
        self.assertNotIn("here now", html)
        self.assertNotIn(">Explain<", html)
        self.assertIn("≥", html, "unknown start is marked")
        self.assertIn("Same screen in", self.main("agent-status", "phone"))
        activity = self.main("agent-activity", "phone")
        self.assertIn("New task title", activity)
        self.assertIn("≈ The collector noticed this later; the real time is unknown.", activity)
        self.assertIn("Seen ", activity)

    def test_settings_chooser_and_empty_state(self):
        settings = self.main("settings", "phone")
        self.assertIn(HIDDEN_LABEL, settings, "the chooser lists every workspace by name")
        self.assertIn('aria-label="Watch Website"', settings)
        empty = self.main("empty", "phone")
        self.assertIn("Pick the workspaces to watch", empty)
        self.assertIn('aria-label="Watch Docs"', empty)
        self.assertIn("Agent quotas", empty, "quotas are machine-wide and still show with no watched workspaces")
        self.assertIn("Claude Code · Personal", empty)

    def test_privacy(self):
        for (name, device), page in self.pages.items():
            html = self.main(name, device)
            with self.subTest(page=name, device=device):
                self.assertNotIn("SECRET TASK", page["dom"])
                self.assertNotIn("secret-agent", page["dom"])
                if name not in ("settings", "empty"):
                    self.assertNotIn(HIDDEN_LABEL, html, "unwatched workspaces only appear in the chooser")
                self.assertNotIn("<img src", html, "agent text never becomes HTML")
                calls = [c[0] for c in page["probe"]["calls"]]
                self.assertFalse(any(c.startswith("/api/output") for c in calls), "output only on request")
                self.assertFalse([c for c in page["probe"]["calls"] if c[1] == "POST"], "nothing is changed on load")
        self.assertIn("&lt;img src=x", self.main("workspace", "phone"))
        self.assertNotEqual(self.pages[("workspace", "phone")]["probe"]["title"], "pwned")


if __name__ == "__main__":
    unittest.main()
