"""v0.9.8.10: a seen quota card must not come back for the same agent and the same reset.

22 Nov 15:15: the Placement coordinator handover sat on Claude Personal's weekly banner
("You've hit your weekly limit · resets Nov 23, 8pm (Europe/Amsterdam)") from 21 Nov 13:20.
Herdr kept flipping the pane done/working; each flip opened a new Finished event, the AI
gate titled it "Weekly limit reached...", and that was pushed to the phone (20 times in 48 h)
even after Alex had acked it. The account weekly really was at 100%; the card should have
been raised once, listed on the dashboard, and never pushed.
"""
import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from herdr_attention.engine import Engine  # noqa: F401
from herdr_attention.quota_stops import (
    QuotaStops, parse_reset, limit_is_real, already_seen, match_account,
)
from herdr_attention.store import Store
from tests.helpers import Clock, agent, settings
from tests.test_notify import Rig
from tests.test_quota_false_positive import CLAUDE_BANNER, agent as qagent

BANNER = """● shrink-metrics-o: done: weekly shrink-metrics script

  ⎿  You've hit your weekly limit · resets Nov 23, 8pm (Europe/Amsterdam)

  What do you want to do?
  ❯ 1. Stop and wait for limit to reset
    2. Wait here, then continue automatically at Nov 23, 8pm
    3. Switch to usage credits
"""
AMS = ZoneInfo("Europe/Amsterdam")
NOW = datetime(2026, 11, 22, 15, 10, tzinfo=AMS).timestamp()
RESET = datetime(2026, 11, 23, 20, 0, tzinfo=AMS).timestamp()

PERSONAL = {
    "id": "claude:personal", "provider": "claude", "provider_name": "Claude Code",
    "label": "Personal",
    "windows": [{"label": "Weekly (7-day)", "title": "Weekly", "used_pct": 100,
                 "resets_at": datetime.fromtimestamp(RESET, timezone.utc).isoformat()}],
}
MAIN = {
    "id": "claude:main", "provider": "claude", "provider_name": "Claude Code",
    "label": "Main",
    "windows": [{"label": "Weekly (7-day)", "title": "Weekly", "used_pct": 40,
                 "resets_at": "2026-11-25T05:59:59+01:00"},
                {"label": "Fable Weekly", "title": "Fable Weekly", "used_pct": 89,
                 "resets_at": "2026-11-25T05:59:59+01:00"}],
}


class ParseReset(unittest.TestCase):
    def test_banner_reset_is_personal_monday_8pm(self):
        at = parse_reset(BANNER, NOW)
        self.assertEqual(at, RESET)


class RealLimit(unittest.TestCase):
    def test_matched_by_reset_time_when_launch_account_is_unknown(self):
        account, reset_at = match_account(qagent(), [MAIN, PERSONAL], BANNER, NOW)
        self.assertEqual(account["id"], "claude:personal")
        self.assertEqual(reset_at, RESET)

    def test_not_flagged_when_the_account_is_not_at_100(self):
        accounts = [dict(MAIN), dict(PERSONAL, windows=[{"used_pct": 40, "resets_at": datetime.fromtimestamp(RESET, timezone.utc).isoformat()}])]
        self.assertFalse(limit_is_real(qagent(), accounts, BANNER, NOW))


