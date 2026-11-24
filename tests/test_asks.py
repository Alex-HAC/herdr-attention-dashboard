"""v0.9.6: the separate questions and decisions for Alex in an agent's last message."""
import unittest
from pathlib import Path

from herdr_attention.asks import answer_text, choices_for, extract, item_id
from tests.web_fixture import DOCS_LEAD_REPORT, IMPORT_CHECK_REPORT, RELEASE_NOTES_REPORT

FIXTURES = Path(__file__).parent / "fixtures" / "asks"
YES_NO = ["Yes", "No"]


def labels(item):
    return [c["label"] for c in item["choices"]]


class RealReports(unittest.TestCase):
    """The coordinator's own reports (copied from its transcript, 17 Nov 2026)."""

    def test_v095_report_gives_exactly_the_one_decision_at_the_bottom(self):
        text = (FIXTURES / "coordinator-v095-report.md").read_text()
        found = extract(text)
        self.assertEqual(found["state"], "questions")
        self.assertEqual(len(found["items"]), 1, [i["text"] for i in found["items"]])
        item = found["items"][0]
        self.assertTrue(item["quote"].startswith("**Decision for Alex:**"))
        self.assertIn("say if you want it", item["quote"])
        self.assertIn(item["quote"], text)
        self.assertIn("phone", item["text"] + item["context"])
        self.assertNotIn("**", item["text"] + item["context"])
        self.assertEqual(item["kind"], "decision")
        self.assertEqual(labels(item), YES_NO)
        self.assertTrue(found["summary"].startswith("v0.9.5 is installed and running"))

    def test_v094_report_offers_its_two_options(self):
        text = (FIXTURES / "coordinator-v094-theme.md").read_text()
        found = extract(text)
        self.assertEqual(len(found["items"]), 1)
        item = found["items"][0]
        self.assertEqual(labels(item), ["Merge v094-theme into main and install it as a small follow-up (0.9.4.1)", "Leave it"])
        self.assertTrue(item["quote"].startswith("Decision for Alex or Grok Bot:"))
        self.assertEqual(item["context"], "I recommend merging, since the brief asked for good contrast; it has not had an independent review.")

    def test_fixture_messages(self):
        lead = extract(DOCS_LEAD_REPORT)
        self.assertEqual([i["kind"] for i in lead["items"]], ["question", "question", "question", "decision"])
        self.assertEqual([labels(i) for i in lead["items"]],
                         [YES_NO, ["A) short snippets", "B) full runnable files"], [], YES_NO])
        self.assertEqual(lead["items"][0]["quote"], "Should I publish the docs site to the public URL today?")
        self.assertEqual(extract(RELEASE_NOTES_REPORT)["state"], "none")
        self.assertEqual(extract(RELEASE_NOTES_REPORT)["summary"], "Release notes for 2.3 are written and saved as `docs/releases/2.3.md`.")
        self.assertEqual(extract(IMPORT_CHECK_REPORT)["state"], "maybe")


class NeverInvent(unittest.TestCase):
    def test_every_quote_is_a_span_of_the_original(self):
        for text in (DOCS_LEAD_REPORT, (FIXTURES / "coordinator-v095-report.md").read_text()):
            for item in extract(text)["items"]:
                self.assertEqual(text[item["start"]:item["end"]], item["quote"])

    def test_ai_wording_needs_a_quote_from_the_message(self):
        text = (FIXTURES / "coordinator-v095-report.md").read_text()
        ai = {"headline": "0.9.5 is installed; one decision is open.", "asks": [
            {"text": "Add a phone alert when an account is nearly full?", "context": "It means more notifications.",
             "quote": "Adding one means more phone notifications, so I left it out; say if you want it."},
            {"text": "Restart the shell now?", "quote": "Please restart the shell now so the panel reloads."},   # not in the text
            {"text": "Should the image folders include Pictures?", "quote": "there is no Settings-page control for it yet"},
        ]}
        found = extract(text, ai=ai)
        self.assertEqual(found["summary"], "0.9.5 is installed; one decision is open.")
        texts = [i["text"] for i in found["items"]]
        self.assertIn("Add a phone alert when an account is nearly full?", texts, "AI wording for the rule item")
        self.assertNotIn("Restart the shell now?", texts, "a quote that is not in the message is dropped")
        self.assertIn("Should the image folders include Pictures?", texts, "an AI item with a real quote is kept")
        self.assertEqual(len(found["items"]), 2)
        for item in found["items"]:
            self.assertIn(item["quote"], text)

    def test_an_ai_ask_without_a_quote_is_only_a_maybe(self):
        found = extract(RELEASE_NOTES_REPORT, ai={"needs_you": {"kind": "decision", "ask": "Publish the notes?"}})
        self.assertEqual(found["state"], "maybe")
        self.assertEqual(found["items"], [])

    def test_code_quotes_and_other_people_are_not_asked(self):
        text = ("Here is the log:\n\n```\nShould I retry? (y/n)\n```\n\n> Do you want the old layout back?\n\n"
                "For Grok Bot: can you merge `v096-build` into main?\n\n**Questions for Grok Bot**\n1. Should I push?\n\n"
                "All tests pass.")
        self.assertEqual(extract(text)["items"], [])

    def test_for_example_is_not_a_person(self):
        found = extract("For example: should I keep the old route as well?")
        self.assertEqual(len(found["items"]), 1)


