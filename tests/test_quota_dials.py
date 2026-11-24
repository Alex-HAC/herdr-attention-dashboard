"""v0.9.7.4: header mini quota dials instead of the "Now: ..." line."""

import unittest
from pathlib import Path

from herdr_attention import quotas
from herdr_attention.config import Settings, EDITABLE

WEB = Path(__file__).resolve().parent.parent / "herdr_attention" / "web"


def acct(i, pct=10, active=True):
    return {"id": i, "worst_pct": pct, "active": active, "levels": {"warning": 70, "critical": 90}}


class Dials(unittest.TestCase):
    def setUp(self):
        self.accounts = [acct("a"), acct("b"), acct("c"), acct("d"), acct("e")]

    def test_first_three_in_arrange_order_pins_first_hidden_left_out(self):
        self.assertEqual(quotas.header_dials(self.accounts), ["a", "b", "c"])
        layout = {"account_order": ["e", "d", "c"], "account_pinned": ["b"], "account_hidden": ["e"]}
        self.assertEqual(quotas.header_dials(self.accounts, layout), ["b", "d", "c"])
        self.assertEqual(quotas.header_dials(self.accounts, layout, 1), ["b"])
        self.assertEqual(quotas.header_dials(self.accounts, layout, 6), ["b", "d", "c", "a"])

    def test_a_nearly_full_account_takes_the_last_place(self):
        self.accounts[4] = acct("e", 95)
        self.assertEqual(quotas.header_dials(self.accounts), ["a", "b", "e"])
        self.accounts[3] = acct("d", 99)
        self.assertEqual(quotas.header_dials(self.accounts, count=2), ["a", "d"])   # the fullest one only
        self.assertEqual(quotas.header_dials(self.accounts, count=3), ["a", "b", "d"])
        self.accounts[3] = acct("d", 99, active=False)   # an inactive account is never pushed in
        self.assertEqual(quotas.header_dials(self.accounts), ["a", "b", "e"])
        self.assertEqual(quotas.header_dials(self.accounts, {"account_hidden": ["e"]}), ["a", "b", "c"])

    def test_settings_toggle_and_count(self):
        s = Settings()
        self.assertEqual((s.quota_view()["header_dials"], s.quota_view()["header_dials_count"]), (True, 3))
        self.assertIs(s.validate("quotas.header_dials", False), False)
        self.assertEqual(s.validate("quotas.header_dials_count", "4"), 4)
        for bad in (0, 7, "x", True, None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                s.validate("quotas.header_dials_count", bad)
        self.assertTrue(EDITABLE["quotas.header_dials"][1])

    def test_web_header_uses_dials_and_the_now_line_is_gone(self):
        app, index = (WEB / "app.js").read_text(), (WEB / "index.html").read_text()
        self.assertNotIn("Now: ${", app)
        self.assertIn("renderQuotaDials", app)
        self.assertIn('"quotas.header_dials"', app)
        self.assertIn("data-quota-chip", index)


if __name__ == "__main__":
    unittest.main()
