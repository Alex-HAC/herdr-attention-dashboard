"""Confirmed replies: preview, queue, never auto-send, no prompt while blocked."""

import unittest

from herdr_attention.herdr import HerdrError
from herdr_attention.reply import HerdrPrompt, ReplyService
from herdr_attention.store import Store
from tests.helpers import Clock, settings


class FakePrompt:
    def __init__(self):
        self.calls = []
        self.fail = None

    def __call__(self, target, text):
        if self.fail:
            exc, self.fail = self.fail, None
            raise exc
        self.calls.append((target, text))
        return {"ok": True}


def agent(**extra):
    base = {"identity": "codex:solo", "name": "solo", "pane_id": "w1:p1",
            "status": "idle", "present": True, "label": "Idle"}
    base.update(extra)
    return base


def service(sender=None, clock=None):
    clock = clock or Clock()
    svc = ReplyService(Store(":memory:"), settings(), clock=clock, sender=sender or FakePrompt())
    return svc, clock, svc._sender


class PreviewAndSafety(unittest.TestCase):
    def test_empty_or_huge_text_is_rejected(self):
        svc, _, _ = service()
        with self.assertRaises(ValueError):
            svc.preview(agent(), "")
        self.assertTrue(svc.preview(agent(), "x" * 9000)["too_long"])
        with self.assertRaises(ValueError):
            svc.submit(agent(), text="x" * 9000, quote=None, when="now", interrupt=False, confirm=True)

    def test_working_defaults_to_queue_and_warns(self):
        svc, _, _ = service()
        prev = svc.preview(agent(status="working"), "please continue")
        self.assertEqual(prev["default_when"], "when_waiting")
        self.assertTrue(prev["can_send_now"])
        self.assertIn("interrupt", prev["warning"])

    def test_blocked_cannot_send_now(self):
        svc, _, _ = service()
        prev = svc.preview(agent(status="blocked"), "yes")
        self.assertFalse(prev["can_send_now"])
        self.assertEqual(prev["default_when"], "when_waiting")
        with self.assertRaises(ValueError):
            svc.submit(agent(status="blocked"), text="yes", quote=None, when="now",
                       interrupt=True, confirm=True)

    def test_gone_cannot_send(self):
        svc, _, _ = service()
        prev = svc.preview(agent(present=False, pane_id=None), "hi")
        self.assertFalse(prev["can_send_now"])

    def test_confirm_is_required(self):
        svc, _, sender = service()
        with self.assertRaises(ValueError) as ctx:
            svc.submit(agent(), text="hello", quote=None, when="now", interrupt=False, confirm=False)
        self.assertIn("confirm", str(ctx.exception))
        self.assertEqual(sender.calls, [])


class SendAndQueue(unittest.TestCase):
    def test_send_now_when_idle(self):
        svc, _, sender = service()
        item = svc.submit(agent(), text="please ship it", quote="PR is ready",
                          when="now", interrupt=False, confirm=True)
        self.assertEqual(item["status"], "sent")
        self.assertEqual(len(sender.calls), 1)
        target, text = sender.calls[0]
        self.assertEqual(target, "w1:p1")
        self.assertIn("please ship it", text)
        self.assertIn("> PR is ready", text)
        trail = svc.store.activity("codex:solo")
        self.assertTrue(any(a["kind"] == "reply" and a["status"] == "sent" for a in trail))

    def test_working_now_without_interrupt_is_blocked(self):
        svc, _, sender = service()
        with self.assertRaises(ValueError):
            svc.submit(agent(status="working"), text="stop", quote=None,
                       when="now", interrupt=False, confirm=True)
        self.assertEqual(sender.calls, [])

    def test_working_now_with_interrupt_sends(self):
        svc, _, sender = service()
        item = svc.submit(agent(status="working"), text="new direction", quote=None,
                          when="now", interrupt=True, confirm=True)
        self.assertEqual(item["status"], "sent")
        self.assertEqual(sender.calls[0][1], "new direction")

    def test_queue_until_waiting(self):
        svc, _, sender = service()
        item = svc.submit(agent(status="working"), text="when you are free", quote=None,
                          when="when_waiting", interrupt=False, confirm=True)
        self.assertEqual(item["status"], "queued")
        self.assertEqual(sender.calls, [])
        live = {"codex:solo": agent(status="working")}
        self.assertEqual(svc.tick(live.get), [])
        self.assertEqual(sender.calls, [])
        live["codex:solo"] = agent(status="idle")
        sent = svc.tick(live.get)
        self.assertEqual(sent[0]["status"], "sent")
        self.assertEqual(sender.calls[0][1], "when you are free")

    def test_blocked_prompt_stays_queued(self):
        sender = FakePrompt()
        sender.fail = HerdrError("agent.prompt: agent_blocked: blocked", code="agent_blocked")
        # Agent became idle according to us, but Herdr still rejects.
        svc, _, _ = service(sender)
        item = svc.submit(agent(status="idle"), text="hello", quote=None,
                          when="now", interrupt=False, confirm=True)
        self.assertEqual(item["status"], "queued")
        self.assertEqual(item["when_policy"], "when_waiting")
        self.assertEqual(sender.calls, [])

    def test_cancel_queued(self):
        svc, _, sender = service()
        item = svc.submit(agent(status="working"), text="later", quote=None,
                          when="when_waiting", interrupt=False, confirm=True)
        cancelled = svc.cancel(item["id"], "codex:solo")
        self.assertEqual(cancelled["status"], "cancelled")
        svc.tick(lambda _: agent(status="idle"))
        self.assertEqual(sender.calls, [])

    def test_read_only_client_still_refuses_prompt(self):
        from herdr_attention.herdr import HerdrClient
        client = HerdrClient("/tmp/does-not-exist.sock")
        with self.assertRaises(HerdrError):
            client.call("agent.prompt", {"target": "w1:p1", "text": "nope"})
        # The write client exists as a separate type.
        self.assertTrue(hasattr(HerdrPrompt, "prompt"))
