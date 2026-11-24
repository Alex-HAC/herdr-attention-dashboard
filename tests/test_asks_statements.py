"""v0.9.8.1: asks to Alex written as statements, mid-report, deferred, or followed by more messages.

The fixture is a real 23:43 coordinator status report (names changed). Its one ask has no
question mark: "One thing for you to decide tomorrow, not now: … Say if you would rather I
only hand him a patch." 0.9.8 missed it: only the last message was read, and the rules and
the report model did not count such statements."""
import unittest
from pathlib import Path

from herdr_attention import asks
from herdr_attention.collector import is_agent_message
from herdr_attention.config import Settings
from herdr_attention.notify_tiers import final_tier
from herdr_attention.team_reports import REPORT_SYSTEM, verify_report

REPORT = (Path(__file__).parent / "fixtures" / "asks" / "coordinator-statement-ask.md").read_text()
GATE_ASK = {"decision": "send", "tier": "waiting_on_you", "title": "Needs your decision"}


def coordinator(status="idle", text=REPORT, at=1000.0, last_role="assistant"):
    return {"identity": "claude:coord", "name": "fable-work", "status": status, "present": True,
            "last_assistant": {"text": text[:2000], "at": at, "last_role": last_role}, "last_message": text}


class StatementAsks(unittest.TestCase):
    def test_the_report_has_one_decision_with_both_choices(self):
        found = asks.extract(REPORT)
        self.assertEqual(found["state"], "questions")
        self.assertEqual(len(found["items"]), 1, found["items"])
        item = found["items"][0]
        self.assertEqual(item["kind"], "decision")
        self.assertIn("deploy-tools", item["text"])
        self.assertIn("only hand him a patch", item["text"])
        self.assertEqual([c["label"] for c in item["choices"]],
                         ["Open that port as a draft PR against his deploy-tools repo", "Only hand him a patch"])
        self.assertEqual(item["urgency"], "later")
        self.assertIn(item["quote"], REPORT)
        self.assertIn("Say if you would rather I only hand him a patch", item["quote"])

    def test_statement_and_imperative_asks(self):
        for text in ("Done with the merge. Say if you would rather I wait for the review.",
                     "The cut is ready. It's your call whether we ship tonight.",
                     "Release notes are drafted; the publish step needs your go.",
                     "All checks pass. Let me know if you want the screenshots too.",
                     "Two options are open, up to you: keep the cache or drop it.",
                     "One more thing for you to confirm: the demo laptop stays on 1.2.9."):
            with self.subTest(text=text):
                self.assertEqual(asks.extract(text)["state"], "questions", asks.extract(text))

    def test_not_asks(self):
        for text in ("rel-1210: question: should I rebase on main first?\nAnswered: yes, rebase.",
                     "`port-core`: question: which branch?",
                     "Your call was right yesterday; the cache fix held.",
                     "Merged #269. Tests green. Next: the freeze.",
                     "I asked the worker to confirm whether the build is signed."):
            with self.subTest(text=text):
                self.assertEqual(asks.extract(text)["items"], [], asks.extract(text))

    def test_urgency(self):
        self.assertEqual(asks.urgency_of("Decide tomorrow, not now"), "later")
        self.assertEqual(asks.urgency_of("No rush: which name do you like?"), "later")
        self.assertIsNone(asks.urgency_of("Which name do you like?"))
        now = asks.extract("Release is blocked. Say if you would rather I revert.")["items"][0]
        self.assertIsNone(now["urgency"])


