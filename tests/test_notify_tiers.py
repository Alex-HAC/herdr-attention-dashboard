"""Notification tiers, presets, quiet hours and close-watch pins."""

import unittest

from herdr_attention import notify_tiers as nt
from herdr_attention.notify import Notifier
from tests.helpers import RecordingSender, agent, settings
from tests.test_notify import Rig


class Presets(unittest.TestCase):
    def test_normal_is_the_default(self):
        s = settings()
        self.assertEqual(nt.current_preset(s), "normal")
        self.assertTrue(nt.tier_config(s, "waiting_on_you")["enabled"])
        self.assertTrue(nt.tier_config(s, "might_look")["enabled"])
        self.assertTrue(nt.tier_config(s, "fyi_finished")["enabled"])
        self.assertFalse(nt.tier_config(s, "waiting_on_coordinator")["enabled"])
        self.assertFalse(nt.tier_config(s, "coordinator_milestone")["enabled"])
        self.assertFalse(nt.tier_config(s, "everything")["enabled"])
        self.assertEqual(nt.tier_config(s, "waiting_on_you")["gate"], "always")

    def test_preset_sets_toggles_and_a_manual_edit_becomes_custom(self):
        s = settings()
        for key, value in nt.apply_preset("quiet").items():
            s.set_override(key, value)
        self.assertEqual(nt.current_preset(s), "quiet")
        self.assertTrue(nt.tier_config(s, "waiting_on_you")["enabled"])
        self.assertFalse(nt.tier_config(s, "fyi_finished")["enabled"])
        patch = nt.expand_changes(s, {"notify_tiers.fyi_finished": True})
        self.assertEqual(patch["notify_tiers.preset"], "custom")
        s.set_override("notify_tiers.fyi_finished", True)
        s.set_override("notify_tiers.preset", "custom")
        self.assertEqual(nt.current_preset(s), "custom")

    def test_close_tabs_and_everything_presets(self):
        close = nt.apply_preset("close-tabs")
        self.assertTrue(close["notify_tiers.coordinator_milestone"])
        self.assertFalse(close["notify_tiers.everything"])
        every = nt.apply_preset("everything")
        self.assertTrue(every["notify_tiers.everything"])


class LockedTier(unittest.TestCase):
    def test_stored_false_is_treated_as_on(self):
        s = settings()
        s.set_override("notify_tiers.waiting_on_you", False)
        self.assertTrue(nt.tier_config(s, "waiting_on_you")["enabled"])
        self.assertEqual(nt.current_preset(s), "normal")

    def test_request_to_switch_off_is_refused_with_a_reason(self):
        s = settings()
        with self.assertRaises(ValueError) as caught:
            s.validate("notify_tiers.waiting_on_you", False)
        self.assertIn("always on", str(caught.exception))
        self.assertTrue(s.validate("notify_tiers.waiting_on_you", True))

    def test_public_view_marks_the_tier_locked(self):
        view = nt.public_view(settings())
        tier = next(t for t in view["tiers"] if t["id"] == "waiting_on_you")
        self.assertTrue(tier["locked"])
        self.assertTrue(tier["enabled"])
        self.assertEqual(tier["hint"], "Approvals and direct questions. Always sent; the AI may only reword it.")


class Classification(unittest.TestCase):
    def test_approval_is_waiting_on_you(self):
        s = settings()
        self.assertEqual(nt.classify_event({"type": "approval"}, settings=s), "waiting_on_you")
        self.assertEqual(nt.classify_event({"type": "failure"}, settings=s), "might_look")
        self.assertEqual(nt.classify_event({"type": "completion"}, settings=s), "fyi_finished")
        self.assertEqual(nt.classify_event({"type": "status"}, settings=s), "everything")

    def test_worker_completion_is_waiting_on_the_coordinator(self):
        s = settings()
        event = {"type": "completion", "identity": "codex:w"}
        links = [{"kind": "reports_to", "from": "codex:w", "to": "claude:c", "to_name": "coord"}]
        self.assertEqual(nt.classify_event(event, settings=s, links=links), "waiting_on_coordinator")

    def test_merged_pr_is_a_coordinator_milestone(self):
        s = settings()
        event = {"type": "completion", "identity": "codex:w",
                 "title": "PR #178 merged; registry check remains"}
        self.assertEqual(nt.classify_event(event, settings=s), "coordinator_milestone")
        # A live approval ask still wins over milestone wording.
        event = {"type": "approval", "title": "PR #12 merged, please approve the next one?"}
        self.assertEqual(nt.classify_event(event, settings=s), "waiting_on_you")


