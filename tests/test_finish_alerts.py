"""v0.9.8.7: one agent's plain finishes no longer push again and again.

19 Nov 21:38-21:50: a release coordinator (w4:p5T) ended a turn every one to five minutes while
reading worker reports; each turn end was a new "Finished, not reviewed" event and went to the phone
(8 alerts in 12 minutes), each resolved again by the next turn. Alex saw the badge but no lasting item.
"""
import unittest

from herdr_attention.engine import Engine  # noqa: F401  (Rig builds it)
from tests.helpers import agent
from tests.test_notify import Rig

GUARD = {"finish_settle_seconds": 90, "finish_repeat_minutes": 20}


def rig():
    r = Rig(presence={"mode": "always", "phone_delay_minutes": 5}, notify=dict(GUARD))
    r.active()
    return r


def resume(r, pane="w1:p1", session="a"):
    r.engine.apply_agent(agent(pane, "working", session=session, name=r.NAMES[session]))


class FinishGuard(unittest.TestCase):
    def test_a_finish_waits_the_settle_time(self):
        r = rig()
        r.finish("w1:p1", "a")
        r.notifier.tick()
        self.assertEqual(r.sender.sent, [], "just finished: wait")
        r.wait(100)
        r.notifier.tick()
        self.assertEqual(len(r.sender.sent), 1)

    def test_a_turn_that_resumes_quickly_never_pushes(self):
        r = rig()
        r.finish("w1:p1", "a")
        r.wait(40)
        r.notifier.tick()
        resume(r)
        r.wait(100)
        r.notifier.tick()
        self.assertEqual(r.sender.sent, [])

    def test_the_same_agent_pushes_once_then_lists_until_the_window_ends(self):
        r = rig()
        r.finish("w1:p1", "a"); r.wait(100); r.notifier.tick()
        self.assertEqual(len(r.sender.sent), 1)
        for _ in range(3):   # the coordinator works and finishes again a few minutes later
            resume(r); r.wait(120)
            r.finish("w1:p1", "a"); r.wait(100); r.notifier.tick()
        self.assertEqual(len(r.sender.sent), 1, "listed on the dashboard, not pushed")
        self.assertIn("dashboard", r.states())
        resume(r); r.wait(20 * 60)
        r.finish("w1:p1", "a"); r.wait(100); r.notifier.tick()
        self.assertEqual(len(r.sender.sent), 2, "after the window a finish pushes again")

    def test_another_agent_is_not_held_back(self):
        r = rig()
        r.finish("w1:p1", "a"); r.wait(100); r.notifier.tick()
        r.finish("w1:p2", "b"); r.wait(100); r.notifier.tick()
        self.assertEqual(len(r.sender.sent), 2)


if __name__ == "__main__":
    unittest.main()


class AlertsHaveRows(unittest.TestCase):
    """The badge, the list and the phone alert name the same items."""

    def setUp(self):
        from herdr_attention.collector import Collector
        from tests.helpers import Clock, FakeHerdr, settings
        self.herdr = FakeHerdr([agent("w1:p1", "working", session="a", name="alpha"),
                                agent("w1:p2", "working", session="b", name="beta")])
        self.c = Collector(db_path=":memory:", client=self.herdr, settings=settings(), clock=Clock())
        self.addCleanup(self.c.store.close)
        self.c.handle_snapshot()

    def finish(self, pane, session, name):
        self.herdr.set(agent(pane, "done", session=session, name=name))
        self.c.handle_event({"event": "pane.agent_status_changed", "data": {"pane_id": pane, "workspace_id": "w1"}})
        return max(e["id"] for e in self.c.store.recent_events(50) if e["pane_id"] == pane)

    def test_a_finish_sent_to_the_phone_is_listed_and_counted(self):
        sent = self.finish("w1:p1", "a", "alpha")
        quiet = self.finish("w1:p2", "b", "beta")
        self.c.store.set_notify_state([sent], "sent", at=self.c.clock())
        self.c.store.set_notify_state([quiet], "dashboard", at=self.c.clock())
        items = self.c.notifications(50)["items"]
        ids = [i["id"] for i in items]
        self.assertIn(sent, ids, "the phone alert has its row")
        self.assertNotIn(quiet, ids, "a routine turn end that never pushed stays in agent history")
        self.assertEqual(self.c.notifications_unread(), 1)

    def test_unread_outside_the_list_does_not_light_the_badge(self):
        from herdr_attention import notifications
        old = self.finish("w1:p1", "a", "alpha")
        new = self.finish("w1:p2", "b", "beta")
        self.c.store.set_notify_state([old, new], "sent", at=self.c.clock())
        self.c.store.ack_event(new, self.c.clock())
        limit = notifications.LIST_LIMIT
        notifications.LIST_LIMIT = 1
        self.addCleanup(setattr, notifications, "LIST_LIMIT", limit)
        self.assertEqual(self.c.notifications_unread(), 0, "only the newest row is shown, and it is read")
