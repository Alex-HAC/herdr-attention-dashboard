"""Phone alert gating and content (fixture data, fake sender)."""

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from herdr_attention.engine import Engine
from herdr_attention.notify import Notifier, NtfySender
from herdr_attention.presence import Presence
from herdr_attention.store import Store
from tests.helpers import Clock, FakeKeyring, RecordingSender, agent, settings, workspaces


class Rig:
    def __init__(self, sender=None, **conf):
        conf.setdefault("presence", {})
        conf["presence"].setdefault("phone_delay_minutes", 5)
        conf.setdefault("notify_gate", {})
        conf["notify_gate"].setdefault("enabled", False)
        self.clock = Clock()
        self.settings = settings(**conf)
        self.store = Store(":memory:")
        self.engine = Engine(self.settings, self.store, self.clock)
        self.presence = Presence(self.settings, self.clock)
        self.sender = sender or RecordingSender()
        self.notifier = Notifier(self.settings, self.store, self.presence, self.sender, self.names, self.clock)
        self.engine.apply_snapshot([agent("w1:p1", "working", session="a", name="alpha"),
                                    agent("w1:p2", "working", session="b", name="beta"),
                                    agent("w1:p3", "working", session="c", name="gamma", title="SECRET task title")],
                                   workspaces("w1"))

    def names(self):
        return {a["identity"]: {"name": a["name"], "agent": a["agent"], "workspace_label": a["workspace_label"]}
                for a in self.engine.view()["agents"]}

    NAMES = {"a": "alpha", "b": "beta", "c": "gamma"}

    def finish(self, pane, session):
        self.engine.apply_agent(agent(pane, "done", session=session, name=self.NAMES.get(session, "tester")))

    def active(self):
        self.idle_started = None
        self.presence.report("active")

    def idle(self, seconds):
        self.idle_started = self.clock.now - seconds
        self.presence.report("idle", idle_seconds=seconds)

    def wait(self, seconds):
        """Let time pass while the desktop plugin keeps sending heartbeats."""
        while seconds > 0:
            step = min(60, seconds)
            self.clock.advance(step)
            seconds -= step
            if getattr(self, "idle_started", None) is None:
                self.presence.report("active")
            else:
                self.presence.report("idle", idle_seconds=self.clock.now - self.idle_started)

    def states(self):
        return sorted(e["notify_state"] for e in self.store.recent_events(50))


class PresenceTests(unittest.TestCase):
    def test_idle_since_and_heartbeats(self):
        clock = Clock()
        p = Presence(settings(presence={"phone_delay_minutes": 5}), clock)
        self.assertEqual(p.state()["state"], "unknown")
        p.report("idle", idle_seconds=30)
        self.assertEqual(p.state()["state"], "idle")
        clock.advance(200)
        p.report("idle", idle_seconds=230)  # heartbeat must not move the start
        clock.advance(80)
        state = p.state()
        self.assertEqual(state["state"], "away")
        self.assertAlmostEqual(state["idle_seconds"], 310, delta=1)
        p.report("active")
        self.assertEqual(p.state()["state"], "present")

    def test_stale_reports_mean_unknown(self):
        clock = Clock()
        p = Presence(settings(presence={"heartbeat_stale_seconds": 180, "unknown_means": "present"}), clock)
        p.report("active")
        clock.advance(181)
        self.assertEqual(p.state(), {**p.state(), "state": "unknown", "counts_as": "present"})

    def test_rejects_other_states(self):
        with self.assertRaises(ValueError):
            Presence(settings()).report("typing")


