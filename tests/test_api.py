"""HTTP API: local socket and phone server (real servers, fake Herdr)."""

import http.client
import json
import os
import shutil
import tempfile
import unittest

from herdr_attention.api import LocalServer, PhoneServer, make_handler, serve
from herdr_attention.cli import Client
from herdr_attention.collector import Collector
from tests.helpers import FakeHerdr, FakeKeyring, agent, settings, workspaces

PASSPHRASE = "correct horse battery"


def make_collector(**conf) -> Collector:
    conf.setdefault("watch", {"enabled_workspaces": ["w1"]})
    conf.setdefault("phone", {"cookie_secure": False})
    herdr = FakeHerdr([agent("w1:p1", "working", session="a", name="alpha", title="Visible task"),
                       agent("w2:p1", "blocked", session="b", name="hidden-agent", title="HIDDEN")],
                      workspaces("w1", "w2"))
    herdr.outputs["w1:p1"] = "\x1b[32mbuild ok\x1b[0m\npassword=hunter2\n"
    collector = Collector(db_path=":memory:", client=herdr, settings=settings(**conf))
    collector.keyring = FakeKeyring({"dashboard-passphrase": PASSPHRASE})
    collector.handle_snapshot()
    herdr.set(agent("w1:p1", "done", session="a", name="alpha", title="Visible task"))
    collector.handle_event({"event": "pane.agent_status_changed", "data": {"pane_id": "w1:p1", "workspace_id": "w1"}})
    return collector


class LocalApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hat-", dir=os.environ.get("XDG_RUNTIME_DIR") or "/tmp")
        self.collector = make_collector()
        self.path = os.path.join(self.tmp, "api.sock")
        self.server = LocalServer(self.path, make_handler(self.collector, local=True))
        serve(self.server)
        self.client = Client(self.path)

    def tearDown(self):
        self.server.stopping.set()
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_socket_is_private(self):
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.tmp).st_mode & 0o777, 0o700)

    def test_view_hides_unwatched_agents_but_lists_workspaces_for_settings(self):
        view = self.client.get("/api/view")
        self.assertEqual([a["name"] for a in view["agents"]], ["alpha"])
        self.assertNotIn("HIDDEN", json.dumps(view["agents"]))
        self.assertEqual([w["id"] for w in view["workspaces"]], ["w1"])
        self.assertEqual({w["id"]: w["watched"] for w in view["chooser"]}, {"w1": True, "w2": False})
        self.assertFalse(view["features"]["steering"])
        self.assertTrue(view["features"]["reply"])

    def test_output_is_cleaned_and_redacted(self):
        out = self.client.get("/api/output?identity=claude%3Aa")
        self.assertEqual(out["text"], "build ok\n[redacted]")
        self.assertEqual(out["redactions"], 1)
        with self.assertRaises(RuntimeError):
            self.client.get("/api/output?identity=claude%3Ab")  # unwatched

    def test_settings_presence_and_ack(self):
        self.client.post("/api/settings", {"stall.minutes": 25, "watch.muted_workspaces": ["w1"]})
        self.assertEqual(self.collector.settings.get("stall.minutes"), 25)
        self.assertEqual(self.collector.store.load_settings()["stall.minutes"], 25)
        with self.assertRaises(RuntimeError):
            self.client.post("/api/settings", {"notify.ntfy_url": "https://elsewhere.test"})
        self.client.post("/api/presence", {"state": "idle", "idle_seconds": 40})
        self.assertEqual(self.client.get("/api/view")["presence"]["state"], "idle")
        event_id = self.client.get("/api/view")["agents"][0]["event_id"]
        self.client.post(f"/api/events/{event_id}/ack")
        self.assertEqual(self.client.get("/api/view")["attention_count"], 0)

    def test_agent_detail_and_chooser(self):
        detail = self.client.get("/api/agent?identity=claude%3Aa")
        self.assertEqual(detail["agent"]["state"], "done")
        self.assertEqual(len(self.client.get("/api/chooser")["workspaces"]), 2)
        with self.assertRaises(RuntimeError):
            self.client.get("/api/agent?identity=claude%3Ab")

    def test_explain_never_runs_anything(self):
        result = self.client.post("/api/explain", {"identity": "claude:a"})
        self.assertFalse(result["available"])

    def test_no_steering_routes(self):
        for path in ("/api/prompt", "/api/send_keys", "/api/agents/claude:a/input", "/api/focus"):
            with self.assertRaises(RuntimeError):
                self.client.post(path, {"text": "rm -rf /"})

    def test_inference_check_is_local_read_only_and_rejects_unwatched_refs(self):
        before = self.collector.store._db.total_changes
        check = self.client.get('/api/inference/check')
        self.assertEqual(check['links'], [])
        self.assertEqual(self.collector.store._db.total_changes, before)
        with self.assertRaises(RuntimeError):
            self.client.get('/api/inference/check?ignore_declared=claude%3Ab')

    def test_local_launch_hooks_walls_and_invalid_payloads(self):
        import uuid
        run_id = str(uuid.uuid4())
        body = {'run_id': run_id, 'kind': 'claude', 'parent_session': 'claude:parent', 'parent_pane': 'w1:p9'}
        self.client.post('/api/launches', body)
        self.client.post('/api/hooks', {'run_id': run_id, 'session': 'claude:a', 'event': 'SessionStart',
                                      'parent_session': 'claude:parent', 'parent_pane': 'w1:p9'})
        self.assertEqual(self.collector.observations.card('claude:a')['run_id'], run_id)
        for bad in ({}, {'run_id': run_id, 'session': 'claude:a', 'event': 'Unknown'}):
            with self.assertRaises(RuntimeError):
                self.client.post('/api/hooks', bad)
        self.collector.observations.wall('claude', 'claude:a')
        self.assertEqual(self.client.get('/api/routing/walls')['walls'][0]['provider'], 'claude')
        self.client.post('/api/routing/clear', {'provider': 'claude'})
        self.assertEqual(self.client.get('/api/routing/walls')['walls'], [])

    def test_layout_check_matches_view_is_scoped_and_does_not_mutate(self):
        before = self.collector.store.open_events()
        data = self.client.get('/api/layout?workspace=w1')
        self.assertEqual(data['findings'], self.client.get('/api/view')['layout']['findings'])
        with self.assertRaises(RuntimeError):
            self.client.get('/api/layout?workspace=w2')
        self.assertNotIn('hidden-agent', json.dumps(data))
        self.assertEqual(self.collector.store.open_events(), before)


    def test_reply_requires_confirm_and_does_not_use_old_prompt_path(self):
        from tests.test_replies import FakePrompt
        sender = FakePrompt()
        self.collector.replies._sender = sender
        preview = self.client.post("/api/replies", {"identity": "claude:a", "text": "please ship it", "confirm": False})
        self.assertTrue(preview["preview"])
        self.assertEqual(sender.calls, [])
        sent = self.client.post("/api/replies", {"identity": "claude:a", "text": "please ship it",
                                                "quote": "build ok", "confirm": True, "when": "now"})
        self.assertEqual(sent["status"], "sent")
        self.assertEqual(sender.calls[0][0], "w1:p1")
        self.assertIn("please ship it", sender.calls[0][1])
        detail = self.client.get("/api/agent?identity=claude%3Aa")
        self.assertTrue(any(a["kind"] == "reply" and a["status"] == "sent" for a in detail["activity"]))

    def test_confirmed_reply_marks_its_notification_read(self):
        from tests.test_replies import FakePrompt
        self.collector.replies._sender = FakePrompt()
        acked = []
        self.collector.ack = lambda event_id: acked.append(event_id) or True
        preview = {"identity": "claude:a", "text": "ok", "confirm": False, "event_id": 7}
        self.client.post("/api/replies", preview)
        self.assertEqual(acked, [], "a preview marks nothing read")
        sent = self.client.post("/api/replies", {**preview, "confirm": True, "when": "now"})
        self.assertEqual((sent["status"], sent["acked"], acked), ("sent", True, [7]))
        self.collector.settings.set_override("ui.acknowledge", False)
        self.client.post("/api/replies", {**preview, "confirm": True, "when": "now"})
        self.assertEqual(acked, [7], "respects ui.acknowledge")

    def test_worker_reply_api_returns_coordinator_and_tracks_queued_delivery(self):
        from tests.test_replies import FakePrompt
        sender=FakePrompt();self.collector.replies._sender=sender
        herdr=self.collector.client
        herdr.set(agent('w1:p2','idle',session='worker',name='worker'))
        self.collector.handle_snapshot()
        self.collector.relation_action({'action':'link','subject':'claude:worker','kind':'reports_to','object':'claude:a'},local=True)
        # Fresh native state wins over an older idle/done snapshot.
        herdr.set(agent('w1:p1','working',session='a',name='alpha'))
        item=self.client.post('/api/replies',{'identity':'claude:worker','recipient_identity':'claude:a',
            'text':'Use the smaller change','confirm':True,'when':'auto'})
        self.assertEqual((item['status'],item['identity'],item['recipient']['name']),('queued','claude:a','alpha'))
        self.assertEqual(sender.calls,[])
        herdr.set(agent('w1:p1','idle',session='a',name='alpha'))
        self.collector.flush_replies()
        history=self.client.get('/api/replies?identity=claude%3Aa')['replies']
        self.assertEqual(history[0]['status'],'sent')
        self.assertEqual(sender.calls,[('w1:p1','Use the smaller change')])

    def test_stream_sends_a_view(self):
        conn = http.client.HTTPConnection("localhost")
        conn.sock = __import__("socket").socket(__import__("socket").AF_UNIX)
        conn.sock.settimeout(5)
        conn.sock.connect(self.path)
        conn.request("GET", "/api/stream")
        response = conn.getresponse()
        first = json.loads(response.readline())
        self.assertIn("agents", first)
        conn.close()


