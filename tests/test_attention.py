"""Attention tiers: what actually needs Alex vs a coordinator vs FYI."""

import unittest

from herdr_attention.attention import (
    classify_tier, looks_like_user_question, snippet_of, summary_asks_user,
)
from tests.helpers import settings


def agent(**extra):
    base = {
        "identity": "codex:worker",
        "name": "worker-1",
        "status": "done",
        "kind": "completion",
        "present": True,
        "acked": False,
        "blocked_rule": None,
    }
    base.update(extra)
    return base


LINKS = [{
    "kind": "reports_to", "from": "codex:worker", "to": "claude:coord",
    "to_name": "coord-claude-app-prs", "hidden": False, "cycle": False,
}]
CONF = settings()


class Heuristics(unittest.TestCase):
    def test_direct_question(self):
        self.assertTrue(looks_like_user_question("The tests passed.\nShould I open the pull request?"))
        self.assertTrue(looks_like_user_question("Please confirm you want me to delete the staging database."))
        self.assertFalse(looks_like_user_question(
            "App suites green. Registry verification remains blocked by remote API provisioning."))
        self.assertFalse(looks_like_user_question("The report is written. Ten ranked findings."))
        self.assertFalse(looks_like_user_question(""))

    def test_summary_ask(self):
        self.assertEqual(
            summary_asks_user({"kind": "approval", "ask": "Approve the cleanup after the pre-release."}),
            "Approve the cleanup after the pre-release.",
        )
        self.assertIsNone(summary_asks_user({"kind": "blocker", "ask": "", "why": "API is down"}))
        self.assertIsNone(summary_asks_user({"kind": "approval", "ask": ""}))

    def test_snippet_shortens(self):
        self.assertEqual(snippet_of("  hello   world  "), "hello world")
        self.assertTrue(snippet_of("x" * 300).endswith("…"))


class Tiers(unittest.TestCase):
    def test_worker_finished_is_waiting_on_coordinator(self):
        t = classify_tier(agent(), settings=CONF, links=LINKS,
                          last_assistant="Implemented and tested. The remaining step belongs to the coordinator.")
        self.assertEqual(t.tier, "waiting_on_coordinator")
        self.assertIn("coord-claude-app-prs", t.reason)
        self.assertEqual(t.waiting_on["name"], "coord-claude-app-prs")
        self.assertIn("remaining step", t.snippet)

    def test_permission_prompt_is_waiting_on_you_even_with_coordinator(self):
        t = classify_tier(agent(status="blocked", kind="approval",
                                blocked_rule="bash_permission_prompt"),
                          settings=CONF, links=LINKS,
                          last_assistant="Allow this bash command?")
        self.assertEqual(t.tier, "waiting_on_you")
        self.assertIn("permission", t.reason.lower())

    def test_top_level_finished_is_fyi(self):
        t = classify_tier(agent(identity="codex:solo"), settings=CONF, links=[],
                          last_assistant="Wrote the survey report.")
        self.assertEqual(t.tier, "fyi_finished")
        self.assertIn("finished a turn", t.reason.lower())

    def test_summary_without_a_current_quoted_question_is_not_waiting_on_you(self):
        t = classify_tier(agent(identity="claude:coord", status="idle", kind="idle"),
                          settings=CONF, links=[],
                          last_assistant="The VM worker reported the pre-release as prepared, but I did not give the go.",
                          summary_needs={"kind": "approval",
                                         "ask": "Approve the proposed duplication cleanup after the pre-release."})
        self.assertIsNone(t)

    def test_answered_ask_is_not_waiting(self):
        t = classify_tier(agent(identity="claude:coord", status="idle", kind="idle"),
                          settings=CONF, links=[], last_role="user",
                          last_assistant="Should I approve the cleanup?",
                          summary_needs={"kind": "approval", "ask": "Approve the cleanup?"})
        self.assertIsNone(t)

    def test_stall_is_might_look(self):
        t = classify_tier(agent(status="working", kind="stall"), settings=CONF, links=[])
        self.assertEqual(t.tier, "might_look")
        self.assertIn("not changed", t.reason)

    def test_unclear_block_is_might_look(self):
        t = classify_tier(agent(status="blocked", kind="blocked", blocked_rule="mystery_screen"),
                          settings=CONF, links=[])
        self.assertEqual(t.tier, "might_look")
        self.assertIn("unclear", t.reason.lower())

    def test_question_form_is_waiting_on_you(self):
        t = classify_tier(agent(status="blocked", kind="question",
                                blocked_rule="mcp_elicitation_prompt"),
                          settings=CONF, links=[],
                          last_assistant="Which option do you want?")
        self.assertEqual(t.tier, "waiting_on_you")

    def test_gone_while_busy_is_might_look(self):
        t = classify_tier(agent(present=False, kind="failure", status="working"),
                          settings=CONF, links=[])
        self.assertEqual(t.tier, "might_look")
        self.assertIn("disappeared", t.reason)

    def test_acked_completion_drops_out(self):
        self.assertIsNone(classify_tier(agent(acked=True), settings=CONF, links=[]))

    def test_working_agent_is_not_in_the_strip(self):
        self.assertIsNone(classify_tier(agent(status="working", kind="working"),
                                        settings=CONF, links=[]))

    def test_worker_question_goes_to_coordinator(self):
        t = classify_tier(agent(status="idle", kind="idle"), settings=CONF, links=LINKS,
                          last_assistant="I am done with the survey. Should I merge the host PR?")
        self.assertEqual(t.tier, "waiting_on_coordinator")

    def test_stale_summary_ask_on_a_parked_agent_is_ignored(self):
        # Idle far longer than the parked window; only evidence is an old summary ask.
        now = 10_000
        t = classify_tier(agent(identity="claude:old", status="idle", kind="idle",
                                last_activity_at=now - 3 * 3600),
                          settings=CONF, links=[], now=now,
                          last_assistant="The report is written.",
                          summary_needs={"kind": "decision",
                                         "ask": "The terminal shows a weekly limit prompt: wait until Nov 11?"})
        self.assertIsNone(t)

    def test_recent_idle_does_not_make_an_inferred_summary_ask_authoritative(self):
        now = 10_000
        t = classify_tier(agent(identity="claude:coord", status="idle", kind="idle",
                                last_activity_at=now - 10 * 60),
                          settings=CONF, links=[], now=now,
                          last_assistant="I did not give the go.",
                          summary_needs={"kind": "approval",
                                         "ask": "Approve the proposed duplication cleanup after the pre-release."})
        self.assertIsNone(t)