class NoReraise(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(NOW)
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.stops = QuotaStops(self.store, self.clock)

    def test_same_banner_after_a_working_flicker_does_not_open_a_second_event(self):
        a = qagent(status="done")
        self.stops.observe(a, BANNER, [PERSONAL])
        self.assertEqual(len(self.store.recent_events(10)), 1)
        self.assertTrue(self.stops.get(a["identity"])["active"])
        self.stops.observe(qagent(status="working"), BANNER, [PERSONAL])
        self.assertFalse(self.stops.get(a["identity"])["active"], "working flicker deactivates")
        self.stops.observe(a, BANNER, [PERSONAL])
        self.assertEqual(len(self.store.recent_events(10)), 1, "same reset: no new card")
        self.assertTrue(self.stops.get(a["identity"])["active"])
        self.assertEqual(self.store.recent_events(1)[0]["notify_state"], "dashboard")

    def test_acked_or_already_sent_limit_cards_are_not_raised_again(self):
        ident = "claude:coord"
        eid = self.store.add_event(at=NOW - 3600, identity=ident, workspace_id="w1", pane_id="w1:p1",
                                   type="completion", evidence={}, confidence="high", seen_late=False,
                                   notify_state="sent", title="Weekly limit reached during placement handover")
        self.store.ack_event(eid, NOW - 1800)
        a = qagent(ident, status="done")
        self.stops.observe(a, BANNER, [PERSONAL])
        self.assertFalse(self.stops.get(ident)["active"])
        self.assertEqual([e["type"] for e in self.store.recent_events(10) if e["type"] == "quota_stop"], [])

    def test_a_new_episode_after_the_reset_may_be_raised(self):
        a = qagent(status="done")
        self.stops.observe(a, BANNER, [PERSONAL])
        self.assertEqual(len(self.store.recent_events(10)), 1)
        self.clock.now = RESET + 60
        later = BANNER.replace("Nov 23, 8pm", "Nov 30, 8pm")
        personal = dict(PERSONAL, windows=[{"used_pct": 100, "resets_at":
            datetime(2026, 11, 30, 20, 0, tzinfo=AMS).isoformat()}])
        self.stops.observe(a, later, [personal])
        self.assertEqual(len([e for e in self.store.recent_events(10) if e["type"] == "quota_stop"]), 2)


class NoCompletionLoop(unittest.TestCase):
    def test_a_done_working_done_flicker_does_not_open_another_finished_or_push(self):
        r = Rig(presence={"mode": "always"}, notify={"rate_count": 0})
        ident = "claude:a"
        # Seed a quota stop for this agent so a later Finished is held.
        banner = BANNER
        r.clock.now = NOW
        from herdr_attention.quota_stops import QuotaStops
        stops = QuotaStops(r.store, r.clock)
        pane_agent = {"identity": ident, "status": "done", "workspace_id": "w1", "pane_id": "w1:p1",
                      "title": "Placement coordinator handover"}
        # Attach launch so we can also test hold without matching? Reset match uses accounts.
        stops.observe(pane_agent, banner, [PERSONAL])
        self.assertTrue(stops.holds_completion(ident))
        before = len(r.store.recent_events(50))
        from tests.helpers import agent as ag
        r.engine.apply_agent(ag("w1:p1", "working", session="a", name="alpha"))
        r.engine.apply_agent(ag("w1:p1", "done", session="a", name="alpha"))
        kinds = [e["type"] for e in r.store.recent_events(50)[before:]]
        self.assertNotIn("completion", kinds)
        r.notifier.tick()
        self.assertEqual(r.sender.sent, [])


class NoPhone(unittest.TestCase):
    def test_quota_stop_and_limit_titled_completion_do_not_push(self):
        r = Rig(presence={"mode": "always"}, notify={"rate_count": 0})
        r.idle(10 * 60)
        eid = r.store.add_event(at=r.clock.now, identity="claude:a", workspace_id="w1", pane_id="w1:p1",
                                type="quota_stop", evidence={"notify_tier": "might_look"}, confidence="high",
                                seen_late=False, notify_state="pending", title="Out of quota")
        r.notifier.tick()
        self.assertEqual(r.sender.sent, [])
        self.assertEqual(r.store.event(eid)["notify_state"], "off")
        eid2 = r.store.add_event(at=r.clock.now, identity="claude:a", workspace_id="w1", pane_id="w1:p1",
                                 type="completion", evidence={"notify_tier": "might_look"}, confidence="high",
                                 seen_late=False, notify_state="pending", title="Weekly limit reached during placement handover")
        r.store.save_notification_text([eid2], "Weekly limit reached during placement handover",
                                       "Check after the limit resets.", "ai", r.clock.now)
        r.notifier.tick()
        self.assertEqual(r.sender.sent, [])


class StaleReply(unittest.TestCase):
    def test_sure_does_not_stick_to_a_later_quota_card(self):
        from herdr_attention.reply_routing import last_reply
        agent = {
            "identity": "claude:coord",
            "name": None,
            "title": "Placement coordinator handover",
            "last_assistant": {"at": NOW - 86400, "text": "You've hit your weekly limit"},
            "event_at": NOW,
        }
        rows = [{"about": "claude:coord", "identity": "claude:coord", "at": NOW - 86400 + 3600,
                 "id": 1, "agent_name": "Finished, not reviewed", "text": "Answer to x: Sure",
                 "status": "sent", "sent_at": NOW - 86400 + 3600, "error": None}]
        self.assertIsNone(last_reply(agent, None, rows, {1: "Sure"}))
        # On the original question (event earlier than the reply) the receipt still shows, without the kind label as "to".
        agent["event_at"] = NOW - 86400
        got = last_reply(agent, None, rows, {1: "Sure"})
        self.assertEqual(got["text"], "Sure")
        self.assertIsNone(got["to"])


if __name__ == "__main__":
    unittest.main()