class PhoneApi(unittest.TestCase):
    def setUp(self):
        self.collector = make_collector(phone={"cookie_secure": False, "allowed_origins": ["https://laptop.example.ts.net"]})
        self.server = PhoneServer(("127.0.0.1", 0), make_handler(self.collector, local=False))
        self.port = self.server.server_address[1]
        self.thread = serve(self.server)
        self.origin = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.server.stopping.set()
        self.server.shutdown()
        self.server.server_close()

    def request(self, method, path, body=None, cookie=None, csrf=None, origin="same"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if cookie:
            headers["Cookie"] = cookie
        if csrf:
            headers["X-CSRF-Token"] = csrf
        if origin == "same":
            headers["Origin"] = self.origin
        elif origin:
            headers["Origin"] = origin
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        response = conn.getresponse()
        raw = response.read()
        conn.close()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = raw.decode()
        return response.status, dict(response.getheaders()), data

    def login(self):
        status, headers, data = self.request("POST", "/login", {"passphrase": PASSPHRASE})
        self.assertEqual(status, 200)
        cookie = headers["Set-Cookie"].split(";")[0]
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", headers["Set-Cookie"])
        return cookie, data["csrf"]

    def test_hook_launch_and_quota_admin_are_local_only(self):
        cookie, csrf = self.login()
        for path in ('/api/hooks', '/api/launches', '/api/launches/bind', '/api/routing/clear'):
            self.assertEqual(self.request('POST', path, {}, cookie=cookie, csrf=csrf)[0], 404, path)
        self.assertEqual(self.request('GET', '/api/routing/walls', cookie=cookie)[0], 404)

    def test_everything_needs_login(self):
        for path in ("/api/view", "/api/history", "/api/settings", "/api/output?identity=claude%3Aa", "/api/health",
                      "/api/notify/setup"):
            self.assertEqual(self.request("GET", path)[0], 401, path)
        self.assertEqual(self.request("POST", "/api/events/1/ack", {})[0], 401)
        self.assertEqual(self.request("POST", "/api/notify/test", {})[0], 401)

    def test_quota_refresh_requires_login_and_csrf(self):
        from unittest.mock import patch
        self.assertEqual(self.request("POST", "/api/quotas/refresh", {})[0], 401)
        cookie, csrf = self.login()
        self.assertEqual(self.request("POST", "/api/quotas/refresh", {}, cookie=cookie)[0], 403)
        with patch.object(self.collector, "refresh_quotas", return_value={"ok": True}) as refresh:
            self.assertEqual(self.request("POST", "/api/quotas/refresh", {}, cookie=cookie, csrf=csrf)[0], 200)
            refresh.assert_called_once()

    def test_page_is_public_but_locked_down(self):
        status, headers, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertIn("Your agents, at a glance", body)

    def test_wrong_passphrase_and_rate_limit(self):
        for _ in range(5):
            self.assertEqual(self.request("POST", "/login", {"passphrase": "nope"})[0], 401)
        self.assertEqual(self.request("POST", "/login", {"passphrase": PASSPHRASE})[0], 429)

    def test_login_from_other_site_is_refused(self):
        status, _, _ = self.request("POST", "/login", {"passphrase": PASSPHRASE}, origin="https://evil.test")
        self.assertEqual(status, 403)

    def test_view_never_contains_unwatched_workspaces(self):
        cookie, _ = self.login()
        status, _, view = self.request("GET", "/api/view", cookie=cookie)
        self.assertEqual(status, 200)
        blob = json.dumps(view)
        for leak in ("w2", "hidden-agent", "HIDDEN", "label-w2"):
            self.assertNotIn(leak, blob)
        self.assertNotIn("chooser", view)
        _, _, history = self.request("GET", "/api/history", cookie=cookie)
        self.assertTrue(all(e["workspace_id"] == "w1" for e in history["events"]))

    def test_changes_need_csrf_token_and_origin(self):
        cookie, csrf = self.login()
        event_id = self.collector.view(local=False)["agents"][0]["event_id"]
        path = f"/api/events/{event_id}/ack"
        self.assertEqual(self.request("POST", path, {}, cookie=cookie)[0], 403, "no token")
        self.assertEqual(self.request("POST", path, {}, cookie=cookie, csrf="wrong")[0], 403, "bad token")
        self.assertEqual(self.request("POST", path, {}, cookie=cookie, csrf=csrf, origin="https://evil.test")[0], 403)
        self.assertEqual(self.request("POST", path, {}, cookie=cookie, csrf=csrf, origin=None)[0], 403, "no origin")
        self.assertEqual(self.request("POST", path, {}, cookie=cookie, csrf=csrf,
                                      origin="https://laptop.example.ts.net")[0], 200)

    def test_phone_settings_are_limited(self):
        cookie, csrf = self.login()
        status, _, data = self.request("POST", "/api/settings", {"stall.per_workspace": {"w1": 30}},
                                       cookie=cookie, csrf=csrf)
        self.assertEqual(status, 400)
        self.assertIn("laptop", data["error"])
        status, _, _ = self.request("POST", "/api/settings", {"presence.mode": "always"}, cookie=cookie, csrf=csrf)
        self.assertEqual(status, 200)

    def test_local_only_routes_are_absent(self):
        cookie, csrf = self.login()
        self.assertEqual(self.request("POST", "/api/presence", {"state": "active"}, cookie=cookie, csrf=csrf)[0], 404)
        self.assertEqual(self.request("GET", "/api/stream", cookie=cookie)[0], 404)
        self.assertEqual(self.request("POST", "/api/settings/reset", {}, cookie=cookie, csrf=csrf)[0], 404)

    def test_agent_detail_needs_login_and_a_watched_workspace(self):
        self.assertEqual(self.request("GET", "/api/agent?identity=claude%3Aa")[0], 401)
        cookie, _ = self.login()
        status, _, detail = self.request("GET", "/api/agent?identity=claude%3Aa", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(detail["agent"]["name"], "alpha")
        self.assertEqual(detail["relationships"], "unknown")
        self.assertTrue(detail["activity"])
        self.assertEqual(self.request("GET", "/api/agent?identity=claude%3Ab", cookie=cookie)[0], 404)

    def test_chooser_lists_labels_only(self):
        self.assertEqual(self.request("GET", "/api/chooser")[0], 401)
        cookie, _ = self.login()
        _, _, data = self.request("GET", "/api/chooser", cookie=cookie)
        self.assertEqual([(w["id"], w["watched"]) for w in data["workspaces"]], [("w1", True), ("w2", False)])
        blob = json.dumps(data)
        for leak in ("hidden-agent", "HIDDEN", "blocked"):
            self.assertNotIn(leak, blob)

    def test_phone_can_switch_workspaces_on_and_off_with_csrf(self):
        cookie, csrf = self.login()
        body = {"watch.enabled_workspaces": ["w1", "w2"]}
        self.assertEqual(self.request("POST", "/api/settings", body, cookie=cookie)[0], 403, "needs the CSRF token")
        self.assertEqual(self.request("POST", "/api/settings", body, cookie=cookie, csrf=csrf)[0], 200)
        self.assertTrue(self.collector.settings.watched("w2"))
        self.assertEqual(self.request("POST", "/api/settings", {"watch.enabled_workspaces": []},
                                      cookie=cookie, csrf=csrf)[0], 200)
        self.assertFalse(self.collector.settings.watched("w1"))
        _, _, view = self.request("GET", "/api/view", cookie=cookie)
        self.assertEqual(view["agents"], [])

    def test_declaring_relationships_needs_csrf_and_is_manual(self):
        cookie, csrf = self.login()
        body = {"action": "set", "subject": "alpha", "kind": "role", "value": "coordinator", "registered": True}
        self.assertEqual(self.request("POST", "/api/relations", body, cookie=cookie)[0], 403)
        status, _, data = self.request("POST", "/api/relations", body, cookie=cookie, csrf=csrf)
        self.assertEqual(status, 200)

        self.assertEqual(data["roles"]["role"][0]["provenance"], "manual", "the phone can never claim 'registered'")
        self.assertEqual(data["roles"]["role"][0]["actor"], "ui")
        status, _, data = self.request("POST", "/api/relations", {"action": "link", "subject": "alpha", "kind": "reports_to",
                                                                   "object": "alpha"}, cookie=cookie, csrf=csrf)
        self.assertEqual(status, 400)

    def test_dry_inference_override_is_not_exposed_on_phone(self):
        cookie, _ = self.login()
        self.assertEqual(self.request('GET', '/api/inference/check?ignore_declared=alpha', cookie=cookie)[0], 404)

    def test_logout_ends_session(self):
        cookie, csrf = self.login()
        self.assertEqual(self.request("POST", "/logout", {}, cookie=cookie, csrf=csrf)[0], 200)
        self.assertEqual(self.request("GET", "/api/view", cookie=cookie)[0], 401)


    def _configure_ntfy(self):
        notify = self.collector.settings.file_values.setdefault("notify", {})
        notify.update({"backend": "ntfy", "enabled": True,
                       "ntfy_url": "https://omarchy.example.ts.net:8443",
                       "ntfy_topic": "unit-secret-topic",
                       "dashboard_url": "https://omarchy.example.ts.net"})
        self.collector.settings._rebuild()
        self.collector.keyring = FakeKeyring({
            "dashboard-passphrase": PASSPHRASE,
            "ntfy-token": "tk_test",
            "ntfy-phone-password": "phone-pass",
        })

    def test_notify_setup_is_login_only_and_view_hides_the_topic(self):
        status, _, data = self.request("GET", "/api/notify/setup")
        self.assertEqual(status, 401)
        self.assertNotIn("unit-secret-topic", json.dumps(data) if not isinstance(data, str) else data)
        self._configure_ntfy()
        cookie, csrf = self.login()
        status, _, setup = self.request("GET", "/api/notify/setup", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(setup["topic"], "unit-secret-topic")
        self.assertEqual(setup["deep_link"], "ntfy://omarchy.example.ts.net:8443/unit-secret-topic")
        self.assertEqual(setup["https_link"], "https://omarchy.example.ts.net:8443/unit-secret-topic")
        self.assertEqual(setup["password"], "phone-pass")
        _, _, view = self.request("GET", "/api/view", cookie=cookie)
        _, _, settings_data = self.request("GET", "/api/settings", cookie=cookie)
        for blob in (view, settings_data):
            self.assertNotIn("unit-secret-topic", json.dumps(blob))
            self.assertNotIn("phone-pass", json.dumps(blob))
        status, _, result = self.request("POST", "/api/notify/test", {}, cookie=cookie, csrf=csrf)
        self.assertEqual(status, 200)
        # No live ntfy in this test; the sender is not ready without a reachable token check...
        # Token is present but this process's sender was built at collector init (backend none).
        # The test endpoint builds a fresh sender; without a real server it may be "ready"
        # (url+topic+token) and then fail to connect. Either way the topic must stay out.
        self.assertNotIn("unit-secret-topic", json.dumps(result))
        self.assertIn("ok", result)

    def test_phone_can_toggle_ntfy(self):
        cookie, csrf = self.login()
        status, _, _ = self.request("POST", "/api/settings", {"notify.enabled": False}, cookie=cookie, csrf=csrf)
        self.assertEqual(status, 200)
        self.assertFalse(self.collector.settings.get("notify.enabled"))


    def test_reply_needs_login_and_csrf(self):
        from tests.test_replies import FakePrompt
        sender = FakePrompt()
        self.collector.replies._sender = sender
        body = {"identity": "claude:a", "text": "hi there", "confirm": True, "when": "now"}
        self.assertEqual(self.request("POST", "/api/replies", body)[0], 401)
        cookie, csrf = self.login()
        self.assertEqual(self.request("POST", "/api/replies", body, cookie=cookie, csrf="wrong")[0], 403)
        self.assertEqual(self.request("POST", "/api/replies", body, cookie=cookie, csrf=csrf,
                                      origin="https://evil.test")[0], 403)
        status, _, preview = self.request("POST", "/api/replies",
                                          {"identity": "claude:a", "text": "hi there", "confirm": False},
                                          cookie=cookie, csrf=csrf)
        self.assertEqual(status, 200)
        self.assertTrue(preview["preview"])
        self.assertEqual(sender.calls, [])
        status, _, sent = self.request("POST", "/api/replies", body, cookie=cookie, csrf=csrf)
        self.assertEqual(status, 200)
        self.assertEqual(sent["status"], "sent")
        self.assertEqual(sender.calls[0][1], "hi there")

    def test_no_passphrase_configured(self):
        self.collector.keyring = FakeKeyring({})
        self.assertEqual(self.request("POST", "/login", {"passphrase": "anything"})[0], 503)


if __name__ == "__main__":
    unittest.main()
