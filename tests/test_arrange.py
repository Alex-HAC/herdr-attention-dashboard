"""v0.9.4 Arrange: Alex's saved order for accounts and main information blocks."""
import unittest

from herdr_attention.config import EDITABLE, LAYOUT_KEYS, LAYOUT_SECTIONS, Settings
from herdr_attention.ui_contract import CONTRACT
from tests.test_api import make_collector

SECTIONS = [s["id"] for s in CONTRACT["arrange"]["sections"]]


class LayoutSettings(unittest.TestCase):
    def setUp(self):
        self.s = Settings()

    def test_defaults_keep_the_contract_order_and_hide_nothing(self):
        self.assertEqual(SECTIONS[0], "needs_action")
        self.assertEqual(list(LAYOUT_SECTIONS), SECTIONS)
        self.assertEqual(self.s.arrangement(), {"account_order": [], "account_pinned": [], "account_hidden": [],
                                                 "section_order": SECTIONS, "section_hidden": []})

    def test_every_key_is_editable_from_the_phone(self):
        for key in LAYOUT_KEYS:
            self.assertTrue(EDITABLE[f"layout.{key}"][1], key)

    def test_account_lists_keep_order_drop_duplicates_and_accept_new_ids(self):
        value = self.s.validate("layout.account_order", ["codex-work", "claude-personal", "codex-work", "new-thing"],
                                from_phone=True)
        self.assertEqual(value, ["codex-work", "claude-personal", "new-thing"])
        for bad in ("codex-work", [""], [3], ["x" * 121], ["a\nb"], ["ok"] * 0 + [None]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.s.validate("layout.account_pinned", bad)

    def test_section_order_is_completed_and_unknown_sections_are_refused(self):
        self.assertEqual(self.s.validate("layout.section_order", ["other_updates", "quotas"]),
                         ["other_updates", "quotas", "needs_action", "status_reports", "latest_visuals"])
        with self.assertRaises(ValueError):
            self.s.validate("layout.section_order", ["workspaces"])

    def test_needs_action_can_be_moved_but_never_hidden(self):
        order = self.s.validate("layout.section_order", ["quotas", "needs_action"])
        self.assertEqual(order[:2], ["quotas", "needs_action"])
        with self.assertRaisesRegex(ValueError, "Needs action cannot be hidden"):
            self.s.validate("layout.section_hidden", ["needs_action"])
        self.assertEqual(self.s.validate("layout.section_hidden", ["other_updates", "quotas"]),
                         ["quotas", "other_updates"])

    def test_a_bad_value_in_the_file_falls_back_to_the_default(self):
        s = Settings({"layout": {"section_hidden": ["needs_action"], "account_order": "oops",
                                 "account_pinned": ["claude-personal"]}})
        got = s.arrangement()
        self.assertEqual(got["section_hidden"], [])
        self.assertEqual(got["account_order"], [])
        self.assertEqual(got["account_pinned"], ["claude-personal"])


class LayoutInView(unittest.TestCase):
    def test_view_layout_keeps_findings_and_adds_the_saved_order(self):
        collector = make_collector()
        view = collector.view(local=False)
        self.assertIn("findings", view["layout"])
        self.assertEqual(view["layout"]["section_order"], SECTIONS)

    def test_a_phone_save_reaches_every_surface_and_is_stored(self):
        collector = make_collector()
        collector.update_settings({"layout.account_order": ["codex-work", "claude-personal"],
                                   "layout.account_pinned": ["claude-personal"],
                                   "layout.account_hidden": ["antigravity"],
                                   "layout.section_order": ["quotas"],
                                   "layout.section_hidden": ["latest_visuals"]}, local=False)
        for local in (False, True):
            layout = collector.view(local=local)["layout"]
            self.assertEqual(layout["account_order"], ["codex-work", "claude-personal"])
            self.assertEqual(layout["account_pinned"], ["claude-personal"])
            self.assertEqual(layout["account_hidden"], ["antigravity"])
            self.assertEqual(layout["section_order"][:2], ["quotas", "needs_action"])
            self.assertEqual(layout["section_hidden"], ["latest_visuals"])
        self.assertEqual(collector.store.load_settings()["layout.account_pinned"], ["claude-personal"])
        self.assertEqual(collector.view(local=False)["settings"]["layout.section_hidden"], ["latest_visuals"])

    def test_hiding_needs_action_is_refused_and_nothing_is_saved(self):
        collector = make_collector()
        with self.assertRaises(ValueError):
            collector.update_settings({"layout.section_order": ["quotas"],
                                       "layout.section_hidden": ["needs_action"]}, local=False)
        self.assertEqual(collector.view(local=False)["layout"]["section_order"], SECTIONS)


if __name__ == "__main__":
    unittest.main()
