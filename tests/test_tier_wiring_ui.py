"""v0.9.3 at 390 px: a notice that asked Alex to act counts in the List header and is listed under Needs action."""
import re
import unittest

from tests.web_fixture import build
from tests.web_harness import BROWSER, run


@unittest.skipUnless(BROWSER, "no Chromium found")
class NoticeLiftUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data = build()
        collector = data.pop("collector")
        etl = data["ids"]["etl"]
        # etl finished a turn and reports to web-lead; its phone notice asked Alex to act.
        event = next(e for e in collector.store.open_events(etl) if e["type"] == "completion")
        collector.store.set_notify_state([event["id"]], "sent", at=event["at"])
        collector.store.set_final_tier(event["id"], "waiting_on_you")
        collector.store.save_notification_text([event["id"]], "Approve the March backfill for tonight",
                                               "etl asks you to approve the backfill run.", "ai", event["at"])
        view = collector.view(local=False)
        items = collector.notifications(30)["items"]
        view["notifications"]["items"] = items
        data.update(view=view, notifications=items)
        cls.data, cls.event_id, cls.etl = data, event["id"], etl

    def test_engine_counts_the_lifted_agent(self):
        agent = next(a for a in self.data["view"]["agents"] if a["identity"] == self.etl)
        self.assertEqual(agent["tier"], "waiting_on_you")
        self.assertIsNone(agent["question"])
        self.assertTrue(agent["read_action"]["available"])
        item = next(n for n in self.data["notifications"] if n["id"] == self.event_id)
        self.assertEqual((item["tier"], item["actionable"]), ("waiting_on_you", True))

    def test_list_header_counts_it(self):
        expected = sum(a.get("tier") == "waiting_on_you" for a in self.data["view"]["agents"]
                       if a.get("present") and not a.get("folded"))
        dom = run(self.data, "/list", 390, 844)["dom"]
        self.assertIn(f'class="sh-title">{expected} waiting on you<', dom)
        self.assertGreaterEqual(expected, 1)
        # The row headline stays the phone title.
        self.assertIn('class="att-headline">Approve the March backfill for tonight<', dom)

    def test_notifications_list_it_under_needs_action(self):
        dom = run(self.data, "/n", 390, 844)["dom"]
        section = re.search(r'<section class="notif-group"[^>]*>(.*?)</section>', dom, re.S)
        self.assertIsNotNone(section)
        self.assertIn("Needs action", section.group(1))
        self.assertIn(f'id="n-{self.event_id}"', section.group(1))
        row = re.search(rf'<article[^>]*id="n-{self.event_id}".*?</article>', dom, re.S).group(0)
        self.assertIn("Waiting on you", row)


if __name__ == "__main__":
    unittest.main()
