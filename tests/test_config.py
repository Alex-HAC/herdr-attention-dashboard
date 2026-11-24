import tempfile
import unittest
from pathlib import Path

from herdr_attention.config import DEFAULTS, Settings, load_file
from herdr_attention.store import Store


class SettingsTests(unittest.TestCase):
    def test_defaults_watch_nothing(self):
        s = Settings()
        self.assertEqual(s.get("watch.enabled_workspaces"), [])
        self.assertFalse(s.watched("w1"))
        for event in ("question", "approval", "failure", "completion", "stall"):
            self.assertTrue(s.event_enabled(event))

    def test_file_then_overrides(self):
        s = Settings({"stall": {"minutes": 20}}, {"stall.minutes": 30})
        self.assertEqual(s.get("stall.minutes"), 30)
        self.assertEqual(s.get("stall.output_poll_seconds"), DEFAULTS["stall"]["output_poll_seconds"])

    def test_per_workspace_stall(self):
        s = Settings({"stall": {"minutes": 15, "per_workspace": {"w4": 40}}})
        self.assertEqual(s.stall_minutes("w4"), 40)
        self.assertEqual(s.stall_minutes("w1"), 15)

    def test_validation(self):
        s = Settings()
        self.assertEqual(s.validate("events.stall", "false"), False)
        self.assertEqual(s.validate("watch.enabled_workspaces", ["w2", "w1", "w1"]), ["w1", "w2"])
        with self.assertRaises(ValueError):
            s.validate("presence.mode", "sometimes")
        with self.assertRaises(ValueError):
            s.validate("stall.minutes", 0)
        with self.assertRaises(KeyError):
            s.validate("notify.ntfy_url", "https://evil.test")

    def test_phone_may_choose_workspaces_but_not_per_workspace_stall(self):
        s = Settings()
        self.assertEqual(s.validate("watch.enabled_workspaces", ["w1"], from_phone=True), ["w1"])
        with self.assertRaises(PermissionError):
            s.validate("stall.per_workspace", {"w1": 30}, from_phone=True)

    def test_parked_threshold(self):
        s = Settings()
        self.assertEqual(s.get("ui.parked_after_minutes"), 60)
        with self.assertRaises(ValueError):
            s.validate("ui.parked_after_minutes", 1)

    def test_unclassified_blocked_follows_question_or_approval(self):
        s = Settings({"events": {"question": False, "approval": False}})
        self.assertFalse(s.event_enabled("blocked"))
        s.set_override("events.approval", True)
        self.assertTrue(s.event_enabled("blocked"))

    def test_settings_survive_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state.db"
            store = Store(db)
            store.save_setting("stall.minutes", 42)
            store.save_setting("watch.enabled_workspaces", ["w3"])
            store.close()
            again = Settings({}, Store(db).load_settings())
            self.assertEqual(again.get("stall.minutes"), 42)
            self.assertTrue(again.watched("w3"))

    def test_example_config_parses_and_matches_defaults(self):
        example = load_file(Path(__file__).parent.parent / "config" / "config.example.toml")
        s = Settings(example)
        for section, values in DEFAULTS.items():
            for key, default in values.items():
                self.assertEqual(s.get(f"{section}.{key}"), default, f"{section}.{key} differs in the example")


if __name__ == "__main__":
    unittest.main()
