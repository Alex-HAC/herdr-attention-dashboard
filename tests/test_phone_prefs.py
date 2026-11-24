"""v0.9.8.9 Phone alerts: calmer defaults and per-kind tuning.

Alex 19 Nov 22:55: "make the notification to the phone easier to adapt and tune how i like, now
theyre quite intense haha". The day before: 137 pushes, 118 of them finished turns.
"""
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from herdr_attention import notify_prefs as np
from herdr_attention.config import DEFAULTS, Settings
from tests.test_notify import Rig

AMS = ZoneInfo("Europe/Amsterdam")


def at(hour, minute=0, day=16):
    return datetime(2026, 11, day, hour, minute, tzinfo=AMS).timestamp()


def ask(r, pane="w1:p3", session="c"):
    from tests.helpers import agent
    r.engine.apply_agent(agent(pane, "blocked", session=session, name=r.NAMES[session], title="SECRET task title"))
    r.engine.set_blocked_rule(f"claude:{session}", "bash_permission_prompt", "Allow this command?\n1. Yes\n2. No")


class Defaults(unittest.TestCase):
    def test_calmer_defaults(self):
        s = Settings(DEFAULTS)
        self.assertEqual(np.level(s, "waiting_on_you"), "urgent")
        self.assertEqual(np.level(s, "might_look"), "normal")
        self.assertEqual(np.level(s, "fyi_finished"), "quiet")
        self.assertFalse(np.away_only(s, "waiting_on_you"))
        self.assertTrue(np.away_only(s, "fyi_finished"))
        self.assertFalse(np.enabled(s, "quota_stop"), "no quota alerts on the phone by default")
        self.assertFalse(np.enabled(s, "visuals"), "no visuals phone alerts by default")
        self.assertTrue(np.quiet(s)["on"])
        self.assertEqual((np.quiet(s)["start"], np.quiet(s)["end"]), ("23:00", "08:00"))
        self.assertEqual(np.rate(s), {"count": 3, "minutes": 30})
        self.assertEqual(s.get("notify.finish_repeat_minutes"), 30)

    def test_levels_map_to_ntfy(self):
        self.assertEqual(np.LEVEL_NTFY, {"quiet": "low", "normal": "default", "urgent": "high"})

    def test_validators(self):
        s = Settings(DEFAULTS)
        self.assertEqual(s.validate("notify_tiers.fyi_finished_level", "Urgent"), "urgent")
        with self.assertRaises(ValueError):
            s.validate("notify_tiers.fyi_finished_level", "loud")
        self.assertIs(s.validate("notify_tiers.might_look_away_only", "false"), False)
        self.assertEqual(s.validate("notify.rate_count", "0"), 0)
        with self.assertRaises(ValueError):
            s.validate("notify.rate_minutes", 1)
        with self.assertRaises(KeyError):
            s.validate("notify.blocking_at_once", False)   # config only: blocking asks always push

    def test_snooze_values(self):
        s = Settings(DEFAULTS)
        now = at(14)
        self.assertEqual(np.snooze_value("1h", s, now), now + 3600)
        self.assertEqual(np.snooze_value("morning", s, now), at(8, day=17))
        self.assertEqual(np.snooze_value("morning", s, at(6)), at(8))
        self.assertEqual(np.snooze_value("off", s, now), 0)
        with self.assertRaises(ValueError):
            np.snooze_value("forever", s, now)

    def test_public_view_has_no_secrets_and_all_rows(self):
        s = Settings(DEFAULTS)
        s.set_override("notify.ntfy_topic", "TOPIC-SECRET")
        view = np.public_view(s, at(14), sent_24h=4)
        self.assertNotIn("TOPIC-SECRET", repr(view))
        self.assertEqual([t["id"] for t in view["types"]][:3], ["waiting_on_you", "might_look", "fyi_finished"])
        self.assertTrue(view["types"][0]["locked"])
        self.assertEqual(view["sent_24h"], 4)


class Blocking(unittest.TestCase):
    def test_hook(self):
        s = Settings(DEFAULTS)
        self.assertTrue(np.is_blocking(s, {"evidence": {}}, "waiting_on_you"))
        self.assertFalse(np.is_blocking(s, {"evidence": {}}, "fyi_finished"))
        # The ask protocol can mark an ask itself.
        self.assertTrue(np.is_blocking(s, {"evidence": {"blocking": True}}, "might_look"))
        self.assertFalse(np.is_blocking(s, {"evidence": {"blocking": False}}, "waiting_on_you"))


def rig(**notify):
    conf = {"rate_count": 0}
    conf.update(notify)
    r = Rig(presence={"mode": "auto", "phone_delay_minutes": 5}, notify=conf)
    return r


