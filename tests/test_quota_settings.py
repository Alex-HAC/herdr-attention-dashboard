"""v0.9.5 quota view settings: gauge parts, warning levels, size and grouping,
saved on the server; and the "no recorded account" line counts each agent once."""
import unittest

from herdr_attention import quotas
from herdr_attention.config import DEFAULTS, EDITABLE, QUOTA_VIEW_CHECKS, Settings
from herdr_attention.ui_contract import CONTRACT
from tests.test_api import make_collector
from tests.test_quotas import ORDER, TZ, USAGE, snapshot

PARTS = [p["id"] for p in CONTRACT["quotas"]["parts"]]


def card(q, account_id):
    return next(a for a in q["accounts"] if a["id"] == account_id)


class QuotaViewSettings(unittest.TestCase):
    def setUp(self):
        self.s = Settings()

    def test_defaults_keep_the_094_view(self):
        self.assertEqual(self.s.quota_view(), {"gauge_show": PARTS, "gauge_accounts": {}, "levels": {"warning": 70, "critical": 90},
                                               "account_levels": {}, "gauge_size": "large", "group_by_provider": False,
                                               "header_dials": True, "header_dials_count": 3, "centre": "higher"})
        self.assertEqual(PARTS, ["weekly", "session", "percent", "reset", "agents"])

    def test_every_key_is_editable_from_the_phone(self):
        for key in QUOTA_VIEW_CHECKS:
            self.assertTrue(EDITABLE[f"quotas.{key}"][1], key)

    def test_parts_keep_the_fixed_order_and_refuse_unknown_parts(self):
        self.assertEqual(self.s.validate("quotas.gauge_show", ["agents", "weekly", "agents"], from_phone=True), ["weekly", "agents"])
        self.assertEqual(self.s.validate("quotas.gauge_show", []), [])
        self.assertEqual(self.s.validate("quotas.gauge_accounts", {"cursor": ["reset", "percent"]}), {"cursor": ["percent", "reset"]})
        for key, bad in (("quotas.gauge_show", ["rings"]), ("quotas.gauge_show", "weekly"),
                         ("quotas.gauge_accounts", {"": ["weekly"]}), ("quotas.gauge_accounts", ["cursor"])):
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.s.validate(key, bad)

    def test_levels_are_whole_percent_and_warning_stays_below_nearly_full(self):
        self.assertEqual(self.s.validate("quotas.levels", {"warning": 60, "critical": 85.0}), {"warning": 60, "critical": 85})
        self.assertEqual(self.s.validate("quotas.account_levels", {"cursor": {"warning": 95, "critical": 100}}),
                         {"cursor": {"warning": 95, "critical": 100}})
        with self.assertRaisesRegex(ValueError, "warning level must be below nearly full"):
            self.s.validate("quotas.levels", {"warning": 90, "critical": 90})
        for bad in ({"warning": 0, "critical": 50}, {"warning": 50, "critical": 101}, {"warning": 50.5, "critical": 90},
                    {"warning": True, "critical": 90}, {"warning": 50}, {"warning": 50, "critical": 90, "x": 1}):
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.s.validate("quotas.levels", bad)

    def test_size_and_grouping(self):
        self.assertEqual(self.s.validate("quotas.gauge_size", "compact"), "compact")
        self.assertIs(self.s.validate("quotas.group_by_provider", "on"), True)
        with self.assertRaises(ValueError):
            self.s.validate("quotas.gauge_size", "huge")

    def test_a_bad_value_in_the_file_falls_back_to_the_default(self):
        s = Settings({"quotas": {"levels": {"warning": 95}, "gauge_size": "huge", "gauge_show": ["percent"]}})
        view = s.quota_view()
        self.assertEqual(view["levels"], DEFAULTS["quotas"]["levels"])   # 95 is not below 90
        self.assertEqual((view["gauge_size"], view["gauge_show"]), ("large", ["percent"]))


