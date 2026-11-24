"""3.1 status header on Graph and List (v0.8.3). FIXTURE-BASED."""

import copy
import unittest

from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER, run


def header(dom):
    start = dom.index('class="status-header')
    return dom[dom.rindex("<", 0, start):dom.index("</", dom.index("sh-title", start))]


@unittest.skipUnless(BROWSER, "no Chromium found")
class StatusHeader(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = page_fixture()

    def test_urgent_header_links_to_the_notification_on_both_routes(self):
        event = next(a["event_id"] for a in self.data["view"]["agents"] if a.get("tier") == "waiting_on_you")
        for route in ("", "/list"):
            with self.subTest(route=route):
                h = header(run(copy.deepcopy(self.data), route, 390, 844)["dom"])
                self.assertIn("status-header urgent", h)
                self.assertIn('href="#/questions"', h)
                self.assertIn("1 waiting on you", h)

    def test_calm_header_and_paused(self):
        data = copy.deepcopy(self.data)
        for a in data["view"]["agents"]:
            if a.get("tier") == "waiting_on_you":
                a["tier"] = "might_look"
        data["view"].setdefault("settings", {})["presence.mode"] = "never"
        for route in ("", "/list"):
            with self.subTest(route=route):
                dom = run(copy.deepcopy(data), route, 390, 844)["dom"]
                self.assertIn("status-header calm", dom)
                self.assertIn("Nothing needs you", dom)
                self.assertIn("Phone alerts paused", dom)


if __name__ == "__main__":
    unittest.main()
