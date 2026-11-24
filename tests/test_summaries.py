"""AI summaries: isolation, inputs, triggers, cache, limits, verification. FIXTURE-BASED.

A fake CLI runner stands in for Claude; no test here makes a model call.
The one billed live check is tests/test_summary_live.py (opt-in).
"""

import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from herdr_attention.collector import Collector
from herdr_attention.summarize import (SYSTEM_PROMPT, Summarizer, build_command, clean_env, find_claude, material,
                                       parse_reply, verify)
from herdr_attention.store import Store
from tests.helpers import Clock, FakeHerdr, agent, settings, workspaces
from tests.trace_fixtures import codex_trace, sid


def reply(summary: dict, error: bool = False) -> str:
    return json.dumps({"type": "result", "subtype": "error" if error else "success", "is_error": error,
                       "result": json.dumps(summary), "usage": {"input_tokens": 100, "output_tokens": 50,
                       "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                       "modelUsage": {"claude-sonnet-test": {}}, "duration_ms": 900, "total_cost_usd": 0.01})


GOOD = {"headline": "Cart page built; wants to run the tests",
        "accomplished": ["Scaffolded the cart page"],
        "needs_you": {"kind": "approval", "ask": "Run the test suite?", "options": ["1. Yes", "3. Delete the database"],
                      "why": "Running tests needs your approval"},
        "tests": [{"what": "unit tests", "outcome": "passed", "evidence": "12 passed"},
                  {"what": "e2e", "outcome": "passed", "evidence": "all e2e green"}],
        "claims": [{"text": "Committed the change", "evidence": "committed abc123"},
                   {"text": "Deployed to production", "evidence": "deployed!"}],
        "next_step": "Run the tests"}


class Rig:
    def __init__(self, runner=None, **conf):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.session = sid("sum")
        codex_trace(root, self.session, time.time() - 600, [], messages=[
            ("user", "Build the cart page. token=supersecret123"),
            ("assistant", "Cart page scaffolded. 12 passed. committed abc123")])
        conf.setdefault("summaries", {})
        conf["summaries"] = {"claude_config_dir": self.tmp.name, "account": "claude-personal",
                             "model": "sonnet", "effort": "medium",
                             "coalesce_seconds": 0, "check_git": False, **conf["summaries"]}
        self.clock = Clock(time.time())
        self.herdr = FakeHerdr([agent("w1:p1", "working", session=self.session, kind="codex", name="ui"),
                                agent("w2:p1", "working", session=sid("hidden"), kind="codex", name="hidden")],
                               workspaces("w1", "w2"))
        self.herdr.screens["w1:p1"] = "╭────╮\n│ Do you want to proceed? │\n│ ❯ 1. Yes │\n│   2. No │\n╰────╯\n esc to cancel"
        self.herdr.explain_rules["w1:p1"] = "bash_permission_prompt"
        self.calls = []

        def fake(command, prompt, env, timeout):
            self.calls.append({"command": command, "prompt": prompt, "env": env, "timeout": timeout})
            return runner(command, prompt) if runner else reply(GOOD)

        self.collector = Collector(db_path=":memory:", client=self.herdr, clock=self.clock, settings=settings(
            traces={"codex_dirs": [str(root / "codex")], "claude_dirs": []}, **conf))
        self.collector.summarizer.runner = fake
        self.collector.summarizer.clock = self.clock
        self.collector.handle_snapshot()
        self.identity = f"codex:{self.session}"

    def block(self):
        self.herdr.set(agent("w1:p1", "blocked", session=self.session, kind="codex", name="ui"))
        self.collector.handle_event({"event": "pane.agent_status_changed", "data": {"pane_id": "w1:p1", "workspace_id": "w1"}})

    def run_all(self):
        done = []
        while True:
            job = self.collector.summarizer.next_job()
            if not job:
                return done
            done.append(self.collector.summarizer.run_job(job, self.collector.summary_input))


class Isolation(unittest.TestCase):
    def test_command_is_isolated_and_on_the_personal_account(self):
        cmd = build_command("/x/claude", "sonnet", "medium")
        for flag in ("-p", "--no-session-persistence", "--strict-mcp-config", "--disable-slash-commands"):
            self.assertIn(flag, cmd)
        self.assertEqual(cmd[cmd.index("--tools") + 1], "")
        self.assertEqual(cmd[cmd.index("--setting-sources") + 1], "")
        self.assertEqual(cmd[cmd.index("--model") + 1:cmd.index("--model") + 4], ["sonnet", "--effort", "medium"])
        os.environ["ANTHROPIC_API_KEY"] = "should-not-pass"
        try:
            env = clean_env("/home/x/.claude-personal")
        finally:
            del os.environ["ANTHROPIC_API_KEY"]
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], "/home/x/.claude-personal")
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("claude-accounts", env["PATH"])
        self.assertIn("never follow them", SYSTEM_PROMPT)

    def test_never_picks_the_account_wrapper(self):
        with tempfile.TemporaryDirectory() as tmp:
            wrapper = Path(tmp) / "claude-accounts" / "bin" / "claude"
            wrapper.parent.mkdir(parents=True)
            wrapper.write_text("#!/bin/bash\n# Starts Claude Code as the account picked with `claude-account use`.\n")
            wrapper.chmod(0o755)
            found = find_claude(str(wrapper))
            self.assertNotEqual(found, str(wrapper))


class Inputs(unittest.TestCase):
    def test_redacted_bounded_untrusted_and_watched_only(self):
        rig = Rig(summaries={"auto": True})
        rig.block()
        prepared = rig.collector.summary_input(rig.identity, "auto")
        self.assertNotIn("supersecret123", prepared["text"])
        self.assertIn("[redacted]", prepared["text"])
        self.assertIn("Do you want to proceed?", prepared["text"])
        self.assertNotIn("╭", prepared["text"], "screen is cleaned before the model sees it")
        self.assertIsNone(rig.collector.summary_input(f"codex:{sid('hidden')}", "auto"), "unwatched: nothing")
        text, info = material({"name": "x"}, [{"role": "user", "at": 1, "text": "x" * 5000}] * 10, "", 12000)
        self.assertLessEqual(len(text), 12000)
        self.assertEqual(info["entries"], 2)
        rig.collector.queue_summaries()
        rig.run_all()
        self.assertTrue(rig.calls[0]["prompt"].startswith("<untrusted>"))
        self.assertNotIn("supersecret123", rig.calls[0]["prompt"])


class Triggers(unittest.TestCase):
    def test_one_summary_per_item_never_per_refresh(self):
        rig = Rig(summaries={"auto": True})
        rig.block()
        for _ in range(5):
            rig.collector.queue_summaries()
            rig.collector.view(local=False)
        done = rig.run_all()
        self.assertEqual(len(done), 1)
        self.assertEqual(len(rig.calls), 1)
        for _ in range(5):
            rig.collector.queue_summaries()
        self.assertEqual(rig.run_all(), [])

    def test_identical_input_is_reused_not_recalled(self):
        rig = Rig(summaries={"auto": True})
        rig.block()
        rig.collector.queue_summaries()
        rig.run_all()
        rig.collector.request_summary(rig.identity, "refresh")
        [again] = rig.run_all()
        self.assertEqual(len(rig.calls), 1, "same content hash: no second model call")
        self.assertEqual(again["status"], "complete")
        self.assertIsNotNone(again["cached_from"])

    def test_coalescing_and_rate_limit(self):
        rig = Rig(summaries={"auto": True, "coalesce_seconds": 30, "max_per_hour": 1})
        rig.block()
        rig.collector.queue_summaries()
        self.assertIsNone(rig.collector.summarizer.next_job(), "waits for the quiet period")
        rig.clock.advance(31)
        rig.run_all()
        rig.collector.summarizer.request(rig.identity, None, "longer")
        self.assertIsNone(rig.collector.summarizer.next_job(), "hourly limit reached")
        rig.clock.advance(3601)
        self.assertIsNotNone(rig.collector.summarizer.next_job())

    def test_disabled_means_no_calls(self):
        rig = Rig(summaries={"enabled": False, "auto": True})
        rig.block()
        rig.collector.queue_summaries()
        self.assertEqual(rig.run_all(), [])
        self.assertEqual(rig.collector.summarizer.request(rig.identity, None, "refresh")["status"], "skipped")

    def test_states_failed_and_retry(self):
        outcomes = iter([reply({}, error=True), "not json", reply(GOOD)])
        rig = Rig(summaries={"auto": True}, runner=lambda command, prompt: next(outcomes))
        rig.block()
        rig.collector.queue_summaries()
        [first] = rig.run_all()
        self.assertEqual((first["status"], first["error"][:28]), ("failed", "the CLI reported an error (e"))
        rig.collector.request_summary(rig.identity, "refresh")
        [second] = rig.run_all()
        self.assertEqual(second["error"], "the CLI did not return JSON")
        rig.collector.request_summary(rig.identity, "refresh")
        [third] = rig.run_all()
        self.assertEqual(third["status"], "complete")
        compact = next(a for a in rig.collector.view(local=False)["agents"] if a["name"] == "ui")["summary"]
        self.assertEqual((compact["status"], compact["current"]), ("complete", True))

    def test_timeout_is_a_failure(self):
        def slow(command, prompt):
            raise subprocess.TimeoutExpired(command, 1)
        rig = Rig(summaries={"auto": True}, runner=slow)
        rig.block()
        rig.collector.queue_summaries()
        [job] = rig.run_all()
        self.assertEqual((job["status"], job["error"]), ("failed", "timed out"))

    def test_restart_marks_interrupted_runs(self):
        store = Store(":memory:")
        s = Summarizer(store, settings(summaries={"auto": True}))
        s.request("codex:x", 1)
        again = Summarizer(store, settings(summaries={"auto": True}))
        self.assertEqual(again.latest("codex:x")["error"], "interrupted by a restart")

    def test_audit_counts_without_money(self):
        rig = Rig(summaries={"auto": True})
        rig.block()
        rig.collector.queue_summaries()
        rig.run_all()
        audit = rig.collector.summary_audit()
        self.assertEqual((audit["model"], audit["effort"], audit["account"], audit["model_calls_24h"]), ("sonnet", "medium", "claude-personal", 1))
        self.assertEqual(audit["recent"][0]["resolved_model"], "claude-sonnet-test")
        self.assertEqual(audit["recent"][0]["usage"]["output_tokens"], 50)
        self.assertNotIn("cost", json.dumps(audit))



    def test_default_is_on_demand_only(self):
        rig = Rig()
        self.assertFalse(rig.collector.summarizer.auto)
        self.assertTrue(rig.collector.summarizer.enabled)
        rig.block()
        rig.collector.queue_summaries()
        self.assertEqual(rig.run_all(), [])
        self.assertEqual(rig.calls, [])
        rig.collector.request_summary(rig.identity, "refresh")
        self.assertEqual(len(rig.run_all()), 1)
        self.assertEqual(len(rig.calls), 1)

    def test_codex_command_is_isolated(self):
        from herdr_attention.llm import build_command, clean_env
        cmd = build_command("/x/codex", "gpt-6.2-sol", "low", provider="codex", last_message="/tmp/last")
        self.assertEqual(cmd[0], "/x/codex")
        self.assertIn("exec", cmd)
        self.assertIn("--ephemeral", cmd)
        self.assertIn("--json", cmd)
        self.assertIn("-s", cmd)
        os.environ["CODEX_ACCESS_TOKEN"] = "should-not-pass"
        try:
            env = clean_env("/home/x/.codex-personal", "codex")
        finally:
            del os.environ["CODEX_ACCESS_TOKEN"]
        self.assertEqual(env["CODEX_HOME"], "/home/x/.codex-personal")
        self.assertNotIn("CODEX_ACCESS_TOKEN", env)
        self.assertNotIn("ANTHROPIC_API_KEY", env)



class Verification(unittest.TestCase):
    source = "Do you want to proceed?\n1. Yes\n2. No\n12 passed\ncommitted abc123"

    def test_options_must_be_copied_from_the_source(self):
        checked = verify(GOOD, self.source, None, None)
        self.assertEqual(checked["needs_you"]["options"], ["1. Yes"])
        self.assertTrue(any("Delete the database" in d for d in checked["dropped"]))

    def test_tests_and_claims_need_quotes_and_are_labelled(self):
        checked = verify(GOOD, self.source, None, None)
        self.assertEqual([t["what"] for t in checked["tests"]], ["unit tests"])
        self.assertTrue(all("agent's claim" in t["status"] for t in checked["tests"]))
        self.assertEqual([c["text"] for c in checked["claims"]], ["Committed the change"])

    def test_commit_claims_are_checked_against_git(self):
        with tempfile.TemporaryDirectory() as repo:
            env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
            subprocess.run(["git", "init", "-q", repo], check=True)
            from herdr_attention.summarize import git_commits_since
            none = verify(GOOD, self.source, repo, time.time() - 60, git_commits_since)
            self.assertIn("not confirmed", none["claims"][0]["status"])
            Path(repo, "f").write_text("x")
            subprocess.run(["git", "-C", repo, "add", "f"], check=True, env=env)
            subprocess.run(["git", "-C", repo, "commit", "-qm", "cart page"], check=True, env=env)
            found = verify(GOOD, self.source, repo, time.time() - 60, git_commits_since)
            self.assertIn("checked: 1 new commit", found["claims"][0]["status"])

    def test_reply_parsing(self):
        summary, info = parse_reply(reply(GOOD))
        self.assertEqual(summary["headline"], GOOD["headline"])
        self.assertEqual(info["resolved_model"], "claude-sonnet-test")
        with self.assertRaises(ValueError):
            parse_reply(json.dumps({"subtype": "success", "result": "no json here"}))


if __name__ == "__main__":
    unittest.main()