class QuietHoursAndPins(unittest.TestCase):
    def test_quiet_hours_hold_everything_but_waiting_on_you(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        now = datetime(2026, 11, 15, 23, 0, tzinfo=ZoneInfo("Europe/Amsterdam")).timestamp()
        s = settings(notify_tiers={"quiet_hours": True, "quiet_start": "22:00", "quiet_end": "08:00"})
        self.assertTrue(nt.in_quiet_hours(s, now))
        self.assertIsNone(nt.allowed_now(s, "waiting_on_you", "claude:a", now))
        self.assertEqual(nt.allowed_now(s, "fyi_finished", "claude:a", now), "quiet")
        # 0.9.8.9: a close-watch pin no longer skips quiet hours (it sent 97 of 137 pushes in a day).
        s.set_override("notify_tiers.watch_closely", ["claude:a"])
        self.assertEqual(nt.allowed_now(s, "fyi_finished", "claude:a", now), "quiet")

    def test_disabled_tier_is_off_unless_pinned(self):
        s = settings()
        self.assertEqual(nt.allowed_now(s, "waiting_on_coordinator", "codex:w", 1_000), "off")
        s.set_override("notify_tiers.watch_closely", ["codex:w"])
        self.assertIsNone(nt.allowed_now(s, "waiting_on_coordinator", "codex:w", 1_000))


class Estimates(unittest.TestCase):
    def test_quiet_estimates_fewer_gate_checks_than_normal(self):
        s = settings()
        events = ([{"type": "approval", "at": 1_000, "evidence": {}}] * 3
                  + [{"type": "completion", "at": 1_000, "evidence": {}}] * 5
                  + [{"type": "stall", "at": 1_000, "evidence": {}}] * 2)
        out = nt.estimate_tokens(events, s, now=1_000 + 60, days=7)
        self.assertEqual(out["presets"]["quiet"]["gate_checks"], 3)
        self.assertEqual(out["presets"]["normal"]["gate_checks"], 10)
        self.assertIn("AI check", out["presets"]["normal"]["hint"])

    def test_estimates_use_reports_to_so_worker_completions_are_not_normal(self):
        s = settings()
        events = [{"type": "completion", "at": 1_000, "identity": "codex:w", "evidence": {}}] * 5
        links = [{"kind": "reports_to", "from": "codex:w", "to": "claude:c", "to_name": "coord"}]
        out = nt.estimate_tokens(events, s, now=1_000 + 60, days=7, links=links)
        self.assertEqual(out["by_tier"].get("waiting_on_coordinator"), 5)
        self.assertEqual(out["presets"]["normal"]["gate_checks"], 0)
        self.assertEqual(out["presets"]["close_tabs"]["gate_checks"], 5)


class Sending(unittest.TestCase):
    def test_worker_completion_is_not_sent_on_the_normal_preset(self):
        rig = Rig(presence={"mode": "always"})
        worker = agent("w1:p2", "working", session="b", name="beta")
        rig.engine.apply_agent(worker)
        # Declare reports_to via a manual-style link in relations if available;
        # otherwise classify from stored evidence after we stamp the event.
        rig.finish("w1:p2", "b")
        events = rig.store.recent_events(5)
        completion = next(e for e in events if e["type"] == "completion" and "beta" in (e.get("identity") or "codex:b") or e["type"] == "completion")
        # Without a reports_to link this is FYI and should still send (default on).
        rig.notifier.tick()
        self.assertEqual(len(rig.sender.sent), 1)
        self.assertIn("Finished:", rig.sender.sent[0]["title"])

    def test_waiting_on_coordinator_stays_off_until_enabled(self):
        rig = Rig(presence={"mode": "always"})
        event_id = rig.store.add_event(
            at=rig.clock.now, identity="codex:b", workspace_id="w1", pane_id="w1:p2",
            type="completion", evidence={"notify_tier": "waiting_on_coordinator"},
            confidence="high", seen_late=False, notify_state="pending", title=None)
        rig.notifier.tick()
        self.assertEqual(rig.sender.sent, [])
        self.assertEqual(rig.store.event(event_id)["notify_state"], "off")
        rig.settings.set_override("notify_tiers.waiting_on_coordinator", True)
        rig.settings.set_override("notify_tiers.preset", "custom")
        rig.notifier.tick()
        self.assertEqual(len(rig.sender.sent), 1)
        self.assertIn("Waiting on a coordinator", rig.sender.sent[0]["title"])

    def test_quiet_hours_hold_fyi_but_send_waiting_on_you(self):
        # Force "now" inside quiet hours regardless of the clock's epoch.
        s = settings(presence={"mode": "always"},
                     notify_gate={"enabled": False},
                     notify_tiers={"quiet_hours": True, "quiet_start": "00:00", "quiet_end": "23:59"})
        rig = Rig(presence={"mode": "always"}, notify_gate={"enabled": False},
                  notify_tiers={"quiet_hours": True, "quiet_start": "00:00", "quiet_end": "23:59"})
        # The helpers.settings merge may not have applied notify_tiers above if Rig
        # already built settings — set overrides explicitly.
        rig.settings.set_override("notify_tiers.quiet_hours", True)
        rig.settings.set_override("notify_tiers.quiet_start", "00:00")
        rig.settings.set_override("notify_tiers.quiet_end", "23:59")
        self.assertTrue(nt.in_quiet_hours(rig.settings, rig.clock.now))
        rig.finish("w1:p1", "a")
        rig.notifier.tick()
        self.assertEqual(rig.sender.sent, [])
        self.assertEqual(rig.store.recent_events(1)[0]["notify_state"], "quiet")
        rig.engine.apply_agent(agent("w1:p3", "blocked", session="c", name="gamma"))
        rig.engine.set_blocked_rule("claude:c", "bash_permission_prompt", "Allow this command?\n1. Yes\n2. No")
        rig.notifier.tick()
        self.assertEqual(len(rig.sender.sent), 1)
        self.assertIn("Waiting on you", rig.sender.sent[0]["title"])

    def test_tier_1_gate_skip_still_sends(self):
        import json
        from herdr_attention import notify_gate as ng
        from herdr_attention.notify_gate import NotifyGate
        from tests.test_notify_gate import gate_reply

        rig = Rig(presence={"mode": "always"}, notify_gate={"enabled": True})
        rig.settings.set_override("notify_gate.enabled", True)
        rig.notifier.gate = NotifyGate(rig.store, rig.settings, rig.clock)
        rig.engine.apply_agent(agent("w1:p3", "blocked", session="c", name="gamma"))
        rig.engine.set_blocked_rule("claude:c", "bash_permission_prompt", "Allow this command?\n1. Yes\n2. No")
        self.assertIsNone(rig.notifier.tick())  # queued for the gate
        job = rig.notifier.gate.next_job()
        self.assertIsNotNone(job)

        def fake_invoke(*a, **k):
            return json.loads(gate_reply(send=False, title="", body="", reason="routine")), {"usage": {"output_tokens": 8}}

        orig = ng.invoke
        ng.invoke = fake_invoke
        try:
            decision = rig.notifier.finish_gate_job(job)
        finally:
            ng.invoke = orig
        self.assertEqual(decision["decision"], "skip")
        rig.notifier.tick()
        self.assertEqual(len(rig.sender.sent), 1, "Waiting on you must not be skipped by the AI gate")
        self.assertIn("Waiting on you", rig.sender.sent[0]["title"])

    def test_dashboard_delivery_does_not_push(self):
        rig = Rig(presence={"mode": "always"})
        rig.settings.set_override("notify_tiers.fyi_finished_delivery", "dashboard")
        rig.finish("w1:p1", "a")
        out = rig.notifier.tick()
        self.assertEqual(rig.sender.sent, [])
        self.assertEqual(rig.store.recent_events(1)[0]["notify_state"], "dashboard")
        self.assertEqual(out["kind"], "dashboard")

    def test_test_alert_uses_the_tier_format(self):
        sender = RecordingSender()
        from herdr_attention.presence import Presence
        from herdr_attention.store import Store
        n = Notifier(settings(notify={"backend": "ntfy", "enabled": True}),
                     None, Presence(settings()), sender, dict)
        # send_test checks sender.ready(); RecordingSender is ready.
        n.send_test()
        self.assertEqual(sender.sent[0]["title"], "Waiting on you (test)")
        self.assertIn("notification-tier format", sender.sent[0]["message"])
        self.assertIn("Phone (ntfy)", sender.sent[0]["message"])


if __name__ == "__main__":
    unittest.main()


class OneVocabulary(unittest.TestCase):
    """notify_tiers.py is the only copy of tier and preset wording (v0.8.1)."""

    def test_table_order_and_labels(self):
        self.assertEqual([nt.TIER_LABELS[t] for t in nt.NOTIFY_TIERS], [
            "Waiting on you", "Might want a look", "Finished", "Waiting on a coordinator",
            "Milestones", "Every status change"])
        self.assertEqual([nt.PRESET_LABELS[p] for p in nt.PRESETS], ["Only me", "Normal", "More", "All", "Custom"])
        view = nt.public_view(settings())
        self.assertEqual([t["id"] for t in view["tiers"]], list(nt.NOTIFY_TIERS))
        self.assertEqual(view["preset_hint"], nt.PRESET_HINTS["normal"])

    def test_attention_tiers_follow_the_table_order(self):
        from herdr_attention import attention
        order = [t for t in nt.NOTIFY_TIERS if t in attention.TIERS]
        self.assertEqual(list(attention.TIERS), order)

    def test_preset_sentences_have_no_tier_numbers(self):
        for hint in nt.PRESET_HINTS.values():
            self.assertNotRegex(hint, r"[Tt]iers? \\d")

    def test_no_hard_coded_copies_in_web_or_plugin(self):
        from pathlib import Path
        root = Path(__file__).parent.parent
        files = [root / "herdr_attention/web/app.js", root / "herdr_attention/web/graph.js",
                 root / "plugin/alexvdb.herdr-attention/Panel.qml"]
        stale = ["Close tabs", "Failures, errors and stalls", "FYI / finished", "Finished turns",
                 "Coordinator milestones", "Everything (verbose)", "When idle", "Always now",
                 "TIER_HEAD", "TIER_ORDER"]
        for path in files:
            text = path.read_text()
            for phrase in stale:
                self.assertNotIn(phrase, text, f"{phrase!r} in {path.name}")
            for label in nt.PRESET_HINTS.values():
                self.assertNotIn(label, text, f"preset sentence copied into {path.name}")
