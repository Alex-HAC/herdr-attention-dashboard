"""Notification gate: send/skip/fallback, no live model calls."""

import json
import unittest

from herdr_attention.notify import Notifier
from herdr_attention.notify_gate import NotifyGate, format_message, dashboard_links
from herdr_attention.presence import Presence
from herdr_attention.store import Store
from tests.helpers import Clock, RecordingSender, agent, settings, workspaces
from tests.test_notify import Rig


def gate_reply(send=True, title="Needs your approval", body="The cart agent is waiting.", do="Approve or reject it.", reason="needs a decision"):
    return json.dumps({"send": send, "title": title, "body": body, "do": do, "reason": reason})


class Links(unittest.TestCase):
    def test_deep_links_use_the_tailnet_dashboard(self):
        conf = settings(notify={"dashboard_url": "https://laptop.example-tailnet.ts.net"})
        links = dashboard_links([{"id": 1, "identity": "codex:abc"}], conf, ["https://github.com/ex/repo/pull/4"])
        urls = [l["url"] for l in links]
        self.assertIn("https://laptop.example-tailnet.ts.net/#/a/codex%3Aabc", urls)
        self.assertIn("https://github.com/ex/repo/pull/4", urls)
        msg = format_message("Title", "What happened.", "Have a look", links)
        self.assertIn("Open: https://laptop.example-tailnet.ts.net/#/a/codex%3Aabc", msg["message"])
        self.assertIn("PR: https://github.com/ex/repo/pull/4", msg["message"])


class Decisions(unittest.TestCase):
    def test_skip_does_not_send_and_is_logged(self):
        calls = []

        def runner(command, prompt, env, timeout):
            calls.append({"command": command, "prompt": prompt})
            return gate_reply(send=False, title="", body="", do="", reason="routine progress, no ask")

        rig = Rig(summaries={"codex_personal_home": "/tmp"}, notify_gate={"enabled": True, "account": "codex-personal"})
        # The Rig disabled the gate by default; turn it on and attach a fake runner.
        rig.settings.set_override  # noqa: keep reference
        from tests.helpers import settings as make_settings
        # Recreate gate on the existing notifier
        rig.notifier.gate = NotifyGate(rig.store, rig.settings, rig.clock, runner=runner)
        rig.notifier.gate.settings.values["notify_gate"]["enabled"] = True
        # Force enabled via file_values
        rig.settings.file_values.setdefault("notify_gate", {})["enabled"] = True
        rig.settings._rebuild()
        rig.notifier.gate.settings = rig.settings
        self.assertTrue(rig.notifier.gate.enabled)

        rig.idle(400)
        rig.finish("w1:p1", "a")
        # First tick queues the gate instead of sending
        self.assertIsNone(rig.notifier.tick())
        self.assertEqual(rig.sender.sent, [])
        job = rig.notifier.gate.next_job()
        self.assertIsNotNone(job)
        # No real home/binary: invoke would fail. Drive run_job by stubbing invoke path:
        # Use a prepare that provides text, and a runner — invoke still needs a binary + home.
        # Instead, finish through a hand-crafted decision to test skip delivery,
        # and test run_job parse via _finish after mocking invoke at the module level.
        from herdr_attention import notify_gate as ng

        def fake_invoke(*a, **k):
            return json.loads(gate_reply(send=False, title="", body="", reason="routine progress, no ask")), {"usage": {"input_tokens": 20, "output_tokens": 8}}

        orig = ng.invoke
        ng.invoke = fake_invoke
        try:
            decision = rig.notifier.finish_gate_job(job)
        finally:
            ng.invoke = orig
        self.assertEqual(decision["decision"], "skip")
        self.assertEqual(rig.sender.sent, [])
        self.assertIn("gated_skip", rig.states())
        log = rig.notifier.gate_status()["recent"]
        self.assertEqual(log[0]["decision"], "skip")
        self.assertIn("routine progress", log[0]["reason"])

    def test_failure_falls_back_to_the_plain_notification(self):
        rig = Rig(notify_gate={"enabled": True})
        rig.notifier.gate = NotifyGate(rig.store, rig.settings, rig.clock)
        rig.settings.file_values.setdefault("notify_gate", {})["enabled"] = True
        rig.settings._rebuild()
        rig.notifier.gate.settings = rig.settings
        rig.idle(400)
        rig.finish("w1:p1", "a")
        self.assertIsNone(rig.notifier.tick())
        job = rig.notifier.gate.next_job()
        from herdr_attention import notify_gate as ng

        def boom(*a, **k):
            raise ValueError("timed out")

        orig = ng.invoke
        ng.invoke = boom
        try:
            decision = rig.notifier.finish_gate_job(job)
        finally:
            ng.invoke = orig
        self.assertEqual(decision["decision"], "fallback")
        # Events return to pending so the next tick sends the plain alert.
        self.assertTrue(any(s in ("pending", "held") for s in rig.states()))
        sent = rig.notifier.tick()
        self.assertIsNotNone(sent)
        self.assertEqual(len(rig.sender.sent), 1)