class Gating(unittest.TestCase):
    def test_nothing_goes_out_while_active(self):
        rig = Rig()
        rig.active()
        rig.finish("w1:p1", "a")
        rig.notifier.tick()
        self.assertEqual(rig.sender.sent, [])
        self.assertEqual(rig.states(), ["held"])
        self.assertEqual(len(rig.engine.view()["agents"]), 3, "dashboard still shows everything")

    def test_alert_only_after_delay(self):
        rig = Rig()
        rig.idle(30)
        rig.finish("w1:p1", "a")
        rig.notifier.tick()
        self.assertEqual(rig.sender.sent, [])
        rig.wait(4 * 60 + 29)
        rig.notifier.tick()
        self.assertEqual(rig.sender.sent, [], "4m59s idle is not enough")
        rig.wait(2)
        rig.notifier.tick()
        self.assertEqual(len(rig.sender.sent), 1)
        self.assertEqual(rig.states(), ["sent"])

    def test_input_cancels_pending_alerts(self):
        rig = Rig()
        rig.idle(30)
        rig.finish("w1:p1", "a")
        rig.wait(120)
        rig.active()  # moved the mouse before the delay
        rig.notifier.tick()
        rig.wait(600)
        rig.notifier.tick()
        self.assertEqual(rig.sender.sent, [])
        self.assertEqual(rig.states(), ["held"])

    def test_going_away_sends_one_summary_then_live_alerts(self):
        rig = Rig()
        rig.active()
        rig.finish("w1:p1", "a")
        rig.finish("w1:p2", "b")
        rig.notifier.tick()
        rig.idle(6 * 60)
        rig.notifier.tick()
        self.assertEqual(len(rig.sender.sent), 1)
        self.assertEqual(rig.sender.sent[0]["title"], "Herdr: 2 to look at")
        self.assertEqual(rig.states(), ["summarized", "summarized"])
        rig.notifier.tick()
        self.assertEqual(len(rig.sender.sent), 1, "each event alerts once")
        rig.finish("w1:p3", "c")
        rig.notifier.tick()
        self.assertEqual(len(rig.sender.sent), 2)
        self.assertTrue(rig.sender.sent[1]["title"].startswith("Finished:"))

    def test_resolved_or_read_items_are_not_sent(self):
        rig = Rig()
        rig.active()
        rig.finish("w1:p1", "a")
        rig.finish("w1:p2", "b")
        rig.notifier.tick()
        rig.engine.apply_agent(agent("w1:p1", "idle", session="a"))  # looked at it in Herdr
        beta = next(a for a in rig.engine.view()["agents"] if a["name"] == "beta")
        rig.engine.ack(beta["event_id"])  # marked the other read
        rig.idle(600)
        rig.notifier.tick()
        self.assertEqual(rig.sender.sent, [])
        self.assertEqual(rig.states(), ["acked", "expired"])

    def test_override_always_and_never(self):
        rig = Rig(presence={"mode": "always"})
        rig.active()
        rig.finish("w1:p1", "a")
        rig.notifier.tick()
        self.assertEqual(len(rig.sender.sent), 1)

        rig = Rig(presence={"mode": "never"})
        rig.idle(3600)
        rig.finish("w1:p1", "a")
        rig.notifier.tick()
        self.assertEqual(rig.sender.sent, [])

    def test_unknown_presence_counts_as_configured(self):
        rig = Rig(presence={"unknown_means": "away"})
        rig.finish("w1:p1", "a")
        rig.notifier.tick()
        self.assertEqual(len(rig.sender.sent), 1)
        rig = Rig(presence={"unknown_means": "present"})
        rig.finish("w1:p1", "a")
        rig.notifier.tick()
        self.assertEqual(rig.sender.sent, [])

    def test_muted_and_disabled_never_sent(self):
        rig = Rig(watch={"muted_workspaces": ["w1"]}, presence={"mode": "always"})
        rig.finish("w1:p1", "a")
        rig.notifier.tick()
        self.assertEqual(rig.sender.sent, [])
        rig.settings.set_override("watch.muted_workspaces", [])
        rig.settings.set_override("events.completion", False)
        rig.finish("w1:p2", "b")
        rig.notifier.tick()
        self.assertEqual(rig.sender.sent, [])

    def test_rate_limit_folds_into_summary(self):
        rig = Rig(presence={"mode": "always"}, notify={"max_per_10min": 1})
        rig.finish("w1:p1", "a")
        rig.notifier.tick()
        rig.finish("w1:p2", "b")
        rig.finish("w1:p3", "c")
        rig.notifier.tick()
        self.assertEqual(len(rig.sender.sent), 1)
        rig.clock.advance(601)
        rig.notifier.tick()
        self.assertEqual(len(rig.sender.sent), 2)
        self.assertEqual(rig.sender.sent[1]["title"], "Herdr: 2 to look at")

    def test_summary_is_bounded(self):
        rig = Rig(presence={"mode": "always"}, notify={"summary_max_items": 2})
        for pane, session in (("w1:p1", "a"), ("w1:p2", "b"), ("w1:p3", "c")):
            rig.finish(pane, session)
        rig.notifier.tick()
        lines = rig.sender.sent[0]["message"].split("\n")
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[-1], "+1 more")

    def test_failed_send_is_retried_later_not_lost(self):
        sender = RecordingSender(fail=True)
        rig = Rig(sender=sender, presence={"mode": "always"})
        rig.finish("w1:p1", "a")
        rig.notifier.tick()
        self.assertEqual(rig.states(), ["pending"])
        self.assertIn("server down", rig.notifier.status()["last_error"])
        sender.fail = False
        rig.notifier.tick()
        self.assertEqual(sender.sent, [], "backs off before retrying")
        rig.clock.advance(31)
        rig.notifier.tick()
        self.assertEqual(len(sender.sent), 1)

    def test_no_backend_means_nothing_queued_forever(self):
        rig = Rig(sender=RecordingSender(ready=False), presence={"mode": "always"})
        rig.finish("w1:p1", "a")
        rig.notifier.tick()
        self.assertEqual(rig.states(), ["no_backend"])