class Rules(unittest.TestCase):
    def test_numbered_questions_become_items_even_without_you(self):
        found = extract("Plan ready.\n\n1. What should the new page be called?\n2. Is Friday fine for the release?")
        self.assertEqual([i["text"] for i in found["items"]],
                         ["What should the new page be called?", "Is Friday fine for the release?"])
        self.assertEqual(labels(found["items"][1]), YES_NO)

    def test_a_question_mark_sentence_addressed_to_alex(self):
        found = extract("The import is done. Do you want me to delete the staging table? It holds 3 GB.")
        self.assertEqual(len(found["items"]), 1)
        self.assertEqual(found["items"][0]["quote"], "Do you want me to delete the staging table?")
        self.assertEqual(labels(found["items"][0]), YES_NO)

    def test_a_question_not_for_alex_makes_maybe(self):
        self.assertEqual(extract("Build is green. Why did the cache miss twice?")["state"], "maybe")
        self.assertEqual(extract("Build is green. Let me know if anything looks off.")["state"], "maybe")
        self.assertEqual(extract("Build is green. All 40 tests pass.")["state"], "none")

    def test_label_heading_over_a_paragraph(self):
        found = extract("## Decision for you\n\nKeep the old API for one more release, or drop it now.\n\nRest of the report.")
        self.assertEqual(len(found["items"]), 1)
        self.assertEqual(labels(found["items"][0]), ["Keep the old API for one more release", "Drop it now"])

    def test_inline_question_labels_and_offers(self):
        found = extract("- Question for Alex: which port should the phone use?\n- I can also add dark mode; say if you want it.")
        self.assertEqual([i["kind"] for i in found["items"]], ["question", "decision"])
        self.assertEqual(found["items"][0]["text"], "Which port should the phone use?")

    def test_the_direct_ask_of_the_tier_rules_is_an_item(self):
        found = extract("Migration script is written.\nPlease confirm you want me to run it on production.")
        self.assertEqual(len(found["items"]), 1)
        self.assertEqual(found["items"][0]["quote"], "Please confirm you want me to run it on production.")
        self.assertEqual(found["items"][0]["rule"], "direct ask")

    def test_the_tier_rule_skips_what_the_items_skip(self):
        from herdr_attention.attention import question_text
        text = "Done.\n\n```\nShould I retry? (y/n)\n```\n\nFor Grok Bot: can you merge it?"
        self.assertEqual(question_text(text), "")
        self.assertEqual(question_text("Done. Should I open the pull request?"), "Done. Should I open the pull request?")

    def test_long_text_splits_into_a_short_ask_and_one_line_of_context(self):
        found = extract("**Decision for Alex:** the nightly backup now takes 40 minutes because the media folder doubled in "
                        "size last week, which overlaps with the 03:00 deploy window on two servers. "
                        "Should I move the backup to 01:00?")
        item = found["items"][0]
        self.assertEqual(item["text"], "Should I move the backup to 01:00?")
        self.assertTrue(item["context"].startswith("The nightly backup now takes 40 minutes"))


