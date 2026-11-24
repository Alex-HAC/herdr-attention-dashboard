"""v0.9.4 Clean up: server-made preview, confirm re-checks, only `herdr pane close`, logged. Fake Herdr only."""
import io
import os
import subprocess
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from herdr_attention import cleanup as cu
from herdr_attention.collector import Collector
from herdr_attention.herdr import HerdrError
from tests.helpers import FakeHerdr, agent, settings, workspaces


class FakeProcs:
    def __init__(self, children=None, cmdlines=None, broken=()):
        self.kids, self.cmd, self.broken = children or {}, cmdlines or {}, set(broken)

    def children(self, pid):
        if pid in self.broken:
            raise OSError("gone")
        return self.kids.get(pid, [])

    def cmdline(self, pid):
        return self.cmd.get(pid, ["proc"])


class FakeCloser:
    def __init__(self):
        self.closed = []

    def close(self, pane_id):
        self.closed.append(pane_id)


def wait(service, job):
    deadline = time.monotonic() + 5
    while job["status"] == "running" and time.monotonic() < deadline:
        time.sleep(0.01)
        job = service.job(job["id"])
    return job


def shell(pane_id, ws=None, **extra):
    ws = ws or pane_id.split(":")[0]
    return dict({"pane_id": pane_id, "workspace_id": ws, "tab_id": f"{ws}:t9", "agent_status": "unknown", "focused": False,
                 "terminal_id": f"term_{pane_id}", "cwd": "/tmp/x", "foreground_cwd": "/tmp/x"}, **extra)


class Rig:
    """w1 is switched on; w2 is watched but off; w4 is never; w3 is not watched."""

    def __init__(self):
        self.herdr = FakeHerdr([
            agent("w1:p1", "working", session="done1", name="finisher"),
            agent("w1:p2", "working", session="idle1", name="idler"),
            agent("w1:p3", "working", session="busy", name="busy"),
            agent("w1:p4", "working", session="block", name="blocker"),
            agent("w1:p5", "idle", session="seen", name="looked-at"),
            agent("w1:p6", "idle", session="dirty", name="dirty-tree"),
            agent("w1:p7", "idle", session="job", name="with-job"),
            agent("w1:p8", "idle", session="mcp", name="with-mcp"),
            agent("w1:p9", "idle", session="gone", name="ended-agent"),
            agent("w1:pA", "idle", session="ask", name="asker"),
            agent("w1:pB", "idle", session="lead", name="lead"),
            agent("w1:pC", "working", session="worker", name="worker"),
            agent("w2:p1", "idle", session="off", name="off-ws"),
            agent("w4:p1", "idle", session="work", name="work"),
            agent("w3:p1", "idle", session="unw", name="unwatched"),
        ], workspaces("w1", "w2", "w3", "w4"))
        self.herdr.agents_by_pane["w1:p5"]["focused"] = True
        conf = settings(watch={"enabled_workspaces": ["w1", "w2", "w4"]}, cleanup={"workspaces": ["w1", "w4"]})
        self.collector = Collector(db_path=":memory:", client=self.herdr, settings=conf)
        self.collector.handle_snapshot()
        for pane, status in (("w1:p1", "done"), ("w1:p2", "idle"), ("w1:p4", "blocked")):
            self.change(pane, agent_status=status)
        # w1:p9's agent ended; its pane is a plain shell now.
        self.herdr.remove("w1:p9")
        self.collector.handle_snapshot()
        self.herdr.shell_panes = [shell("w1:p9"), shell("w1:pD"), shell("w1:pE"), shell("w2:p9"), shell("w4:p9")]
        self.herdr.process_infos["w1:pE"] = {"shell_pid": 50, "foreground_process_group_id": 51,
                                             "foreground_processes": [{"pid": 51, "name": "npm"}]}
        self.herdr.process_infos["w1:p7"] = {"shell_pid": 70, "foreground_process_group_id": 71, "foreground_processes": []}
        self.herdr.process_infos["w1:p8"] = {"shell_pid": 80, "foreground_process_group_id": 81, "foreground_processes": []}
        engine = self.collector.engine
        engine.set_last_assistant("claude:ask", "Should I merge the release branch now?", at=time.time())
        self.collector.relation_action({"action": "link", "subject": "claude:worker", "kind": "reports_to",
                                        "object": "claude:lead"}, local=True)
        self.git = {"/tmp/x": ("clean", 0), "/tmp/dirty": ("dirty", 3)}
        self.herdr.agents_by_pane["w1:p6"]["foreground_cwd"] = "/tmp/dirty"
        service = self.collector.cleanup
        service.git = lambda cwd: self.git.get(cwd, ("unknown", 0))
        service.procs = FakeProcs({70: [71], 71: [72], 80: [81], 81: [82], 82: [83]},
                                  {71: ["claude"], 72: ["/usr/bin/bash", "-c", "pytest -q"], 81: ["claude"],
                                   82: ["npm", "exec", "@modelcontextprotocol/server-pdf", "--stdio"],
                                   83: ["node", "/x/mcp-pdf-server"]})
        self.closer = service.closer = FakeCloser()
        self.service = service

    def change(self, pane, **fields):
        current = self.herdr.agent(pane)
        current.update(fields)
        current["state_change_seq"] += 1
        self.herdr.set(current)
        self.collector.handle_event({"event": "pane.agent_status_changed",
                                     "data": {"pane_id": pane, "workspace_id": pane.split(":")[0]}})

    def preview(self, workspace=None, caller=None):
        return wait(self.service, self.service.start_preview(workspace, who="local", caller_pane=caller))


