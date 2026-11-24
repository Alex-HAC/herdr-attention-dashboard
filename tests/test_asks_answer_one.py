"""v0.9.8.6: answering one question card resolves that card only.

Seen 19 Nov 21:03: Alex answered "May it go in as a PR?" on the Placement coordinator's card and the
agent's other open ask disappeared too. The card answer arrives in the agent's conversation as a user
turn ('Answer to "…": yes'); the 0.9.8.1 rule counted it as "Alex wrote to the agent" and dropped
every earlier message with an ask. A card answer no longer counts as that; a free message still does.
"""
import unittest

from herdr_attention import asks
from herdr_attention.asks import is_item_answer
from herdr_attention.collector import Collector
from tests.helpers import Clock, FakeHerdr, agent, settings

from tests.test_asks_statements import REPORT as TWO_ASKS   # a coordinator report with asks for Alex


class AnswerTurns(unittest.TestCase):
    def test_card_answers_are_not_free_messages(self):
        fmt = asks.CONTRACT["ask_items"]["answer_format"]
        self.assertTrue(is_item_answer(fmt.replace("{text}", "May it go in as a PR?").replace("{answer}", "yes")))
        self.assertTrue(is_item_answer('Answer to "A?": yes\nAnswer to "B?": no'))
        self.assertTrue(is_item_answer('Answer to "A?": yes, and keep the old flag\nfor now'))
        self.assertFalse(is_item_answer("ok, go ahead with both"))
        self.assertFalse(is_item_answer(""))


class OneAnswerKeepsTheOtherAsk(unittest.TestCase):
    def setUp(self):
        self.collector = Collector(db_path=":memory:", client=FakeHerdr([agent("w1:p1", "idle", session="coord", name="Coord")]),
                                   settings=settings(), clock=Clock())
        self.addCleanup(self.collector.store.close)
        self.collector.handle_snapshot()
        self.identity = next(iter(self.collector.engine.agents))

    def read(self, entries):
        self.collector.traces.conversation = lambda *a, **k: {"entries": entries}
        self.collector._refresh_last_assistant(self.identity)
        return [m["at"] for m in self.collector.engine.agents[self.identity].get("earlier_messages") or []]

    def test_a_card_answer_keeps_the_earlier_ask_message(self):
        ask = {"role": "assistant", "text": TWO_ASKS, "at": 1000.0}
        self.assertEqual(asks.extract(TWO_ASKS)["state"], "questions")
        answer = {"role": "user", "text": 'Answer to "May it go in as a PR?": yes', "at": 1010.0}
        after = {"role": "assistant", "text": "Opening the PR now.", "at": 1020.0}
        self.assertEqual(self.read([ask, answer, after]), [1000.0], "the other ask stays")

    def test_a_free_message_from_alex_still_clears(self):
        ask = {"role": "assistant", "text": TWO_ASKS, "at": 1000.0}
        free = {"role": "user", "text": "ok, go ahead with both", "at": 1010.0}
        after = {"role": "assistant", "text": "Doing both.", "at": 1020.0}
        self.assertEqual(self.read([ask, free, after]), [])


if __name__ == "__main__":
    unittest.main()
