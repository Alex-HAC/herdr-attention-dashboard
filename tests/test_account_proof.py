"""v0.9.7.1 proven accounts: only a hard fact about the session itself, mapped by Alex's routing accounts."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from herdr_attention import account_icons, account_proof as ap
from herdr_attention.quotas import enrich
from tests.test_quotas import snapshot

SESSION = "11111111-2222-3333-4444-555555555555"
OTHER = "66666666-7777-8888-9999-000000000000"


class Procs:
    """A fake /proc: {pid: (argv, children, folder env)}."""
    def __init__(self, table):
        self.table = table

    def argv(self, pid):
        return self.table[pid][0]

    def children(self, pid):
        return self.table[pid][1]

    def folder_env(self, pid):
        return self.table[pid][2]


class Home(unittest.TestCase):
    """A temporary home with Alex's account folders; ~ points here."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name).resolve()
        for folder in (".claude/projects/p", ".claude-personal/projects/p", ".codex/sessions/2026/11/17",
                       ".codex-personal/sessions/2026/11/17", "elsewhere/projects/p"):
            (self.home / folder).mkdir(parents=True)
        self.env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self.env.start()
        self.accounts = {"claude-personal": {"env": {"CLAUDE_CONFIG_DIR": "~/.claude-personal"}}, "claude-work": {"env": {}},
                         "codex-personal": {"env": {"CODEX_HOME": "~/.codex-personal"}}, "codex-work": {"env": {}},
                         "agy-work": {"env": {}}, "cursor-work": {"env": {}}}

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def trace(self, folder, session=SESSION):
        path = self.home / folder / f"{session}.jsonl"
        path.write_text("{}\n")
        return path.resolve()


class FolderMap(Home):
    def test_folders_come_from_the_routing_accounts_and_the_documented_defaults(self):
        folders = ap.folder_map(self.accounts)
        self.assertEqual(folders["claude"], {self.home / ".claude-personal": ["claude-personal"], self.home / ".claude": ["claude-work"]})
        self.assertEqual(folders["codex"], {self.home / ".codex-personal": ["codex-personal"], self.home / ".codex": ["codex-work"]})

    def test_a_folder_two_accounts_share_proves_nothing(self):
        folders = ap.folder_map({**self.accounts, "claude-main": {"env": {"CLAUDE_CONFIG_DIR": "~/.claude"}}})
        self.assertIsNone(ap.account_for(folders["claude"], self.home / ".claude"))
        self.assertEqual(ap.account_for(folders["claude"], self.home / ".claude-personal"), "claude-personal")


class TraceFact(Home):
    def test_the_trace_folder_names_the_account(self):
        folders = ap.folder_map(self.accounts)["claude"]
        self.assertEqual(ap.trace_folder(self.trace(".claude-personal/projects/p"), "claude", folders), self.home / ".claude-personal")
        self.assertEqual(ap.trace_folder(self.trace(".claude/projects/p", OTHER), "claude", folders), self.home / ".claude")
        self.assertIsNone(ap.trace_folder(self.trace("elsewhere/projects/p"), "claude", folders))   # not an account folder
        self.assertIsNone(ap.trace_folder(None, "claude", folders))

    def test_a_trace_reachable_from_two_account_folders_is_ambiguous(self):
        shared = self.home / ".claude-shared"
        shared.mkdir()
        (shared / "projects").symlink_to(self.home / ".claude/projects")      # one projects folder for two accounts
        folders = ap.folder_map({**self.accounts, "claude-x": {"env": {"CLAUDE_CONFIG_DIR": "~/.claude-shared"}}})["claude"]
        self.assertEqual(ap.trace_folder(self.trace(".claude/projects/p"), "claude", folders), ap.AMBIGUOUS)


class EnvironmentFact(Home):
    def test_the_agent_process_names_its_folder_or_runs_on_the_default(self):
        info = {"shell_pid": 10}
        procs = Procs({10: (["/usr/bin/bash"], [11], {}),
                       11: (["/home/a/.local/bin/claude", "--resume", SESSION], [], {"CLAUDE_CONFIG_DIR": str(self.home / ".claude-personal")})})
        self.assertEqual(ap.env_folder(info, procs, "claude"), self.home / ".claude-personal")
        procs.table[11] = (["claude"], [], {})
        self.assertEqual(ap.env_folder(info, procs, "claude"), self.home / ".claude")

    def test_a_wrapper_script_is_not_the_agent_its_child_is(self):
        procs = Procs({10: (["/usr/bin/bash"], [11], {}),
                       11: (["/usr/bin/bash", "/home/a/bin/claude-personal"], [12], {}),          # exports inside
                       12: (["claude"], [13], {"CLAUDE_CONFIG_DIR": "~/.claude-personal"}),
                       13: (["claude", "-p", "x"], [], {})})                                      # a tool call, deeper
        self.assertEqual(ap.env_folder({"shell_pid": 10}, procs, "claude"), self.home / ".claude-personal")

    def test_two_agents_or_none_in_a_pane_prove_nothing(self):
        two = Procs({10: (["bash"], [11, 12], {}), 11: (["codex"], [], {}), 12: (["codex"], [], {"CODEX_HOME": "~/.codex-personal"})})
        self.assertIsNone(ap.env_folder({"shell_pid": 10}, two, "codex"))
        none = Procs({10: (["bash"], [11], {}), 11: (["vim"], [], {})})
        self.assertIsNone(ap.env_folder({"shell_pid": 10}, none, "codex"))
        self.assertIsNone(ap.env_folder({}, none, "codex"))

    def test_only_the_two_folder_variables_are_ever_read(self):
        proc = self.home / "proc" / "42"
        proc.mkdir(parents=True)
        (proc / "environ").write_bytes(b"ANTHROPIC_API_KEY=sk-secret\0HOME=/home/a\0CODEX_HOME=/x/y\0OPENAI_API_KEY=k\0")
        self.assertEqual(ap.ProcReader(str(self.home / "proc")).folder_env(42), {"CODEX_HOME": "/x/y"})