class LongDirectAsk(unittest.TestCase):
    """review-096 B1: an ask on one line over 500 characters, without a question mark."""
    LINE = ("Please confirm that you approve deleting " + "the old build artefacts and caches in every worktree, " * 11
            + "before I continue.")
    TEXT = "Done.\n" + LINE

    def test_the_cut_quote_still_gives_an_item_with_the_whole_line(self):
        from herdr_attention.attention import question_text
        self.assertGreater(len(self.LINE), 500)
        self.assertTrue(question_text(self.TEXT).endswith("…"), "the tier rule quotes it cut")
        found = extract(self.TEXT)
        self.assertEqual(found["state"], "questions")
        [item] = found["items"]
        self.assertEqual(item["quote"], self.LINE, "the quote is the whole line, word for word")
        self.assertEqual(item["rule"], "direct ask")

    def test_it_keeps_waiting_on_you(self):
        from unittest import mock
        from herdr_attention import asks
        from herdr_attention.notify_tiers import asks_cap
        agent = {"status": "done", "last_message": self.TEXT,
                 "last_assistant": {"text": self.TEXT[:2000], "at": 100.0, "last_role": "assistant"}}
        self.assertEqual(asks_cap("waiting_on_you", agent, 110.0), "waiting_on_you")
        # Never lowered while the tier rule quotes a direct ask, whatever the items say.
        with mock.patch.object(asks, "extract", return_value={"state": "none", "items": []}):
            self.assertEqual(asks_cap("waiting_on_you", agent, 110.0), "waiting_on_you")


class Speed(unittest.TestCase):
    """review-096 S1: a hostile 24,000-character message parses well under 0.2 s, once."""
    SHAPES = {"brackets": "[", "links": "[a](", "for sam": "For Sam: x\n\n", "you?": "you? ", "numbered": "1. Why?\n",
              "labels": "Decision for Alex: x\n", "sections": "Questions for Sam\n- x?\n", "quotes": "> x\n",
              "fences": "```\n", "options": "A) x B) y ", "dots": ". ", "stars": "*", "newlines": "\n",
              "offers": "say if you want it. ", "or": "Decision for Alex: " + "a, or b. " * 2600}

    def test_hostile_messages_parse_fast(self):
        import time
        from herdr_attention import asks
        from herdr_attention.attention import question_text
        for name, piece in self.SHAPES.items():
            text = (piece * (24000 // len(piece) + 1))[:24000]
            asks._extract.cache_clear()
            asks.not_for_alex.cache_clear()
            start = time.perf_counter()
            extract(text)
            question_text(text)
            with self.subTest(shape=name):
                self.assertLess(time.perf_counter() - start, 0.2)

    def test_an_unchanged_message_is_parsed_once(self):
        from herdr_attention import asks
        text = (FIXTURES / "coordinator-v095-report.md").read_text()
        asks._extract.cache_clear()
        extract(text)
        extract(text)
        self.assertEqual(asks._extract.cache_info().misses, 1)

    def test_a_message_without_a_time_keys_items_by_its_text(self):
        from herdr_attention.asks import message_key
        self.assertEqual(message_key(12.5, "a"), 12.5)
        self.assertNotEqual(message_key(None, "Should I push?"), message_key(None, "Done. Should I push?"))


class Choices(unittest.TestCase):
    def test_choices_only_when_offered(self):
        self.assertEqual([c["label"] for c in choices_for("Should I merge now or wait for the review?", "question")],
                         ["Merge now", "Wait for the review"])
        self.assertEqual(choices_for("Which name should the page get?", "question"), [])
        self.assertEqual([c["label"] for c in choices_for("Option A: keep it. Option B: remove it.", "decision")],
                         ["A) keep it", "B) remove it"])
        self.assertEqual([c["value"] for c in choices_for("Deploy tonight (yes/no)?", "question")], ["yes", "no"])
        self.assertEqual(choices_for("Should I publish now or not?", "question")[0]["value"], "yes")


class Ids(unittest.TestCase):
    def test_ids_are_stable_and_differ_per_message(self):
        a = item_id("claude:x", 100.0, "Should I push?")
        self.assertEqual(a, item_id("claude:x", 100.0, "Should I push?"))
        self.assertNotEqual(a, item_id("claude:x", 101.0, "Should I push?"))
        self.assertNotEqual(a, item_id("claude:y", 100.0, "Should I push?"))

    def test_the_answer_names_the_question(self):
        self.assertEqual(answer_text({"text": "Should I push?"}, " yes "), 'Answer to "Should I push?": yes')


if __name__ == "__main__":
    unittest.main()