class EarlierMessages(unittest.TestCase):
    """The report was followed within seconds by replies to workers: the ask must stay."""

    def test_an_ask_in_an_earlier_message_stays(self):
        agent = coordinator(text="Told port-core to use a fresh clone.", at=1010.0)
        result = asks.for_agent(agent, agent["last_message"], None, {}, earlier=[{"text": REPORT, "at": 1000.0}])
        self.assertEqual(result["state"], "questions")
        self.assertEqual(len(result["items"]), 1)
        self.assertTrue(result["items"][0]["earlier"])
        self.assertEqual(result["items"][0]["urgency"], "later")

    def test_while_working_the_earlier_ask_stays_and_ids_are_stable(self):
        agent = coordinator(status="working", text="Answering port-runtime.", at=1010.0)
        one = asks.for_agent(agent, agent["last_message"], None, {}, earlier=[{"text": REPORT, "at": 1000.0}])
        two = asks.for_agent(coordinator(text="Waiting for the reviewer.", at=1020.0), None, None, {},
                             earlier=[{"text": REPORT, "at": 1000.0}, {"text": "Answering port-runtime.", "at": 1010.0}])
        self.assertEqual(one["items"][0]["id"], two["items"][0]["id"])

    def test_same_ask_twice_is_one_item(self):
        agent = coordinator(text=REPORT, at=1010.0)
        result = asks.for_agent(agent, REPORT, None, {}, earlier=[{"text": REPORT, "at": 1000.0}])
        self.assertEqual(len(result["items"]), 1)

    def test_no_earlier_and_no_ask_is_unchanged(self):
        agent = coordinator(text="Merged #269. Tests green.", at=1010.0)
        result = asks.for_agent(agent, agent["last_message"], None, {}, earlier=[{"text": "Started port-core.", "at": 1000.0}])
        self.assertNotEqual((result or {}).get("state"), "questions")

    def test_worker_messages_are_not_alex(self):
        names = {"port-core", "rel-1210"}
        self.assertTrue(is_agent_message("rel-1210: question: rebase first?", names))
        self.assertTrue(is_agent_message("port-core: done, 3 PRs open", names))
        self.assertTrue(is_agent_message("someworker: blocked on the lock", set()))
        self.assertTrue(is_agent_message("<task-notification>…</task-notification>", names))
        self.assertFalse(is_agent_message("ok, go ahead with the draft PR", names))
        self.assertFalse(is_agent_message("note: do the patch instead", set()))


class LaterIsNotAPhoneAlert(unittest.TestCase):
    def test_a_later_only_ask_is_finished_not_waiting_on_you(self):
        event = {"id": 1, "identity": "claude:coord", "type": "completion", "at": 1010.0, "evidence": {}}
        agent = dict(coordinator(at=1000.0), kind="completion", status="done")
        tier = final_tier(event, GATE_ASK, [], agent=agent, settings=Settings({}), now=1020.0)
        self.assertEqual(tier, "fyi_finished")

    def test_an_urgent_ask_still_is(self):
        event = {"id": 1, "identity": "claude:coord", "type": "completion", "at": 1010.0, "evidence": {}}
        text = "The cut is ready. Say if you would rather I wait for the review."
        agent = dict(coordinator(text=text, at=1000.0), kind="completion", status="done")
        self.assertEqual(final_tier(event, GATE_ASK, [], agent=agent, settings=Settings({}), now=1020.0), "waiting_on_you")