class Proofs(Home):
    def proofs(self, traces=None, procs=None, clock=None):
        traces = traces or {}
        self.info_calls = []

        def info(pane):
            self.info_calls.append(pane)
            return {"shell_pid": int(pane.split(":p")[1]) * 10}
        return ap.AccountProofs(lambda: self.accounts, lambda kind, s: traces.get(s), info,
                                procs or Procs({}), clock or (lambda: 1000.0))

    def agent(self, session=SESSION, pane="w1:p1", kind="claude", **extra):
        return {"identity": f"{kind}:{session}", "pane_id": pane, "present": True, **extra}

    def claude_proc(self, folder=None):
        env = {"CLAUDE_CONFIG_DIR": folder} if folder else {}
        return Procs({10: (["bash"], [11], {}), 11: (["claude"], [], env)})

    def test_each_fact_alone_and_both_agreeing(self):
        p = self.proofs({SESSION: self.trace(".claude-personal/projects/p")})
        p.observe([self.agent()])
        self.assertEqual(p.result(f"claude:{SESSION}"), {"account": "claude-personal", "source": "config_folder"})
        p = self.proofs(procs=self.claude_proc("~/.claude-personal"))
        p.observe([self.agent()])
        self.assertEqual(p.result(f"claude:{SESSION}"), {"account": "claude-personal", "source": "environment"})
        p = self.proofs({SESSION: self.trace(".claude-personal/projects/p")}, self.claude_proc("~/.claude-personal"))
        p.observe([self.agent()])
        self.assertEqual(p.result(f"claude:{SESSION}"), {"account": "claude-personal", "source": "both"})

    def test_two_facts_that_disagree_prove_nothing(self):
        p = self.proofs({SESSION: self.trace(".claude-personal/projects/p")}, self.claude_proc())     # env: default ~/.claude
        p.observe([self.agent()])
        self.assertIsNone(p.result(f"claude:{SESSION}"))

    def test_the_two_accounts_case_proves_nothing(self):
        self.accounts["claude-main"] = {"env": {}}          # two Claude accounts on the default folder
        p = self.proofs({SESSION: self.trace(".claude/projects/p")}, self.claude_proc())
        p.observe([self.agent()])
        self.assertIsNone(p.result(f"claude:{SESSION}"))

    def test_kind_and_working_folder_are_never_proof(self):
        p = self.proofs()                                   # no trace found, no agent process found
        p.observe([self.agent(cwd=str(self.home / ".claude-personal"))])
        self.assertIsNone(p.result(f"claude:{SESSION}"))

    def test_a_launch_record_wins_and_is_not_even_checked(self):
        p = self.proofs({SESSION: self.trace(".claude-personal/projects/p")})
        launched = self.agent(launch={"account": "claude-work"})
        p.observe([launched])
        self.assertEqual(self.info_calls, [])
        p.decorate([launched])
        self.assertNotIn("account_proof", launched)

    def test_facts_are_read_once_per_session_and_unknown_ones_retried_later(self):
        now = [1000.0]
        p = self.proofs(procs=self.claude_proc("~/.claude-personal"), clock=lambda: now[0])
        for _ in range(3):
            p.observe([self.agent()])
        self.assertEqual(self.info_calls, ["w1:p1"])
        q = self.proofs(procs=Procs({10: (["bash"], [], {})}), clock=lambda: now[0])   # no agent process yet
        q.observe([self.agent()])
        q.observe([self.agent()])
        now[0] += ap.RETRY_SECONDS
        q.observe([self.agent()])
        self.assertEqual(len(self.info_calls), 2)
        p.observe([])                                       # the session ended: forgotten
        self.assertIsNone(p.result(f"claude:{SESSION}"))

    def test_subagents_take_the_parent_account_only_when_it_is_certain(self):
        p = self.proofs({SESSION: self.trace(".claude-personal/projects/p")})
        parent, sub = self.agent(), {"identity": "claude:sub/1", "in_process": True, "in_process_parent": f"claude:{SESSION}"}
        p.observe([parent])
        p.decorate([parent, sub])
        self.assertEqual(sub["account_proof"], {"account": "claude-personal", "source": "parent"})
        q = self.proofs({SESSION: self.trace(".claude-personal/projects/p")}, self.claude_proc())   # parent disagrees
        parent, sub = self.agent(), dict(sub)
        q.observe([parent])
        q.decorate([parent, sub])
        self.assertNotIn("account_proof", parent)
        self.assertNotIn("account_proof", sub)
        launched, sub = self.agent(launch={"account": "codex-work"}), dict(sub)
        q.decorate([launched, sub])
        self.assertEqual(sub["account_proof"]["account"], "codex-work")

    def test_a_bad_routing_file_means_no_proofs(self):
        p = ap.AccountProofs(lambda: (_ for _ in ()).throw(ValueError("bad")), lambda k, s: None, lambda pane: {}, Procs({}))
        self.assertEqual(p.folders(), {"claude": {}, "codex": {}})