class LevelsDriveFlagsAndTheHeader(unittest.TestCase):
    def test_default_levels_give_the_same_flags_as_094(self):
        q = quotas.enrich(snapshot(), [])
        main, personal = card(q, "claude:main"), card(q, "claude:personal")
        self.assertEqual((main["worst_pct"], main["flag"]["id"], main["level"]), (94, "nearly_full", "high"))
        self.assertTrue(personal["healthy"])
        self.assertEqual(main["levels"], {"warning": 70, "critical": 90})
        self.assertFalse(main["levels_own"])

    def test_own_levels_flag_an_account_and_reach_the_header_but_never_move_it(self):
        base = quotas.enrich(snapshot(), [])
        # 0.9.8.7: the account's own windows decide (Weekly 29 %); Fable Weekly is a model-only line.
        q = quotas.enrich(snapshot(), [], view={"account_levels": {"claude:personal": {"warning": 10, "critical": 25}}})
        personal = card(q, "claude:personal")
        self.assertEqual((personal["flag"]["id"], personal["level"], personal["healthy"], personal["levels_own"]),
                         ("nearly_full", "high", False, True))
        self.assertEqual([w["level"] for w in personal["windows"]], ["mid", "high", "high"])
        self.assertEqual(q["account_ids"], base["account_ids"])
        self.assertEqual(q["summary"]["hot"], base["summary"]["hot"] + 1)
        self.assertTrue(q["header"]["urgent"])

    def test_raised_levels_calm_an_account_down(self):
        q = quotas.enrich(snapshot(), [], view={"account_levels": {"claude:main": {"warning": 96, "critical": 99}}})
        main = card(q, "claude:main")
        self.assertIsNone(main["flag"])
        self.assertEqual(main["level"], "low")
        self.assertNotIn("claude:main", [a["id"] for a in q["accounts"] if a["level"] in ("high", "wall")])
        # A full account stays full: 100 % is not a setting. Cursor's full "Named models" pool is a
        # model-only limit (0.9.8.7): its own line, while the account reads by its Monthly 93 %.
        cursor = card(q, "cursor")
        self.assertEqual((cursor["worst_pct"], cursor["flag"]["id"]), (93, "nearly_full"))
        self.assertIn("Named models: 100%", " ".join(w["label"] for w in cursor["scoped"]))

    def test_global_levels_apply_to_every_account_without_own_levels(self):
        q = quotas.enrich(snapshot(), [], view={"levels": {"warning": 10, "critical": 40},
                                                "account_levels": {"codex": {"warning": 70, "critical": 90}}})
        self.assertEqual(card(q, "codex-personal:personal")["flag"]["id"], "nearly_full")   # 49 %
        self.assertIsNone(card(q, "codex")["flag"])                                           # own levels, 18 %


class GaugeParts(unittest.TestCase):
    def test_parts_resolve_per_account_and_rings_say_which_is_which(self):
        q = quotas.enrich(snapshot(), [], view={"gauge_show": ["weekly", "percent"], "gauge_accounts": {"cursor": []}})
        self.assertEqual(card(q, "claude:main")["show"], ["weekly", "percent"])
        self.assertEqual((card(q, "cursor")["show"], card(q, "cursor")["show_own"]), ([], True))
        self.assertEqual([r["kind"] for r in card(q, "claude:main")["rings"]], ["weekly", "session"])
        self.assertEqual(q["view"], {"size": "large", "group_by_provider": False, "show": ["weekly", "percent"],
                                     "levels": {"warning": 70, "critical": 90}, "header_dials": True, "header_dials_count": 3, "centre": "higher"})
        self.assertEqual(card(q, "codex")["group"], card(q, "codex-personal:personal")["group"])

    def test_saved_settings_reach_the_view(self):
        collector = make_collector(quotas={"usage_dir": str(USAGE), "order_path": str(ORDER), "timezone": TZ})
        collector.update_settings({"quotas.gauge_size": "compact", "quotas.group_by_provider": True,
                                   "quotas.account_levels": {"codex": {"warning": 5, "critical": 15}},
                                   "quotas.gauge_accounts": {"cursor": ["percent"]}}, local=False)
        q = collector.view(local=False)["quotas"]
        self.assertEqual((q["view"]["size"], q["view"]["group_by_provider"]), ("compact", True))
        self.assertEqual(card(q, "codex")["flag"]["id"], "nearly_full")   # 18 %, real clock
        self.assertEqual(card(q, "cursor")["show"], ["percent"])


class UnassignedAgents(unittest.TestCase):
    """The old line said '409 running agents have no recorded account'."""

    def agent(self, identity, pane, **extra):
        return {"identity": identity, "pane_id": pane, "present": True, "name": identity.split(":")[1], **extra}

    def test_each_running_agent_counts_once_and_only_for_providers_with_quotas(self):
        agents = [
            self.agent("claude:lead", "w1:p1"),
            self.agent("claude:lead-again", "w1:p1"),                                  # same pane, counted once
            self.agent("claude:sub", None, in_process=True, in_process_parent="claude:lead"),
            self.agent("codex:folded", "w1:p3", folded=True),
            self.agent("codex:gone", "w1:p4", present=False),                          # ended
            self.agent("opencode:x", "w1:p5"),                                         # no quota records for it
            self.agent("codex:known", "w1:p6", launch={"account": "codex-work"}),      # recorded
            self.agent("codex:worker", "w2:p1"),
        ]
        q = quotas.enrich(snapshot(), agents)
        self.assertEqual(q["unassigned_agents"], 2)
        self.assertEqual([a["identity"] for a in q["unassigned"]], ["claude:lead", "codex:worker"])

    def test_nothing_when_every_agent_is_known(self):
        q = quotas.enrich(snapshot(), [self.agent("codex:known", "w1:p6", launch={"account": "codex-work"})])
        self.assertEqual((q["unassigned_agents"], q["unassigned"]), (0, []))


if __name__ == "__main__":
    unittest.main()
