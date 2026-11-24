"""Engine behaviour against the plan's acceptance tests (fixture data)."""

import tempfile
import unittest
from pathlib import Path

from herdr_attention.config import Settings
from herdr_attention.engine import Engine, identity_of
from herdr_attention.store import Store
from tests.helpers import Clock, agent, settings, workspaces


def make(conf: Settings | None = None, store: Store | None = None, clock: Clock | None = None):
    clock = clock or Clock()
    conf = conf or settings()
    store = store or Store(":memory:")
    return Engine(conf, store, clock), store, clock


def events(store: Store, **match):
    return [e for e in store.recent_events(500) if all(e[k] == v for k, v in match.items())]


class Watching(unittest.TestCase):
    def test_unwatched_workspaces_never_show_up(self):
        engine, store, _ = make()
        engine.apply_snapshot([agent("w1:p1", "blocked", session="a"), agent("w2:p1", "done", session="b")],
                              workspaces("w1", "w2"))
        engine.set_blocked_rule("claude:a", "bash_permission_prompt")
        engine.apply_agent(agent("w2:p1", "blocked", session="b"))
        view = engine.view()
        self.assertEqual([a["workspace_id"] for a in view["agents"]], ["w1"])
        self.assertEqual([w["id"] for w in view["workspaces"]], ["w1"])
        self.assertNotIn("chooser", view)
        self.assertTrue(all(e["workspace_id"] == "w1" for e in store.recent_events(100)))
        self.assertTrue(all(e["workspace_id"] == "w1" for e in engine.history(100)))
        self.assertIsNone(engine.agent_by_identity("claude:b"))
        # The chooser may list the workspace (id and label only) so it can be switched on.
        local = engine.view(include_all_workspaces=True)
        self.assertEqual([a["workspace_id"] for a in local["agents"]], ["w1"])
        self.assertEqual([w["id"] for w in local["workspaces"]], ["w1"])
        self.assertEqual({w["id"] for w in local["chooser"]}, {"w1", "w2"})
        self.assertEqual(set(local["chooser"][1]), {"id", "label", "number", "watched", "muted"})

    def test_switching_a_workspace_off_forgets_its_agents(self):
        conf = settings()
        engine, store, _ = make(conf)
        engine.apply_snapshot([agent("w1:p1", "done")], workspaces("w1"))
        conf.set_override("watch.enabled_workspaces", [])
        engine.settings_changed()
        self.assertEqual(engine.view()["agents"], [])
        self.assertEqual(engine.history(10), [])


