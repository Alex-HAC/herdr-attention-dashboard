"""uiux2 F4/F9: no empty Questions card on List; named Status report buttons outside the card link."""
import copy
import re
import unittest

from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER, run


@unittest.skipUnless(BROWSER, "no Chromium found")
class PhoneFirstGlance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = page_fixture()

    def no_questions(self):
        data = copy.deepcopy(self.data)
        data["view"]["questions"] = []
        return data

    def test_empty_questions_card_hidden_on_list_and_notifications(self):
        for route in ("/list", "/n"):
            with self.subTest(route=route):
                dom = run(self.no_questions(), route, 390, 844)["dom"]
                self.assertNotIn("No open questions for you.", dom)
                self.assertNotIn("Questions for you · 0", dom)

    def test_questions_route_keeps_the_calm_card(self):
        dom = run(self.no_questions(), "/questions", 390, 844)["dom"]
        self.assertIn("Questions for you · 0", dom)
        self.assertIn("No open questions for you.", dom)

    def test_two_coordinators_get_named_buttons_outside_the_link(self):
        data = self.no_questions()
        view = data["view"]
        ws = next(w for w in view["workspaces"] if w.get("agent_count"))
        members = [a for a in view["agents"] if a.get("workspace_id") == ws["id"]][:2]
        if len(members) < 2:
            self.skipTest("fixture workspace has fewer than two agents")
        for a in members:
            a["is_coordinator"] = True
        ws["coordinators"] = [a["identity"] for a in members]
        dom = run(data, "/list", 390, 844)["dom"]
        # 0.9.11.0: an AI button (spark icon before the label).
        buttons = re.findall(r'class="btn btn-ai status-report-button"[^>]*>.*?</svg>([^<]*)<', dom)
        self.assertEqual(len(buttons), 2)
        self.assertTrue(all(b.startswith("Status report: ") for b in buttons))
        self.assertEqual(len(set(buttons)), 2)
        for link in re.findall(r'<a class="card ws".*?</a>', dom, re.S):
            self.assertNotIn("status-report-button", link)


if __name__ == "__main__":
    unittest.main()
