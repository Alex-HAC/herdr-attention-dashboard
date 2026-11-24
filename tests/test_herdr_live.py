"""Checks against the real, running Herdr. Read-only.

Skipped unless HERDR_LIVE=1. They confirm the facts the collector relies on
(docs/phase0-runtime.md) still hold for the installed Herdr.
"""

import json
import os
import threading
import time
import unittest

from herdr_attention.collector import Collector
from herdr_attention.engine import identity_of
from herdr_attention.herdr import HerdrClient, HerdrError
from tests.helpers import settings

LIVE = os.environ.get("HERDR_LIVE") == "1"


@unittest.skipUnless(LIVE, "set HERDR_LIVE=1 to run against the real Herdr")
class LiveHerdr(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = HerdrClient()
        cls.agents = cls.client.agents()

    def test_protocol(self):
        pong = self.client.ping()
        self.assertEqual(pong["type"], "pong")
        self.assertEqual(pong["protocol"], 22, "built against protocol 22; re-run phase 0 checks if this changed")

    def test_agent_fields_we_rely_on(self):
        self.assertTrue(self.agents, "need at least one agent running")
        # "name" is left out entirely for agents nobody named, so it is optional.
        needed = {"pane_id", "workspace_id", "agent", "agent_status", "state_change_seq", "terminal_id"}
        for info in self.agents:
            self.assertLessEqual(needed, set(info), info.get("pane_id"))
            self.assertIn(info["agent_status"], {"idle", "working", "blocked", "done", "unknown"})
        with_session = [a for a in self.agents if (a.get("agent_session") or {}).get("value")]
        self.assertTrue(with_session, "expected Herdr to report session ids")

    def test_state_change_seq_is_global(self):
        seqs = [a["state_change_seq"] for a in self.agents]
        self.assertEqual(len(seqs), len(set(seqs)), "seq values are unique across panes (one global counter)")

    def test_get_explain_read(self):
        pane = self.agents[0]["pane_id"]
        self.assertEqual(self.client.agent(pane)["pane_id"], pane)
        explain = self.client.explain(pane)
        self.assertIn("matched_rule", explain)
        self.assertIn(explain["matched_rule"]["state"], {"idle", "working", "blocked", "done", "unknown"})
        self.assertIn("evaluated_rules", explain)
        self.assertIsInstance(self.client.read_output(pane, lines=5), str)
        self.assertIsNone(self.client.agent("w999:p999"))

    def test_status_subscription_needs_pane_id_and_starts(self):
        stop = threading.Event()
        started = threading.Event()
        thread = threading.Thread(target=self.client.subscribe,
                                  args=([self.agents[0]["pane_id"]], stop, lambda e: None, started.set), daemon=True)
        thread.start()
        self.assertTrue(started.wait(5), "subscription confirmed")
        stop.set()
        thread.join(5)

    def test_write_methods_are_refused_locally(self):
        for method in ("agent.prompt", "agent.send_keys", "pane.send_text", "agent.focus"):
            with self.assertRaises(HerdrError):
                self.client.call(method, {"target": "w0:p0"})

    def test_tabs_are_listed(self):
        tabs = self.client.tabs()
        self.assertTrue(tabs)
        for tab in tabs:
            self.assertLessEqual({"tab_id", "workspace_id", "label"}, set(tab))
        agent_tabs = {a.get("tab_id") for a in self.agents}
        self.assertLessEqual(agent_tabs - {None}, {t["tab_id"] for t in tabs})

    def test_workspace_view_against_real_herdr(self):
        workspace = os.environ.get("HERDR_WORKSPACE_ID") or self.agents[0]["workspace_id"]
        collector = Collector(db_path=":memory:", client=self.client,
                              settings=settings(watch={"enabled_workspaces": [workspace]}))
        collector.handle_snapshot()
        view = collector.view(local=False)
        [ws] = view["workspaces"]
        self.assertEqual(ws["id"], workspace)
        self.assertEqual(sum(ws["counts"].values()), ws["agent_count"])
        self.assertEqual(ws["relationships"], "unknown")
        grouped = [i for tab in ws["tabs"] for i in tab["agents"]]
        self.assertEqual(sorted(grouped), sorted(a["identity"] for a in view["agents"]))
        detail = collector.agent_detail(view["agents"][0]["identity"])
        self.assertTrue(detail["activity"])
        self.assertTrue(all(c["id"] for c in collector.chooser()))

    def test_traces_of_real_agents(self):
        """Passive trace reading for the agents in one workspace (read-only, bounded)."""
        workspace = os.environ.get("HERDR_WORKSPACE_ID") or self.agents[0]["workspace_id"]
        collector = Collector(db_path=":memory:", client=self.client,
                              settings=settings(watch={"enabled_workspaces": [workspace]}))
        collector.handle_snapshot()
        for _ in range(20):
            collector.poll_traces()
            if all((a.get("trace") or {}).get("caught_up", True) for a in collector.engine.agents.values()):
                break
        view = collector.view(local=False)
        found = [a for a in view["agents"] if a["metrics"]["trace"]["available"]]
        self.assertTrue(found, "expected at least one Claude/Codex trace for this workspace")
        for a in view["agents"]:
            m = a["metrics"]
            for name in ("context", "tokens", "age", "active"):
                self.assertTrue(m[name].get("source") or m[name].get("reason"), f"{name} has provenance")
        a = found[0]
        self.assertTrue(a["metrics"]["age"]["exact"])
        page = collector.conversation(a["identity"], 5, None)
        self.assertLessEqual(len(page["entries"]), 5)
        self.assertTrue(all(e["role"] in ("user", "assistant") for e in page["entries"]))
        self.assertNotIn("$", json.dumps(a["metrics"]))

    def test_collector_snapshot_against_real_herdr(self):
        workspace = os.environ.get("HERDR_WORKSPACE_ID") or self.agents[0]["workspace_id"]
        collector = Collector(db_path=":memory:", client=self.client,
                              settings=settings(watch={"enabled_workspaces": [workspace]}))
        collector.handle_snapshot()
        view = collector.view(local=False)
        expected = {identity_of(a) for a in self.agents if a["workspace_id"] == workspace}
        self.assertEqual({a["identity"] for a in view["agents"]}, expected)
        self.assertTrue(all(a["workspace_id"] == workspace for a in view["agents"]))
        self.assertLess(time.time() - view["generated_at"], 60)


if __name__ == "__main__":
    unittest.main()