class StatusEvents(unittest.TestCase):
    def test_one_event_per_episode(self):
        engine, store, clock = make()
        engine.apply_snapshot([agent("w1:p1", "working", seq=1)], workspaces("w1"))
        clock.advance(60)
        engine.apply_agent(agent("w1:p1", "done", seq=2))
        engine.apply_agent(agent("w1:p1", "done", seq=2))  # repeated event
        engine.apply_snapshot([agent("w1:p1", "done", seq=2)])  # reconnect snapshot
        done = events(store, type="completion")
        self.assertEqual(len(done), 1)
        self.assertFalse(done[0]["seen_late"])
        clock.advance(10)
        engine.apply_agent(agent("w1:p1", "idle", seq=3))  # viewed in Herdr
        self.assertIsNotNone(store.event(done[0]["id"])["resolved_at"])
        engine.apply_agent(agent("w1:p1", "working", seq=4))
        engine.apply_agent(agent("w1:p1", "done", seq=5))
        self.assertEqual(len(events(store, type="completion")), 2)

    def test_idle_is_never_blocked(self):
        engine, store, _ = make()
        engine.apply_snapshot([agent("w1:p1", "idle")], workspaces("w1"))
        view = engine.view()["agents"][0]
        self.assertEqual(view["kind"], "idle")
        self.assertEqual(store.recent_events(10), [])

    def test_blocked_waits_for_explain_and_classifies(self):
        engine, store, _ = make()
        engine.apply_snapshot([agent("w1:p1", "working")], workspaces("w1"))
        self.assertTrue(engine.apply_agent(agent("w1:p1", "blocked")))
        self.assertEqual(store.recent_events(10), [], "no event until the type is known")
        engine.set_blocked_rule("claude:s1", "bash_permission_prompt")
        [event] = store.recent_events(10)
        self.assertEqual(event["type"], "approval")
        self.assertEqual(event["evidence"]["rule"], "bash_permission_prompt")
        view = engine.view()["agents"][0]
        self.assertEqual(view["label"], "Approval needed")

    def test_question_and_unclassified_blocked(self):
        engine, store, _ = make()
        engine.apply_snapshot([agent("w1:p1", "working", session="q"), agent("w1:p2", "working", session="u")],
                              workspaces("w1"))
        engine.apply_agent(agent("w1:p1", "blocked", session="q"))
        engine.set_blocked_rule("claude:q", "mcp_elicitation_prompt")
        engine.apply_agent(agent("w1:p2", "blocked", session="u"))
        engine.set_blocked_rule("claude:u", None)  # explain failed
        types = sorted(e["type"] for e in store.recent_events(10))
        self.assertEqual(types, ["blocked", "question"])

    def test_already_blocked_at_start_is_marked_seen_late(self):
        engine, store, _ = make()
        needs = engine.apply_snapshot([agent("w1:p1", "blocked")], workspaces("w1"))
        self.assertEqual(needs, [("claude:s1", "w1:p1")])
        engine.set_blocked_rule("claude:s1", "generic_permission_prompt")
        [event] = store.recent_events(10)
        self.assertTrue(event["seen_late"])
        self.assertFalse(engine.view()["agents"][0]["since_known"])


