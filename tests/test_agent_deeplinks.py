"""#/a/<identity>/conversation and /output open the Activity screen of the details view and load that
part once (v0.8.2 slice C; screens since the details review 2026-11-24)."""

import json
import unittest
import urllib.parse

from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER, run

SCRIPT = """
await new Promise((ok) => setTimeout(ok, 500));
const screen = document.querySelector('.details-screen');
const target = document.getElementById('agent-conversation');
document.body.dataset.reading = JSON.stringify({
  open: !!screen && screen.dataset.screen === 'activity',
  top: target ? Math.round(target.getBoundingClientRect().top) : null,
  scrollY: Math.round(window.scrollY),
  calls: window.__calls.map((c) => c[0].split('?')[0]),
});
"""


def reading(page):
    raw = page["dom"].split('data-reading="', 1)[1].split('"', 1)[0]
    return json.loads(raw.replace("&quot;", '"').replace("&amp;", "&"))


@unittest.skipUnless(BROWSER, "no Chromium found")
class AgentDeepLinks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = page_fixture()
        lead = next(a for a in cls.data["view"]["agents"] if a["name"] == "web-lead")
        cls.base = "/a/" + urllib.parse.quote(lead["identity"], safe="")

    def test_conversation_link_opens_activity_and_loads_messages(self):
        r = reading(run(self.data, self.base + "/conversation", 390, 844, script=SCRIPT))
        self.assertTrue(r["open"])
        self.assertIsNotNone(r["top"], "the Messages part is on that screen")
        self.assertIn("/api/conversation", r["calls"])
        self.assertNotIn("/api/output", r["calls"])

    def test_output_link_loads_terminal_lines(self):
        r = reading(run(self.data, self.base + "/output", 390, 844, script=SCRIPT))
        self.assertTrue(r["open"])
        self.assertIn("/api/output", r["calls"])

    def test_plain_agent_link_is_unchanged(self):
        r = reading(run(self.data, self.base, 390, 844, script=SCRIPT))
        self.assertFalse(r["open"])
        self.assertEqual(r["scrollY"], 0)
        self.assertNotIn("/api/conversation", r["calls"])
        self.assertNotIn("/api/output", r["calls"])


if __name__ == "__main__":
    unittest.main()