class Content(unittest.TestCase):
    def test_metadata_only(self):
        rig = Rig(presence={"mode": "always"})
        rig.engine.apply_agent(agent("w1:p3", "blocked", session="c", name="gamma", title="SECRET task title"))
        rig.engine.set_blocked_rule("claude:c", "bash_permission_prompt", "Allow this command?\n1. Yes\n2. No")
        rig.notifier.tick()
        [message] = rig.sender.sent
        self.assertEqual(message["title"], "Waiting on you: gamma")
        self.assertEqual(message["message"], "gamma · label-w1 · Approval needed")
        self.assertEqual(message["priority"], "high")
        blob = json.dumps(message)
        for leak in ("SECRET", "/tmp/x", "bash_permission_prompt", "w1:p3"):
            self.assertNotIn(leak, blob)

    def test_click_links_to_dashboard(self):
        rig = Rig(presence={"mode": "always"}, notify={"dashboard_url": "https://laptop.example.ts.net"})
        rig.finish("w1:p1", "a")
        rig.notifier.tick()
        event_id = rig.store.recent_events(1)[0]["id"]
        self.assertEqual(rig.sender.sent[0]["click"], f"https://laptop.example.ts.net#/n/{event_id}")

    def test_seen_late_is_labelled(self):
        rig = Rig(presence={"mode": "always"})
        rig.engine.apply_snapshot([agent("w1:p1", "done", session="a", name="alpha")])  # found on resync
        rig.notifier.tick()
        self.assertIn("(seen late)", rig.sender.sent[0]["message"])


class NtfyRequest(unittest.TestCase):
    """The exact HTTP request sent to ntfy, against a local stand-in server."""

    def setUp(self):
        self.received = []
        test = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                test.received.append({"path": self.path, "auth": self.headers.get("Authorization"),
                                      "type": self.headers.get("Content-Type"), "body": json.loads(body)})
                status = 403 if self.headers.get("Authorization") != "Bearer tk_test" else 200
                self.send_response(status)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def sender(self, token="tk_test"):
        conf = settings(notify={"backend": "ntfy", "ntfy_url": self.url, "ntfy_topic": "herdr"})
        return NtfySender(conf, FakeKeyring({"ntfy-token": token} if token else {}))

    def test_json_publish_with_bearer_token(self):
        self.sender().send(title="Approval needed: gamma", message="gamma · web · Approval needed",
                           priority="high", tags=["warning"], click="https://dash.example#event-3")
        [req] = self.received
        self.assertEqual(req["auth"], "Bearer tk_test")
        self.assertEqual(req["type"], "application/json")
        self.assertEqual(req["body"], {"topic": "herdr", "title": "Approval needed: gamma",
                                       "message": "gamma · web · Approval needed", "priority": 4,
                                       "tags": ["warning"], "click": "https://dash.example#event-3"})

    def test_auth_failure_is_reported_without_the_token(self):
        with self.assertRaises(RuntimeError) as ctx:
            self.sender(token="tk_wrong").send(title="t", message="m", priority="default", tags=[], click="")
        self.assertIn("403", str(ctx.exception))
        self.assertNotIn("tk_wrong", str(ctx.exception))

    def test_not_ready_without_token_or_url(self):
        self.assertEqual(self.sender(token=None).ready()[0], False)
        self.assertEqual(NtfySender(settings(notify={"backend": "ntfy"}), FakeKeyring()).ready()[0], False)


class SetupLinks(unittest.TestCase):
    def test_topic_only_when_secrets_are_requested(self):
        from herdr_attention.notify import ntfy_setup
        conf = settings(notify={"backend": "ntfy", "enabled": True,
                                "ntfy_url": "https://omarchy.example.ts.net:8443",
                                "ntfy_topic": "unit-secret-topic",
                                "dashboard_url": "https://omarchy.example.ts.net"})
        hidden = ntfy_setup(conf, include_secrets=False)
        self.assertTrue(hidden["configured"])
        self.assertEqual(hidden["topic"], "")
        self.assertEqual(hidden["deep_link"], "")
        self.assertEqual(hidden["https_link"], "")
        self.assertNotIn("unit-secret-topic", json.dumps(hidden))
        shown = ntfy_setup(conf, include_secrets=True, phone_password="phone-pass")
        self.assertEqual(shown["topic"], "unit-secret-topic")
        self.assertEqual(shown["deep_link"], "ntfy://omarchy.example.ts.net:8443/unit-secret-topic")
        self.assertEqual(shown["https_link"], "https://omarchy.example.ts.net:8443/unit-secret-topic")
        self.assertEqual(shown["username"], "herdr-phone")
        self.assertEqual(shown["password"], "phone-pass")
        self.assertEqual(shown["server"], "https://omarchy.example.ts.net:8443")

    def test_disabled_sender_is_not_ready(self):
        from herdr_attention.notify import NtfySender, make_sender
        conf = settings(notify={"backend": "ntfy", "enabled": False,
                                "ntfy_url": "https://omarchy.example.ts.net:8443",
                                "ntfy_topic": "unit-secret-topic"})
        sender = make_sender(conf, FakeKeyring({"ntfy-token": "tk_test"}))
        self.assertEqual(sender.name, "none")
        ready, why = NtfySender(conf, FakeKeyring({"ntfy-token": "tk_test"})).ready()
        self.assertFalse(ready)
        self.assertIn("switched off", why)



if __name__ == "__main__":
    unittest.main()