class Restarts(unittest.TestCase):
    def test_restart_with_same_state_creates_no_new_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state.db"
            clock = Clock()
            first, store, _ = make(store=Store(db), clock=clock)
            first.apply_snapshot([agent("w1:p1", "working", seq=10)], workspaces("w1"))
            first.apply_agent(agent("w1:p1", "done", seq=11))
            self.assertEqual(len(store.recent_events(10)), 1)
            store.close()

            clock.advance(3600)
            second, store2, _ = make(store=Store(db), clock=clock)
            second.apply_snapshot([agent("w1:p1", "done", seq=11)], workspaces("w1"))
            self.assertEqual(len(store2.recent_events(10)), 1, "no duplicate after restart")
            self.assertEqual(second.view()["agents"][0]["kind"], "completion")

    def test_change_during_gap_is_seen_late(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state.db"
            first, store, clock = make(store=Store(db))
            first.apply_snapshot([agent("w1:p1", "working", seq=10)], workspaces("w1"))
            store.close()
            clock.advance(600)
            second, store2, _ = make(store=Store(db), clock=clock)
            second.apply_snapshot([agent("w1:p1", "done", seq=14)], workspaces("w1"))
            [event] = store2.recent_events(10)
            self.assertEqual(event["type"], "completion")
            self.assertTrue(event["seen_late"])

    def test_agent_gone_during_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state.db"
            first, store, clock = make(store=Store(db))
            first.apply_snapshot([agent("w1:p1", "working", session="busy"), agent("w1:p2", "idle", session="calm")],
                                 workspaces("w1"))
            store.close()
            second, store2, _ = make(store=Store(db), clock=clock)
            second.apply_snapshot([], workspaces("w1"))
            [event] = store2.recent_events(10)
            self.assertEqual((event["type"], event["identity"], event["seen_late"]), ("failure", "claude:busy", True))


class Identity(unittest.TestCase):
    def test_session_id_identifies_the_agent_across_panes(self):
        engine, store, _ = make()
        engine.apply_snapshot([agent("w1:p1", "working", session="abc")], workspaces("w1"))
        engine.apply_agent(agent("w1:p1", "done", session="abc"))
        engine.apply_snapshot([agent("w1:p9", "done", session="abc")])  # pane moved
        view = engine.view()["agents"]
        self.assertEqual(len(view), 1)
        self.assertEqual(view[0]["pane_id"], "w1:p9")
        self.assertEqual(len(store.recent_events(10)), 1)

    def test_new_session_in_same_pane_is_a_new_agent(self):
        engine, store, _ = make()
        engine.apply_snapshot([agent("w1:p1", "working", session="old")], workspaces("w1"))
        engine.apply_agent(agent("w1:p1", "idle", session="new"))
        old = engine.agents["claude:old"]
        self.assertFalse(old["present"])
        self.assertEqual(old["kind"], "gone", "replaced sessions are not failures")
        self.assertEqual(store.recent_events(10), [])

    def test_history_follows_when_herdr_learns_the_session_id(self):
        engine, store, _ = make()
        engine.apply_snapshot([agent("w1:p1", "working", session=None, terminal_id="t1")], workspaces("w1"))
        engine.apply_agent(agent("w1:p1", "done", session=None, terminal_id="t1"))
        engine.apply_agent(agent("w1:p1", "done", session="late-id", terminal_id="t1"))
        [event] = store.recent_events(10)
        self.assertEqual(event["identity"], "claude:late-id")
        self.assertEqual(identity_of(agent(session=None, terminal_id="t1")), "term:t1")


class Failures(unittest.TestCase):
    def test_gone_while_working_is_a_failure(self):
        engine, store, _ = make()
        engine.apply_snapshot([agent("w1:p1", "working")], workspaces("w1"))
        engine.pane_gone("w1:p1", "process exited")
        [event] = store.recent_events(10)
        self.assertEqual(event["type"], "failure")
        self.assertEqual(event["confidence"], "low")
        self.assertEqual(event["evidence"]["reason"], "process exited")

    def test_gone_while_idle_is_not(self):
        engine, store, _ = make()
        engine.apply_snapshot([agent("w1:p1", "idle")], workspaces("w1"))
        engine.pane_gone("w1:p1", "pane closed")
        self.assertEqual(store.recent_events(10), [])
        self.assertEqual(engine.view()["agents"], [])


class Stalls(unittest.TestCase):
    """Regression tests for the stall fix (docs/requirements-followup-stall-fix.md)."""

    def setUp(self):
        self.engine, self.store, self.clock = make(settings(stall={"minutes": 15, "output_poll_seconds": 60}))
        self.engine.apply_snapshot([agent("w1:p1", "idle")], workspaces("w1"))
        self.engine.apply_agent(agent("w1:p1", "working"))

    def check(self, fingerprint, minutes=1):
        self.clock.advance(minutes * 60)
        self.engine.record_output("claude:s1", fingerprint)

    def kind(self):
        self.engine.tick()
        return self.engine.agents["claude:s1"]["kind"]

    def test_unchanged_content_across_the_threshold_is_a_possible_stall(self):
        for _ in range(17):
            self.check("same")
        self.assertEqual(self.kind(), "stall")
        [event] = self.store.recent_events(5)
        self.assertEqual(event["confidence"], "medium")
        self.assertGreaterEqual(event["evidence"]["output_checks"], 3)
        self.assertEqual(event["evidence"]["output_unchanged_minutes"], 15, "raised as soon as 15 min were observed")

    def test_status_alone_is_never_a_stall(self):
        self.clock.advance(3600)
        self.assertEqual(self.kind(), "working")
        engine, store, clock = make(settings(stall={"minutes": 15, "output_poll_seconds": 0}))
        engine.apply_snapshot([agent("w1:p1", "working")], workspaces("w1"))
        clock.advance(3600)
        engine.tick()
        self.assertEqual(store.recent_events(5), [], "no output checks, no stall")

    def test_failed_busy_read_means_unknown_not_stall(self):
        for _ in range(10):
            self.check("same")
        self.clock.advance(60)
        self.engine.record_output_error("claude:s1", "agent_not_idle")
        self.clock.advance(3600)
        self.assertEqual(self.kind(), "working")
        evidence = self.engine.agents["claude:s1"]["attention"]["evidence"]
        self.assertIn("unavailable (agent_not_idle)", evidence["output_check"])
        self.assertEqual(self.store.recent_events(5), [])

    def test_substantive_progress_prevents_the_flag(self):
        for i in range(30):
            self.check("same" if i % 10 else f"progress {i}")
        self.assertEqual(self.kind(), "working")

    def test_a_single_fresh_snapshot_is_not_enough(self):
        self.clock.advance(20 * 60)
        self.check("first look")
        self.assertEqual(self.kind(), "working")

    def test_stale_evidence_does_not_flag(self):
        for _ in range(17):
            self.check("same")
        self.clock.advance(10 * 60)        # no checks for 10 minutes (collector busy or Herdr gone)
        self.assertEqual(self.kind(), "working")

    def test_errors_restart_and_disconnect_reset_the_baseline(self):
        for _ in range(10):
            self.check("same")
        self.engine.record_output_error("claude:s1", "io")
        for _ in range(10):
            self.check("same")
        self.assertEqual(self.kind(), "working", "the error reset the evidence: only 10 min observed since")
        self.engine.set_herdr_status(False, error="gone")
        self.engine.set_herdr_status(True)
        for _ in range(10):
            self.check("same")
        self.assertEqual(self.kind(), "working")

    def test_session_turnover_resets(self):
        for _ in range(10):
            self.check("same")
        self.engine.apply_agent(agent("w1:p1", "working", session="s2"))
        for _ in range(10):
            self.engine.record_output("claude:s2", "same")
            self.clock.advance(60)
        self.engine.tick()
        self.assertEqual(self.engine.agents["claude:s2"]["kind"], "working")

    def test_recovery_retires_the_stall_flag_but_not_real_questions(self):
        self.engine.apply_snapshot([agent("w1:p1", "working"), agent("w1:p2", "working", session="q")])
        self.engine.apply_agent(agent("w1:p2", "blocked", session="q"))
        self.engine.set_blocked_rule("claude:q", "mcp_elicitation_prompt")
        for _ in range(17):
            self.check("same")
        self.kind()
        stall = next(e for e in self.store.recent_events(10) if e["type"] == "stall")
        self.check("moving again")
        self.assertIsNotNone(self.store.event(stall["id"])["resolved_at"])
        question = next(e for e in self.store.recent_events(10) if e["type"] == "question")
        self.assertIsNone(question["resolved_at"])
        self.assertIsNone(question["acked_at"])

    def test_per_workspace_threshold(self):
        engine, store, clock = make(settings(stall={"minutes": 15, "per_workspace": {"w1": 60}, "output_poll_seconds": 60}))
        engine.apply_snapshot([agent("w1:p1", "idle")], workspaces("w1"))
        engine.apply_agent(agent("w1:p1", "working"))
        for _ in range(30):
            clock.advance(60)
            engine.record_output("claude:s1", "same")
        engine.tick()
        self.assertEqual(store.recent_events(10), [])

    def test_collector_records_busy_reads_from_the_visible_screen(self):
        from herdr_attention.collector import Collector
        from tests.helpers import FakeHerdr
        herdr = FakeHerdr([agent("w1:p1", "working")], workspaces("w1"))
        herdr.busy.add("w1:p1")
        herdr.screens["w1:p1"] = "Working (3h 15m 29s • esc to interrupt)\n⠋ running tests"
        collector = Collector(db_path=":memory:", client=herdr, settings=settings(stall={"output_poll_seconds": 60}))
        collector.handle_snapshot()
        collector.poll_output()
        a = collector.engine.agents["claude:s1"]
        self.assertIsNotNone(a.get("output_hash"))
        self.assertNotIn("output_error", a)
        self.assertIn(("read", "w1:p1", "visible"), herdr.calls)
        self.assertTrue(collector.read_output("claude:s1")["source"].startswith("visible screen (agent busy)"))
        herdr.broken.add("w1:p1")
        collector.engine.agents["claude:s1"]["output_checked_at"] = 0
        collector.poll_output()
        self.assertEqual(collector.engine.agents["claude:s1"]["output_error"], "io")
        self.assertIn("Could not read", collector.read_output("claude:s1")["note"])

    def test_timer_and_spinner_changes_alone_are_not_progress(self):
        from herdr_attention.sanitize import fingerprint
        a = fingerprint("Working (3h 15m 29s • esc to interrupt)\n⠋ 12.3k tokens")
        b = fingerprint("Working (3h 16m 02s • esc to interrupt)\n⠙ 12.9k tokens")
        self.assertEqual(a, b)


class Acknowledge(unittest.TestCase):
    def test_ack_moves_item_out_of_attention(self):
        engine, store, _ = make()
        engine.apply_snapshot([agent("w1:p1", "working")], workspaces("w1"))
        engine.apply_agent(agent("w1:p1", "done"))
        view = engine.view()
        self.assertEqual(view["attention_count"], 1)
        self.assertTrue(engine.ack(view["agents"][0]["event_id"]))
        view = engine.view()
        self.assertEqual(view["attention_count"], 0)
        self.assertTrue(view["agents"][0]["acked"])
        self.assertEqual(store.recent_events(1)[0]["notify_state"], "acked")

    def test_cannot_ack_unwatched_events(self):
        conf = settings()
        engine, store, _ = make(conf)
        engine.apply_snapshot([agent("w1:p1", "working")], workspaces("w1"))
        engine.apply_agent(agent("w1:p1", "done"))
        event_id = store.recent_events(1)[0]["id"]
        conf.set_override("watch.enabled_workspaces", [])
        self.assertFalse(engine.ack(event_id))


class Notifiability(unittest.TestCase):
    def test_disabled_types_and_muted_workspaces_are_recorded_but_not_pending(self):
        conf = settings(events={"completion": False}, watch={"enabled_workspaces": ["w1", "w2"], "muted_workspaces": ["w2"]})
        engine, store, _ = make(conf)
        engine.apply_snapshot([agent("w1:p1", "working", session="a"), agent("w2:p1", "working", session="b")],
                              workspaces("w1", "w2"))
        engine.apply_agent(agent("w1:p1", "done", session="a"))
        engine.pane_gone("w2:p1", "process exited")
        states = {e["type"]: e["notify_state"] for e in store.recent_events(10)}
        self.assertEqual(states, {"completion": "off", "failure": "muted"})


if __name__ == "__main__":
    unittest.main()


class SidebarPositions(unittest.TestCase):
    def test_cards_preserve_native_positions_and_display_number_fallbacks(self):
        engine,store,_=make()
        self.addCleanup(store.close)
        info=agent('w1:p1',session='position')
        info.update(workspace_order=7,tab_order=8,pane_order=0,number=9)
        engine.set_tabs([{'tab_id':'w1:t1','workspace_id':'w1','number':3}])
        engine.apply_snapshot([info],[{'workspace_id':'w1','number':2}])
        card,=engine.view()['agents']
        self.assertEqual((card['workspace_order'],card['tab_order'],card['pane_order']),(7,8,0))
        self.assertEqual((card['workspace_number'],card['tab_number'],card['pane_number']),(2,3,9))
        for field in ('workspace_order','tab_order','pane_order','number'):info.pop(field)
        engine.apply_agent(info)
        card,=engine.view()['agents']
        self.assertIsNone(card['pane_order'])
        self.assertIsNone(card['pane_number'])
        self.assertEqual((card['workspace_number'],card['tab_number']),(2,3))