class IconsAndQuotas(unittest.TestCase):
    """The proven account reaches the icon (with its source) and the Quotas agents count."""

    def view(self, *agents):
        q = enrich(snapshot(), list(agents))
        view = {"quotas": q, "agents": list(agents)}
        account_icons.decorate(view, {})
        return view

    def test_icon_says_where_the_account_comes_from(self):
        proven = {"identity": "claude:a", "pane_id": "w1:p1", "present": True,
                  "account_proof": {"account": "claude-personal", "source": "config_folder"}}
        launched = {"identity": "codex:b", "pane_id": "w1:p2", "present": True, "launch": {"account": "codex-work"},
                    "account_proof": {"account": "codex-personal", "source": "environment"}}
        unknown = {"identity": "claude:c", "pane_id": "w1:p3", "present": True}
        view = self.view(proven, launched, unknown)
        self.assertEqual(proven["icon"]["account"], "claude:personal")
        self.assertEqual(proven["icon"]["source"], {"id": "config_folder", "label": "from the session's config folder"})
        self.assertEqual((launched["icon"]["account"], launched["icon"]["source"]["label"]), ("codex", "recorded at launch"))
        self.assertNotIn("icon", unknown)
        personal = next(a for a in view["quotas"]["accounts"] if a["id"] == "claude:personal")
        self.assertEqual([a["identity"] for a in personal["agents"]], ["claude:a"])
        self.assertEqual(view["quotas"]["unassigned_agents"], 1)          # only the unknown one

    def test_a_subagent_with_the_parent_account_gets_the_icon_but_is_not_counted(self):
        sub = {"identity": "claude:a/sub", "in_process": True, "present": True,
               "account_proof": {"account": "claude-personal", "source": "parent"}}
        view = self.view(sub)
        self.assertEqual(sub["icon"]["source"]["label"], "from its parent agent")
        personal = next(a for a in view["quotas"]["accounts"] if a["id"] == "claude:personal")
        self.assertEqual(personal["agents"], [])


if __name__ == "__main__":
    unittest.main()


class InTheCollector(Home):
    """Trace thread -> cached proof -> view: icon with its source and the Quotas count."""

    def test_a_session_trace_in_an_account_folder_gives_the_icon(self):
        from herdr_attention.collector import Collector
        from tests.helpers import FakeHerdr, agent, settings, workspaces
        from tests.test_quotas import ORDER, TZ, USAGE
        from tests.trace_fixtures import sid
        session = sid("proven")
        herdr = FakeHerdr([agent("w1:p1", "working", session=session, name="lead")], workspaces("w1"))
        herdr.process_infos["w1:p1"] = {"shell_pid": 10}
        collector = Collector(db_path=":memory:", client=herdr, settings=settings(
            watch={"enabled_workspaces": ["w1"]},
            traces={"claude_dirs": [str(self.home / ".claude-personal/projects"), str(self.home / ".claude/projects")], "codex_dirs": []},
            quotas={"usage_dir": str(USAGE), "order_path": str(ORDER), "timezone": TZ}))
        collector.account_proofs.accounts = lambda: self.accounts
        collector.account_proofs.procs = Procs({10: (["bash"], [11], {}), 11: (["claude"], [], {"CLAUDE_CONFIG_DIR": "~/.claude-personal"})})
        collector.handle_snapshot()
        self.trace(".claude-personal/projects/p", session)
        collector.poll_traces()
        view = collector.view(local=True)
        lead = next(a for a in view["agents"] if a["pane_id"] == "w1:p1")
        self.assertEqual(lead["account_proof"], {"account": "claude-personal", "source": "both"})
        self.assertEqual((lead["icon"]["account"], lead["icon"]["source"]["id"]), ("claude:personal", "both"))
        personal = next(a for a in view["quotas"]["accounts"] if a["id"] == "claude:personal")
        self.assertEqual([a["identity"] for a in personal["agents"]], [lead["identity"]])
        calls = [c for c in herdr.calls if c[0] == "process_info"]
        collector.poll_traces()
        collector.view(local=True)
        self.assertEqual([c for c in herdr.calls if c[0] == "process_info"], calls)    # read once, not per view or poll