class ReportModelAsks(unittest.TestCase):
    def test_prompt_asks_for_owner_questions_separately(self):
        self.assertIn('"asks_for_owner" is required', REPORT_SYSTEM)
        self.assertIn("Say if you would rather", REPORT_SYSTEM)
        self.assertIn("question: ...", REPORT_SYSTEM)

    def test_verify_keeps_well_formed_asks(self):
        reply = {"done": [], "in_progress": [], "left": [], "why_it_matters": "x", "asks_for_owner": [
            {"text": "PR or patch?", "quote": "Say if you would rather I only hand him a patch.",
             "options": ["Open a draft PR", "Only hand him a patch", 3], "urgency": "later"},
            {"text": "", "quote": "x"}, "junk"]}
        out = verify_report(reply, {"status": "on track"}, REPORT)
        self.assertEqual(len(out["asks_for_owner"]), 1)
        self.assertEqual(out["asks_for_owner"][0]["options"], ["Open a draft PR", "Only hand him a patch"])
        self.assertEqual(verify_report({"done": []}, {"status": "on track"})["asks_for_owner"], [])

    def test_a_model_ask_needs_its_quote_in_the_messages(self):
        agent = coordinator(text="Waiting.", at=1010.0)
        agent["asks"] = None
        made_up = [{"text": "Ship tonight?", "quote": "Shall I ship tonight?", "options": [], "urgency": "now"}]
        self.assertEqual(asks.add_report_asks(agent, made_up, [{"text": REPORT, "at": 1000.0}], {}), 0)
        real = [{"text": "Draft PR or patch?", "quote": "Say if you would rather I only hand him a patch.",
                 "options": ["Open a draft PR", "Only hand him a patch"], "urgency": "later"}]
        self.assertEqual(asks.add_report_asks(agent, real, [{"text": REPORT, "at": 1000.0}], {}), 1)
        item = agent["asks"]["items"][0]
        self.assertEqual(item["urgency"], "later")
        self.assertTrue(item["from_report"])
        self.assertEqual([c["label"] for c in item["choices"]], ["Open a draft PR", "Only hand him a patch"])

    def test_the_model_ask_and_the_rule_item_are_one_card(self):
        agent = coordinator(text="Waiting.", at=1010.0)
        agent["asks"] = asks.for_agent(agent, "Waiting.", None, {}, earlier=[{"text": REPORT, "at": 1000.0}])
        real = [{"text": "Draft PR on Sam's repo, or a patch?", "quote": "Say if you would rather I only hand him a patch.",
                 "options": [], "urgency": "later"}]
        self.assertEqual(asks.add_report_asks(agent, real, [{"text": REPORT, "at": 1000.0}], {}), 0)
        self.assertEqual(len(agent["asks"]["items"]), 1)


if __name__ == "__main__":
    unittest.main()


class StickyEarlier(unittest.TestCase):
    """The engine keeps earlier messages with an ask after they leave the read window, until Alex writes."""

    def test_kept_until_alex_writes(self):
        from herdr_attention.collector import Collector
        from tests.helpers import Clock, FakeHerdr, agent, settings
        collector = Collector(db_path=":memory:", client=FakeHerdr([agent("w1:p1", "idle", session="lead", name="Lead")]),
                              settings=settings(), clock=Clock())
        self.addCleanup(collector.store.close)
        collector.handle_snapshot()
        engine = collector.engine
        identity = next(iter(engine.agents))
        engine.set_last_assistant(identity, "Waiting.", 1010.0, "assistant",
                                  earlier=[{"text": REPORT, "at": 1000.0}, {"text": "Started port-core.", "at": 1005.0}])
        self.assertEqual([m["at"] for m in engine.agents[identity]["earlier_messages"]], [1000.0])
        engine.agents[identity]["earlier_messages"].insert(0, {"text": "Old note, no ask.", "at": 990.0})   # 0.9.8.1 first cut kept all
        engine.set_last_assistant(identity, "Still waiting.", 1020.0, "assistant",
                                  earlier=[{"text": f"Note {n}.", "at": 1011.0 + n} for n in range(9)])
        self.assertEqual([m["at"] for m in engine.agents[identity]["earlier_messages"]], [1000.0], "out of the window: kept")
        engine.set_last_assistant(identity, "On it.", 1040.0, "assistant", earlier=[], alex_at=1030.0)
        self.assertEqual(engine.agents[identity]["earlier_messages"], [], "Alex wrote: cleared")


class CodexFailureReason(unittest.TestCase):
    def test_usage_limit_is_named_not_no_json(self):
        from herdr_attention.llm import parse_codex_reply
        raw = ('{"type":"error","message":"You hit your usage limit. Upgrade (https://example.com/pro) or try again at 4:31 AM."}\n'
               '{"type":"turn.failed","error":{"message":"You hit your usage limit. Try again at 4:31 AM."}}')
        with self.assertRaisesRegex(ValueError, r"^the CLI failed: You hit your usage limit\. Try again at 4:31 AM\.$"):
            parse_codex_reply(raw)
        with self.assertRaisesRegex(ValueError, "did not return JSON"):
            parse_codex_reply("not json")
