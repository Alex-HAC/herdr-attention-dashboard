"""Declared relationships: registry, policies, privacy, provenance, launch helper. FIXTURE-BASED.

The Herdr metadata-token behaviour these rely on was checked live on a
throwaway pane (docs/relationships.md); Herdr-restart durability of tokens is
unverified and not relied on: the registry is the authority.
"""

import json
import tempfile
import unittest
from pathlib import Path

from herdr_attention.collector import Collector
from herdr_attention.launcher import LaunchError, plan, run
from herdr_attention.relations import Registry, RelationError, cycles
from herdr_attention.store import Store
from tests.helpers import FakeHerdr, agent, settings, workspaces

A, B, C, D = "claude:aaaaaaaa-0000-4000-8000-000000000001", "claude:aaaaaaaa-0000-4000-8000-000000000002", \
             "codex:aaaaaaaa-0000-4000-8000-000000000003", "codex:aaaaaaaa-0000-4000-8000-000000000004"


class RegistryRules(unittest.TestCase):
    def setUp(self):
        self.reg = Registry(Store(":memory:"))

    def link(self, s, o, kind="reports_to", provenance="manual", at=1.0):
        return self.reg.link(s, kind, o, at=at, provenance=provenance, actor="cli")

    def test_self_links_are_refused(self):
        with self.assertRaises(RelationError):
            self.link(A, A)

    def test_reports_to_is_never_observed_and_spawn_is_not_supervision(self):
        with self.assertRaises(RelationError):
            self.link(B, A, provenance="observed-spawn")
        self.link(B, A, kind="spawned_by", provenance="observed-spawn")
        state = self.reg.state()
        self.assertNotIn((B, "reports_to", A), state.links)
        self.assertEqual(self.reg.labels_for(A)["role"], [], "spawning never makes the parent a coordinator")

    def test_labels_are_declared_only(self):
        with self.assertRaises(RelationError):
            self.reg.set_label(A, "role", "coordinator", at=1, provenance="observed-spawn", actor="trace")
        with self.assertRaises(RelationError):
            self.reg.set_label(A, "role", "bad\x00value", at=1, provenance="manual", actor="cli")

    def test_multiple_coordinators_and_handover(self):
        self.link(C, A, at=1)
        self.link(C, B, at=2)
        self.assertEqual({o for (s, k, o) in self.reg.state().links if s == C}, {A, B})
        self.reg.handover(C, D, at=3, provenance="manual", actor="ui")
        self.assertEqual({o for (s, k, o) in self.reg.state().links if s == C}, {D})
        actions = [(e["action"], e["object"], e["note"]) for e in reversed(self.reg.events(C))]
        self.assertEqual(actions[-3:], [("unlink", A, f"handover to {D}"), ("unlink", B, f"handover to {D}"), ("link", D, "handover")])

    def test_roles_add_up_team_and_task_replace_and_revocation_is_recorded(self):
        for role in ("coordinator", "reviewer"):
            self.reg.set_label(A, "role", role, at=1, provenance="manual", actor="cli")
        self.reg.set_label(A, "team", "alpha", at=2, provenance="registered", actor="helper")
        self.reg.set_label(A, "team", "beta", at=3, provenance="manual", actor="ui")
        labels = self.reg.labels_for(A)
        self.assertEqual([r["value"] for r in labels["role"]], ["coordinator", "reviewer"])
        self.assertEqual([(t["value"], t["provenance"]) for t in labels["team"]], [("beta", "manual")])
        self.reg.clear_label(A, "role", "coordinator", at=4, provenance="manual", actor="cli")
        self.assertEqual([r["value"] for r in self.reg.labels_for(A)["role"]], ["reviewer"])
        notes = [e["note"] for e in self.reg.events(A) if e["action"] == "clear"]
        self.assertIn("revoked", notes)
        self.assertIn("replaced by beta", notes)

    def test_history_is_append_only(self):
        self.link(B, A)
        self.reg.unlink(B, "reports_to", A, at=2, provenance="manual", actor="cli")
        self.link(B, A, at=3)
        self.assertEqual([e["action"] for e in reversed(self.reg.events(B))], ["link", "unlink", "link"])
        self.assertIsNone(self.link(B, A, at=4), "duplicate link adds nothing")

    def test_cycles_are_flagged_not_refused(self):
        self.link(A, B)
        self.link(B, C)
        self.link(C, A)
        self.link(D, A)
        looped = cycles([(s, o) for (s, k, o) in self.reg.state().links])
        self.assertEqual(looped, {(A, B), (B, C), (C, A)})

    def test_survives_restart_and_follows_identity_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state.db"
            store = Store(db)
            Registry(store).link("term:t1", "reports_to", A, at=1, provenance="manual", actor="cli")
            store.rename_identity("term:t1", B)
            store.close()
            again = Registry(Store(db))
            self.assertIn((B, "reports_to", A), again.state().links)


