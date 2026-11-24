"""v0.9.8.2: one ask is one card.

On 19 Nov the dashboard showed 206 open questions: one coordinator's two (false) asks, copied
onto each of its 102 finished in-process subagent cards (a subagent card is a copy of its
parent's). Both asks were not for Alex either: a handover listing the jobs given to another
agent ("prepare one sealed install for you to choose from"; "check its contract first. Is it
the same family as X?")."""
import unittest

from herdr_attention import asks

HANDOVER = """The work coordinator has the job and is running.

**The four jobs I set, in order**
1. **Don't lose the demo fixes.** Reconcile the patch records with the drafts and port what is missing.
2. **Prepare placement:** check read-only what's installed on the rig, fix the findings that block policy loading, and prepare one sealed install for you to choose from, with every option off by default.
3. **When the new model arrives:** check its contract first. Is it the same family as XW-GC, and what inputs does it need, such as the target pose? Then tell you what it needs.
4. No robot contact beyond named read-only files until you give a go.

**I'm stopping here.** Talk to the work coordinator directly from now on."""


def parent(text="Which port should I use?", at=1000.0):
    return {"identity": "claude:lead", "name": "lead", "status": "idle", "present": True,
            "last_assistant": {"text": text, "at": at, "last_role": "assistant"}, "last_message": text}


class SubagentCopies(unittest.TestCase):
    def test_subagent_cards_do_not_repeat_the_parents_asks(self):
        lead = parent()
        lead["asks"] = asks.for_agent(lead, lead["last_message"], None, {})
        self.assertEqual(len(lead["asks"]["items"]), 1)
        subs = [dict(lead, identity=f"claude:lead/subagent/a{n:03d}", in_process=True, in_process_parent="claude:lead")
                for n in range(100)]
        view = {"agents": [lead] + subs, "questions": []}
        asks.decorate(view)
        open_items = [i for a in view["agents"] for i in ((a.get("asks") or {}).get("items") or [])]
        self.assertEqual(len(open_items), 1)
        self.assertTrue(all(s["asks"] is None for s in subs))

    def test_the_same_item_id_on_two_cards_is_shown_once(self):
        a = parent()
        a["asks"] = asks.for_agent(a, a["last_message"], None, {})
        b = dict(a, identity="claude:copy")
        view = {"agents": [a, b], "questions": []}
        asks.decorate(view)
        self.assertEqual(len(a["asks"]["items"]), 1)
        self.assertIsNone(b["asks"])


class OneAskOneCard(unittest.TestCase):
    def test_same_ask_matches_rewording_not_different_asks(self):
        self.assertTrue(asks.same_ask("Should I open the draft PR now?", "Should I open the draft PR now"))
        self.assertTrue(asks.same_ask("Open a draft PR on the deploy repo, or only hand him a patch?",
                                      "Open a draft PR on the deploy repo or only hand him the patch?"))
        self.assertFalse(asks.same_ask("Should I open the draft PR now?", "Should I close the draft PR now?"))
        self.assertFalse(asks.same_ask("Which port should I use?", "Which name should the page use?"))

    def test_an_ask_repeated_in_later_messages_is_one_card(self):
        agent = parent("Still waiting on this: should I open the draft PR on the deploy repo now?", at=1030.0)
        earlier = [{"text": "Tests pass. Should I open the draft PR on the deploy repo now?", "at": 1000.0},
                   {"text": "Reminder: should I open the draft PR on the deploy repo now?", "at": 1010.0}]
        result = asks.for_agent(agent, agent["last_message"], None, {}, earlier=earlier)
        self.assertEqual(len(result["items"]), 1, [i["text"] for i in result["items"]])
        self.assertEqual(result["items"][0]["source_at"] if "source_at" in result["items"][0] else 1030.0, 1030.0)

    def test_an_answered_copy_keeps_the_ask_answered(self):
        earlier = [{"text": "Should I open the draft PR on the deploy repo now?", "at": 1000.0}]
        first = asks.earlier_items(parent(), earlier, {"identity": "claude:lead", "name": "lead"}, {})
        answers = {first[0]["id"]: {"text": "yes", "at": 1001.0, "delivery": "sent"}}
        agent = parent("Should I open the draft PR on the deploy repo now?", at=1010.0)
        result = asks.for_agent(agent, agent["last_message"], None, answers, earlier=earlier)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["answered"]["text"], "yes")

    def test_a_model_ask_reworded_is_not_a_second_card(self):
        agent = parent("Waiting.", at=1010.0)
        msg = "Tests pass. Should I open the draft PR on the deploy repo now?"
        agent["asks"] = asks.for_agent(agent, "Waiting.", None, {}, earlier=[{"text": msg, "at": 1000.0}])
        added = asks.add_report_asks(agent, [{"text": "Open the draft PR on the deploy repo now?", "quote": "Tests pass.",
                                              "options": [], "urgency": "now"}], [{"text": msg, "at": 1000.0}], {})
        self.assertEqual(added, 0)
        self.assertEqual(len(agent["asks"]["items"]), 1)


class HandoverIsNotAsks(unittest.TestCase):
    def test_jobs_given_to_another_agent_are_not_questions_for_alex(self):
        found = asks.extract(HANDOVER)
        self.assertEqual(found["items"], [], [i["text"] for i in found["items"]])

    def test_real_asks_next_to_a_check_still_count(self):
        self.assertEqual(len(asks.extract("Check the logs. Do you want me to restart it?")["items"]), 1)
        self.assertEqual(len(asks.extract("1. Which port should I use?")["items"]), 1)
        self.assertEqual(len(asks.extract("Both are ready for you to choose between: A or B.")["items"]), 1)


if __name__ == "__main__":
    unittest.main()
