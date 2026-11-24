"""#/a/<identity>/conversation and /output open Details at that section (v0.8.2 slice C)."""

import json
import unittest
import urllib.parse

from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER, run

SCRIPT = """
await new Promise((ok) => setTimeout(ok, 500));
const more = document.querySelector('details.more');
const target = document.getElementById('agent-conversation');
document.body.dataset.reading = JSON.stringify({
  open: !!(more && more.open),
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

    def test_conversation_link_opens_details_scrolled_and_loads_messages(self):
        r = reading(run(self.data, self.base + "/conversation", 390, 844, script=SCRIPT))
        self.assertTrue(r["open"])
        self.assertGreater(r["scrollY"], 100)
        self.assertLess(abs(r["top"]), 120, "section is at the top of the screen")
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