class InViews(unittest.TestCase):
    def setUp(self):
        self.herdr = FakeHerdr([
            agent("w1:p1", "working", session="aaaaaaaa-0000-4000-8000-000000000001", name="lead"),
            agent("w2:p1", "working", session="aaaaaaaa-0000-4000-8000-000000000002", name="worker"),
            agent("w3:p1", "working", session="aaaaaaaa-0000-4000-8000-000000000005", name="secret", title="SECRET"),
        ], workspaces("w1", "w2", "w3"))
        self.collector = Collector(db_path=":memory:", client=self.herdr,
                                   settings=settings(watch={"enabled_workspaces": ["w1", "w2"]}))
        self.collector.handle_snapshot()
        self.secret = "claude:aaaaaaaa-0000-4000-8000-000000000005"

    def act(self, local=True, **body):
        return self.collector.relation_action(body, local=local)

    def test_declared_link_crosses_workspaces_with_provenance(self):
        self.act(action="link", subject="worker", kind="reports_to", object="lead")
        self.act(action="set", subject="lead", kind="role", value="coordinator")
        view = self.collector.view(local=False)
        [link] = view["links"]
        self.assertEqual((link["from"], link["to"], link["kind"], link["provenance"]), (B, A, "reports_to", ["manual"]))
        self.assertIn("declared by you (cli)", link["source"])
        lead = next(a for a in view["agents"] if a["name"] == "lead")
        self.assertEqual([r["value"] for r in lead["roles"]["role"]], ["coordinator"])

    def test_unwatched_endpoints_are_minimal_placeholders(self):
        # The launch helper may name a caller that is not watched.
        self.act(action="link", subject=self.secret, kind="reports_to", object=A, registered=True)
        self.act(action="link", subject=B, kind="spawned_by", object=self.secret, registered=True)
        view = self.collector.view(local=False)
        blob = json.dumps(view)
        self.assertNotIn(self.secret, blob)
        self.assertNotIn("SECRET", blob)
        self.assertNotIn("secret", blob)
        hidden_from = [l for l in view["links"] if l["from_hidden"]]
        hidden_to = [l for l in view["links"] if l["hidden"]]
        self.assertEqual((len(hidden_from), len(hidden_to)), (1, 1))
        detail = self.collector.agent_detail(A)
        self.assertNotIn(self.secret, json.dumps(detail))
        self.assertTrue(any(r.get("hidden_end") for r in detail["relation_history"]))

    def test_manual_actions_only_name_watched_agents(self):
        with self.assertRaises(RelationError):
            self.act(action="link", subject="worker", kind="reports_to", object=self.secret)
        with self.assertRaises(RelationError):
            self.act(local=False, action="link", subject="worker", kind="reports_to", object=self.secret, registered=True)

    def test_gone_agents_keep_history_and_show_as_gone(self):
        self.act(action="link", subject="worker", kind="reports_to", object="lead")
        self.collector.engine.pane_gone("w1:p1", "pane closed")
        view = self.collector.view(local=False)
        [link] = view["links"]
        self.assertTrue(link["hidden"])
        self.assertEqual(link["label"], "gone: lead")
        self.assertEqual(len(self.collector.relations.events(B)), 1)

    def test_new_session_in_the_same_pane_does_not_inherit(self):
        self.act(action="set", subject="lead", kind="role", value="coordinator")
        self.herdr.set(agent("w1:p1", "working", session="aaaaaaaa-0000-4000-8000-000000000009", name="lead"))
        self.collector.handle_event({"event": "pane.agent_status_changed", "data": {"pane_id": "w1:p1", "workspace_id": "w1"}})
        view = self.collector.view(local=False)
        new = next(a for a in view["agents"] if a["identity"].endswith("09"))
        self.assertEqual(new["roles"]["role"], [], "roles belong to the session, not the pane")

    def test_spawn_seen_in_a_trace_and_registered_merge_into_one_edge(self):
        self.act(action="link", subject="worker", kind="spawned_by", object="lead", registered=True)
        self.collector.engine.agents[B]["trace"] = {"available": True, "links": [
            {"kind": "spawned_by", "target": A, "source": "Codex session_meta (parent_thread_id)"}]}
        [link] = self.collector.view(local=False)["links"]
        self.assertEqual(sorted(link["provenance"]), ["observed-spawn", "registered"])

    def test_references_resolve_by_identity_name_or_pane(self):
        self.assertEqual(self.collector.resolve_agent("lead"), A)
        self.assertEqual(self.collector.resolve_agent("w2:p1"), B)
        self.assertEqual(self.collector.resolve_agent(A), A)
        with self.assertRaises(RelationError):
            self.collector.resolve_agent("secret")