class AwayOnly(unittest.TestCase):
    def test_finished_turns_wait_while_at_the_laptop(self):
        r = rig()
        r.active()
        r.finish("w1:p1", "a")
        r.notifier.tick()
        self.assertEqual(r.sender.sent, [])
        self.assertIn("held", r.states())
        r.idle(10 * 60)
        r.notifier.tick()
        self.assertEqual(len(r.sender.sent), 1)
        self.assertEqual(r.sender.sent[0]["priority"], "low", "finished turns are quiet")

    def test_an_ask_comes_through_at_the_laptop(self):
        r = rig()
        r.active()
        ask(r)
        r.notifier.tick()
        self.assertEqual(len(r.sender.sent), 1)
        self.assertEqual(r.sender.sent[0]["priority"], "high")

    def test_away_only_off_sends_at_the_laptop_with_the_chosen_level(self):
        r = rig()
        r.settings.set_override("notify_tiers.fyi_finished_away_only", False)
        r.settings.set_override("notify_tiers.fyi_finished_level", "normal")
        r.active()
        r.finish("w1:p1", "a")
        r.notifier.tick()
        self.assertEqual([m["priority"] for m in r.sender.sent], ["default"])

    def test_always_sends_regardless(self):
        r = rig()
        r.settings.set_override("presence.mode", "always")
        r.active()
        r.finish("w1:p1", "a")
        r.notifier.tick()
        self.assertEqual(len(r.sender.sent), 1)

    def test_off_holds_even_asks(self):
        r = rig()
        r.settings.set_override("presence.mode", "never")
        ask(r)
        r.notifier.tick()
        self.assertEqual(r.sender.sent, [])


class SnoozeAndQuiet(unittest.TestCase):
    def test_snooze_parks_finishes_but_not_asks(self):
        r = rig()
        r.idle(10 * 60)
        r.settings.set_override("notify.snooze_until", r.clock.now + 3600)
        r.finish("w1:p1", "a")
        r.notifier.tick()
        self.assertEqual(r.sender.sent, [])
        self.assertIn("quiet", r.states())
        ask(r)
        r.notifier.tick()
        self.assertEqual(len(r.sender.sent), 1, "the ask came through the snooze")
        r.wait(3700)
        r.notifier.tick()
        self.assertEqual(len(r.sender.sent), 2, "the finish goes once the snooze ends")

    def test_quiet_hours_park_and_watched_closely_no_longer_skips_them(self):
        r = rig()
        r.clock.now = at(23, 30)
        r.settings.set_override("notify_tiers.quiet_hours", True)
        r.settings.set_override("notify_tiers.watch_closely", ["claude:a"])
        r.idle(10 * 60)
        r.finish("w1:p1", "a")
        r.notifier.tick()
        self.assertEqual(r.sender.sent, [])
        ask(r)
        r.notifier.tick()
        self.assertEqual(len(r.sender.sent), 1, "asks come through quiet hours")

    def test_watched_closely_gets_the_finish_guard(self):
        r = rig(finish_settle_seconds=90)
        r.settings.set_override("notify_tiers.watch_closely", ["claude:a"])
        r.idle(10 * 60)
        r.finish("w1:p1", "a")
        r.notifier.tick()
        self.assertEqual(r.sender.sent, [], "settles first, like any other agent")


class RateLimit(unittest.TestCase):
    def test_extras_wait_and_go_as_one_digest_but_asks_skip_the_queue(self):
        r = rig(rate_count=1, rate_minutes=30)
        r.idle(10 * 60)
        r.finish("w1:p1", "a")
        r.notifier.tick()
        self.assertEqual(len(r.sender.sent), 1)
        r.finish("w1:p2", "b")
        r.wait(60); r.notifier.tick()
        self.assertEqual(len(r.sender.sent), 1, "over the limit: waits")
        ask(r)
        r.notifier.tick()
        self.assertEqual(len(r.sender.sent), 2, "the ask skipped the limit")
        r.wait(31 * 60); r.notifier.tick()
        self.assertEqual(len(r.sender.sent), 3)

    def test_dashboard_listings_do_not_use_the_budget(self):
        r = rig(rate_count=1, rate_minutes=30)
        r.store.log_notification(r.clock.now, "dashboard", True)
        r.idle(10 * 60)
        r.finish("w1:p1", "a")
        r.notifier.tick()
        self.assertEqual(len(r.sender.sent), 1)

    def test_digest(self):
        r = rig(rate_count=1, rate_minutes=30)
        r.store.log_notification(r.clock.now, "single", True)
        r.idle(10 * 60)
        r.finish("w1:p1", "a"); r.finish("w1:p2", "b")
        r.notifier.tick()
        self.assertEqual(r.sender.sent, [])
        r.wait(31 * 60); r.notifier.tick()
        self.assertEqual(len(r.sender.sent), 1)
        self.assertIn("alpha", r.sender.sent[0]["message"])
        self.assertIn("beta", r.sender.sent[0]["message"])


class SettingsRoundTrip(unittest.TestCase):
    def test_snooze_through_settings_and_view(self):
        from herdr_attention.collector import Collector
        from tests.helpers import Clock, FakeHerdr, agent, settings
        herdr = FakeHerdr([agent("w1:p1", "working", session="a", name="alpha")])
        c = Collector(db_path=":memory:", client=herdr, settings=settings(), clock=Clock(at(14)))
        self.addCleanup(c.store.close)
        c.handle_snapshot()
        c.update_settings({"notify.snooze": "1h"}, local=False)
        self.assertEqual(c.settings.get("notify.snooze_until"), at(15))
        self.assertEqual(c.view(local=False)["phone_prefs"]["snooze_label"], "Snoozed until 15:00")
        c.update_settings({"notify.snooze": "off"}, local=False)
        self.assertEqual(c.settings.get("notify.snooze_until"), 0)
        c.update_settings({"notify_tiers.fyi_finished_level": "normal",
                           "notify_tiers.fyi_finished_away_only": False}, local=False)
        self.assertEqual(np.level(c.settings, "fyi_finished"), "normal")


if __name__ == "__main__":
    unittest.main()