class Preview(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rig = Rig()
        cls.job = cls.rig.preview()

    def reasons(self):
        rows = self.job["candidates"] + self.job["excluded"]
        return {r["pane_id"]: r["reason"] for r in rows}

    def test_preview_is_ready_and_closes_nothing(self):
        self.assertEqual(self.job["status"], "ready", self.job.get("error"))
        self.assertEqual(self.rig.closer.closed, [])

    def test_candidates_and_their_reasons(self):
        got = {r["pane_id"]: r["kind"] for r in self.job["candidates"]}
        self.assertEqual(got, {"w1:p2": "idle", "w1:p8": "idle", "w1:p9": "shell"})
        self.assertIn("ended-agent", self.reasons()["w1:p9"])

    def test_every_exclusion_has_a_short_reason(self):
        r = self.reasons()
        self.assertEqual(r["w1:p1"], cu.REASONS["unread"])          # done, finished turn not read yet
        self.assertEqual(r["w1:p3"], cu.REASONS["working"])
        self.assertEqual(r["w1:p4"], cu.REASONS["blocked"])
        self.assertEqual(r["w1:p5"], cu.REASONS["focused"])
        self.assertEqual(r["w1:p6"], cu.REASONS["dirty"].format(n=3))
        self.assertEqual(r["w1:p7"], cu.REASONS["job"].format(name="bash"))
        self.assertEqual(r["w1:pA"], cu.REASONS["question"])
        self.assertEqual(r["w1:pB"], cu.REASONS["coordinator"].format(n=1))
        self.assertEqual(r["w1:pD"], cu.REASONS["plain_shell"])
        self.assertEqual(r["w1:pE"], cu.REASONS["program"].format(name="npm"))

    def test_never_off_and_unwatched_workspaces_are_summarised(self):
        off = {w["workspace_id"]: w["reason"] for w in self.job["workspaces_off"]}
        self.assertEqual(off, {"w2": cu.REASONS["off"], "w4": cu.REASONS["never"], "w3": cu.REASONS["unwatched"]})
        listed = {r["pane_id"] for r in self.job["candidates"] + self.job["excluded"]}
        self.assertFalse({p for p in listed if not p.startswith("w1:")})

    def test_per_workspace_scope(self):
        job = self.rig.preview("w2")
        self.assertEqual((job["candidates"], job["excluded"]), ([], []))
        self.assertEqual([w["workspace_id"] for w in job["workspaces_off"]], ["w2"])


class Rules(unittest.TestCase):
    def test_read_makes_a_finished_agent_a_candidate(self):
        rig = Rig()
        event_id = next(a["event_id"] for a in rig.collector.view(local=False)["agents"] if a["pane_id"] == "w1:p1")
        rig.collector.ack(event_id)
        job = rig.preview()
        self.assertIn("w1:p1", {r["pane_id"] for r in job["candidates"]})

    def test_caller_pane_and_unknown_checks_are_not_candidates(self):
        rig = Rig()
        rig.herdr.process_infos["w1:p8"] = HerdrError("no")
        rig.git["/tmp/x"] = ("unknown", 0)
        job = rig.preview(caller="w1:p2")
        r = {x["pane_id"]: x["reason"] for x in job["excluded"]}
        self.assertEqual(job["candidates"], [])
        self.assertEqual(r["w1:p2"], cu.REASONS["caller"])
        self.assertEqual(r["w1:p8"], cu.REASONS["processes_unknown"])
        self.assertEqual(r["w1:p9"], cu.REASONS["git_unknown"])

    def test_unread_notice_of_an_ended_agent_blocks_its_shell(self):
        rig = Rig()
        rig.service.unread = lambda: [{"identity": "claude:gone", "pane_id": "w1:p9"}]
        job = rig.preview()
        self.assertNotIn("w1:p9", {r["pane_id"] for r in job["candidates"]})

    def test_never_workspace_cannot_be_switched_on(self):
        rig = Rig()
        with self.assertRaises(ValueError):
            rig.collector.update_settings({"cleanup.workspaces": ["w1", "w4"]}, local=True)
        self.assertEqual(rig.collector.settings.get("cleanup.workspaces"), ["w1", "w4"])  # file value, filtered:
        self.assertEqual(rig.service.allowed(), ["w1"])
        self.assertEqual(Collector(db_path=":memory:", client=FakeHerdr()).settings.get("cleanup.workspaces"), [])


class Confirm(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.job = self.rig.preview()
        self.service = self.rig.service

    def confirm(self, panes):
        return wait(self.service, self.service.start_confirm(self.job["preview_id"], panes, who="phone"))

    def test_closes_only_ticked_panes_that_still_pass(self):
        self.rig.change("w1:p2", agent_status="working")       # busy again since the preview
        result = self.confirm(["w1:p2", "w1:p9"])
        self.assertEqual(result["status"], "ready", result.get("error"))
        self.assertEqual(self.rig.closer.closed, ["w1:p9"])
        self.assertEqual([r["pane_id"] for r in result["closed"]], ["w1:p9"])
        self.assertEqual(result["skipped"], [{"pane_id": "w1:p2", "reason": cu.REASONS["working"]}])
        log = self.service.log()
        self.assertEqual({(e["pane_id"], e["result"], e["who"]) for e in log},
                         {("w1:p9", "closed", "phone"), ("w1:p2", "skipped", "phone")})
        self.assertTrue(all(e["at"] and e["reason"] for e in log))

    def test_only_panes_from_a_server_preview(self):
        for panes in (["w1:p3"], ["w4:p1"], [], ["w1:p2", "free-form"]):
            with self.assertRaises(ValueError, msg=panes):
                self.service.start_confirm(self.job["preview_id"], panes, who="local")
        with self.assertRaises(ValueError):
            self.service.start_confirm(999, ["w1:p2"], who="local")
        self.assertEqual(self.rig.closer.closed, [])

    def test_a_preview_is_used_once_and_expires(self):
        self.confirm(["w1:p2"])
        with self.assertRaises(ValueError):
            self.service.start_confirm(self.job["preview_id"], ["w1:p8"], who="local")
        job = self.rig.preview()
        with patch.object(self.service, "clock", lambda: time.time() + cu.PREVIEW_SECONDS + 1):
            with self.assertRaises(ValueError):
                self.service.start_confirm(job["preview_id"], ["w1:p8"], who="local")

    def test_a_replaced_pane_is_not_closed(self):
        self.rig.herdr.shell_panes[0]["terminal_id"] = "term_new"
        result = self.confirm(["w1:p9"])
        self.assertEqual(result["skipped"], [{"pane_id": "w1:p9", "reason": cu.REASONS["changed"]}])
        self.assertEqual(self.rig.closer.closed, [])

    def test_a_new_agent_in_a_shell_pane_is_not_closed(self):
        self.rig.herdr.shell_panes = [p for p in self.rig.herdr.shell_panes if p["pane_id"] != "w1:p9"]
        self.rig.herdr.set(agent("w1:p9", "idle", session="fresh", name="fresh"))
        self.rig.collector.handle_snapshot()
        result = self.confirm(["w1:p9"])
        self.assertEqual(result["skipped"], [{"pane_id": "w1:p9", "reason": cu.REASONS["changed"]}])
        self.assertEqual(self.rig.closer.closed, [])

    def test_viewed_since_the_preview_still_closes(self):
        job = self.rig.preview()
        self.rig.change("w1:p2", agent_status="done")   # same agent, a new turn ended and was read
        event_id = next(a["event_id"] for a in self.rig.collector.view(local=False)["agents"] if a["pane_id"] == "w1:p2")
        self.rig.collector.ack(event_id)
        result = wait(self.service, self.service.start_confirm(job["preview_id"], ["w1:p2"], who="local"))
        self.assertEqual(self.rig.closer.closed, ["w1:p2"], result)

    def test_injected_clients_never_close(self):
        self.service.closer = None   # what Collector does with a test or replay client
        result = self.confirm(["w1:p2"])
        self.assertEqual(result["closed"], [])
        self.assertEqual(result["failed"][0]["pane_id"], "w1:p2")
        self.assertEqual(self.service.log()[0]["result"], "failed")
        self.assertIsNone(Collector(db_path=":memory:", client=FakeHerdr()).cleanup.closer)

    def test_close_is_the_herdr_cli_and_nothing_else(self):
        with patch("subprocess.run") as run:
            cu.PaneCloser("/tmp/herdr.sock").close("w1:p9")
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["herdr", "pane", "close", "w1:p9"])
        self.assertEqual(kwargs["env"]["HERDR_SOCKET_PATH"], "/tmp/herdr.sock")
        self.assertTrue(kwargs["check"])


class Checks(unittest.TestCase):
    def test_process_state(self):
        quiet_shell = {"shell_pid": 1, "foreground_process_group_id": 1}
        self.assertEqual(cu.process_state(quiet_shell, FakeProcs(), agent=False), ("quiet", ""))
        self.assertEqual(cu.process_state(dict(quiet_shell, foreground_process_group_id=2,
                                               foreground_processes=[{"pid": 2, "name": "vim"}]), FakeProcs(), agent=False),
                         ("busy", "vim"))
        self.assertEqual(cu.process_state(quiet_shell, FakeProcs({1: [5]}, {5: ["sleep", "100"]}), agent=False)[0], "busy")
        agent_info = {"shell_pid": 1, "foreground_process_group_id": 2}
        self.assertEqual(cu.process_state(agent_info, FakeProcs({1: [2]}, {2: ["codex"]}), agent=True), ("quiet", ""))
        self.assertEqual(cu.process_state(agent_info, FakeProcs({1: [2], 2: [3]}, {2: ["codex"], 3: ["cargo", "build"]}),
                                          agent=True), ("busy", "cargo"))
        self.assertEqual(cu.process_state(agent_info, FakeProcs(broken=[1]), agent=True), ("unknown", ""))
        self.assertEqual(cu.process_state({}, FakeProcs(), agent=True), ("unknown", ""))

    def test_git_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(cu.git_state(tmp), ("none", 0))
            subprocess.run(["git", "init", "-q", tmp], check=True)
            self.assertEqual(cu.git_state(tmp), ("clean", 0))
            for name in ("a.txt", "b.txt"):
                open(os.path.join(tmp, name), "w").close()
            self.assertEqual(cu.git_state(tmp), ("dirty", 2))
            self.assertFalse(os.path.exists(os.path.join(tmp, ".git", "index.lock")))
        self.assertEqual(cu.git_state(os.path.join(tmp, "missing")), ("unknown", 0))
        self.assertEqual(cu.git_state(None), ("unknown", 0))


class PhoneRoutes(unittest.TestCase):
    """Same login, CSRF and Origin checks as every other write route."""
    from tests.test_api import PhoneApi
    request, login = PhoneApi.request, PhoneApi.login

    def setUp(self):
        from herdr_attention.api import PhoneServer, make_handler, serve
        from tests.test_api import PASSPHRASE
        from tests.helpers import FakeKeyring
        self.rig = Rig()
        self.rig.collector.keyring = FakeKeyring({"dashboard-passphrase": PASSPHRASE})
        self.rig.collector.settings.set_override("phone.cookie_secure", False)
        self.server = PhoneServer(("127.0.0.1", 0), make_handler(self.rig.collector, local=False))
        self.port = self.server.server_address[1]
        serve(self.server)
        self.origin = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.server.stopping.set()
        self.server.shutdown()
        self.server.server_close()

    def ready(self, cookie, job):
        deadline = time.monotonic() + 5
        while job["status"] == "running" and time.monotonic() < deadline:
            time.sleep(0.02)
            job = self.request("GET", f"/api/cleanup?id={job['id']}", cookie=cookie)[2]
        return job

    def test_preview_and_confirm_need_login_and_csrf(self):
        for path in ("/api/cleanup/preview", "/api/cleanup/confirm"):
            self.assertEqual(self.request("POST", path, {})[0], 401, path)
        self.assertEqual(self.request("GET", "/api/cleanup")[0], 401)
        cookie, csrf = self.login()
        self.assertEqual(self.request("POST", "/api/cleanup/preview", {}, cookie=cookie)[0], 403)
        self.assertEqual(self.request("POST", "/api/cleanup/preview", {}, cookie=cookie, csrf=csrf,
                                      origin="https://evil.example")[0], 403)
        status, _, job = self.request("POST", "/api/cleanup/preview", {"caller_pane": "w1:p2"}, cookie=cookie, csrf=csrf)
        self.assertEqual(status, 200)
        job = self.ready(cookie, job)
        # The phone cannot name a caller pane; w1:p2 is still a candidate.
        self.assertIn("w1:p2", {r["pane_id"] for r in job["candidates"]})
        body = {"preview_id": job["preview_id"], "panes": ["w1:p2"], "confirm": True}
        self.assertEqual(self.request("POST", "/api/cleanup/confirm", body, cookie=cookie)[0], 403)
        self.assertEqual(self.request("POST", "/api/cleanup/confirm", dict(body, confirm=False), cookie=cookie, csrf=csrf)[0], 400)
        self.assertEqual(self.request("POST", "/api/cleanup/confirm", dict(body, panes=["w1:p3"]), cookie=cookie, csrf=csrf)[0], 409)
        self.assertEqual(self.request("POST", "/api/cleanup/confirm", dict(body, preview_id=True), cookie=cookie, csrf=csrf)[0], 400)
        self.assertEqual(self.rig.closer.closed, [])
        status, _, done = self.request("POST", "/api/cleanup/confirm", body, cookie=cookie, csrf=csrf)
        self.assertEqual(status, 200)
        done = self.ready(cookie, done)
        self.assertEqual(self.rig.closer.closed, ["w1:p2"])
        log = self.request("GET", "/api/cleanup", cookie=cookie)[2]["log"]
        self.assertEqual((log[0]["pane_id"], log[0]["result"], log[0]["who"]), ("w1:p2", "closed", "phone"))


class Cli(unittest.TestCase):
    def test_preview_only(self):
        from herdr_attention.cli import cmd_cleanup

        class Client:
            def __init__(self):
                self.posts = []

            def post(self, path, body):
                self.posts.append((path, body))
                return {"id": 1, "status": "running"}

            def get(self, path):
                return {"id": 1, "status": "ready", "candidates": [{"pane_id": "w1:p2", "name": "idler", "reason": "Idle."}],
                        "excluded": [], "workspaces_off": [{"workspace_id": "w4", "label": "Work", "reason": "Never.", "panes": 3}]}

        class Args:
            preview, workspace, json = True, "w1", False

        client = Client()
        out = io.StringIO()
        with patch.dict(os.environ, {"HERDR_PANE_ID": "wC:p3"}), redirect_stdout(out):
            self.assertEqual(cmd_cleanup(Args, client), 0)
        self.assertEqual(client.posts, [("/api/cleanup/preview", {"workspace": "w1", "caller_pane": "wC:p3"})])
        self.assertIn("nothing closed", out.getvalue())
        self.assertIn("w1:p2", out.getvalue())
        Args.preview = False
        with self.assertRaises(SystemExit):
            cmd_cleanup(Args, client)


if __name__ == "__main__":
    unittest.main()