class LaunchHelper(unittest.TestCase):
    env = {"HERDR_ENV": "1", "HERDR_PANE_ID": "w7:p1", "CLAUDE_CONFIG_DIR": "/tmp"}

    def test_refusals(self):
        args = dict(name="worker-1", kind="claude", config_dir=None, direction="right", cwd=None, agent_args=[])
        with self.assertRaises(LaunchError):
            plan(env={}, **args)
        with self.assertRaises(LaunchError):
            plan(env={**self.env, "ANTHROPIC_API_KEY": "x"}, **args)
        with self.assertRaises(LaunchError):
            plan(env={"HERDR_ENV": "1", "HERDR_PANE_ID": "w7:p1"}, **args)
        self.assertEqual(plan(env=self.env, **{**args, "name": "Bad Name"})["name"], "bad-name")
        steps = plan(env=self.env, **args)
        self.assertNotIn("--env", steps["split"])
        self.assertEqual(steps["child_env"]["CLAUDE_CONFIG_DIR"], "/tmp")

    def test_registers_and_mirrors_only_on_the_new_pane(self):
        commands, registered = [], []

        def runner(cmd):
            commands.append(cmd)
            if cmd[:3] == ["herdr", "pane", "split"]:
                return json.dumps({"result": {"pane": {"pane_id": "w7:p9"}}})
            return "{}"

        infos = {"w7:p1": {"agent": "claude", "agent_session": {"agent": "claude", "value": "aaaaaaaa-0000-4000-8000-000000000001"}},
                 "w7:p9": {"agent": "claude", "agent_session": {"agent": "claude", "value": "aaaaaaaa-0000-4000-8000-000000000002"}}}
        steps = plan(env=self.env, name="worker-1", kind="claude", config_dir=None, direction="down", cwd=None, agent_args=[])
        result = run(steps, name="worker-1", register=registered.append, agent_info=infos.get, role="worker", team="alpha",
                     task=None, reports_to="me", runner=runner, sleep=lambda s: None)
        self.assertEqual(result["identity"], B)
        self.assertEqual([(r["kind"], r.get("object"), r.get("value")) for r in registered],
                         [("spawned_by", A, None), ("reports_to", A, None), ("role", None, "worker"), ("team", None, "alpha")])
        self.assertTrue(all(r["registered"] for r in registered))
        metadata = [c for c in commands if c[:3] == ["herdr", "pane", "report-metadata"]]
        self.assertEqual([c[3] for c in metadata], ["w7:p9"], "tokens only on the pane it created")
        self.assertFalse([c for c in commands if "prompt" in c or "send-text" in c or "send-keys" in c])

    def test_no_session_id_means_no_registration(self):
        registered = []
        steps = plan(env=self.env, name="worker-2", kind="claude", config_dir=None, direction="down", cwd=None, agent_args=[])
        result = run(steps, name="worker-2", register=registered.append, agent_info=lambda pane: None, role="worker",
                     team=None, task=None, reports_to=None,
                     runner=lambda cmd: json.dumps({"result": {"pane": {"pane_id": "w7:p9"}}}) if "split" in cmd else "{}",
                     wait_seconds=0.01, sleep=lambda s: None)
        self.assertEqual(registered, [])
        self.assertIn("nothing was registered", result["warning"])


if __name__ == "__main__":
    unittest.main()
