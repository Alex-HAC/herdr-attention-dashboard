"""v0.9.6 server side: agent.asks in the view, the tier cap in final_tier, answers, last_reply."""
import unittest
from pathlib import Path

from herdr_attention.config import Settings
from herdr_attention.notify_tiers import allowed_now, apply_preset, final_tier
from tests.web_fixture import DOCS_LEAD_REPORT, IMPORT_CHECK_REPORT, RELEASE_NOTES_REPORT, build

FIXTURES = Path(__file__).parent / "fixtures" / "asks"
V095 = (FIXTURES / "coordinator-v095-report.md").read_text()
GATE_ASK = {"decision": "send", "tier": "waiting_on_you", "title": "Needs your decision on the report"}


def finished(text, at=1000.0, **extra):
    """An engine agent (as notify.py gets it) that ended its turn with ``text``."""
    return dict({"identity": "claude:coord", "kind": "completion", "status": "done", "present": True,
                 "last_assistant": {"text": text[:2000], "at": at, "last_role": "assistant"},
                 "last_message": text}, **extra)


class TierCap(unittest.TestCase):
    """One stored tier (final_tier): the items may lower "Waiting on you", never raise a tier."""

    def setUp(self):
        self.settings = Settings({})
        self.event = {"id": 1, "identity": "claude:coord", "type": "completion", "at": 1010.0, "evidence": {}}

    def tier(self, agent, gate=GATE_ASK):
        return final_tier(self.event, gate, [], agent=agent, settings=self.settings, now=1020.0)

    def test_a_report_with_no_question_is_finished_not_waiting_on_you(self):
        self.assertEqual(self.tier(finished(RELEASE_NOTES_REPORT)), "fyi_finished")

    def test_unsure_is_might_want_a_look_without_a_red_label(self):
        self.assertEqual(self.tier(finished(IMPORT_CHECK_REPORT)), "might_look")

    def test_a_message_with_items_keeps_waiting_on_you(self):
        self.assertEqual(self.tier(finished(V095)), "waiting_on_you")
        self.assertEqual(self.tier(finished(DOCS_LEAD_REPORT), gate=None), "waiting_on_you")

    def test_items_never_raise_a_tier(self):
        # The 11:41 report has a decision, but without the gate's ask the rule tier is Finished: it stays so.
        self.assertEqual(self.tier(finished(V095), gate=None), "fyi_finished")

    def test_only_a_finished_current_message_is_judged(self):
        stale = finished(RELEASE_NOTES_REPORT, at=self.event["at"] - 3600)
        self.assertEqual(self.tier(stale), "waiting_on_you", "the transcript has not caught up: no change")
        self.assertEqual(self.tier(finished(RELEASE_NOTES_REPORT, status="blocked", kind="approval")), "waiting_on_you")
        self.assertEqual(self.tier(None), "waiting_on_you")

    def test_the_stored_tier_wins(self):
        event = dict(self.event, evidence={"final_tier": "waiting_on_you"})
        self.assertEqual(final_tier(event, None, [], agent=finished(RELEASE_NOTES_REPORT), settings=self.settings), "waiting_on_you")

    def test_phone_volume_is_the_same_or_lower(self):
        for preset in ("quiet", "normal", "close_tabs", "everything"):
            settings = Settings({})
            for key, value in apply_preset(preset).items():
                settings.set_override(key, value)
            with self.subTest(preset=preset):
                self.assertIsNone(allowed_now(settings, "waiting_on_you", "claude:coord", 0))
                for capped in ("fyi_finished", "might_look"):
                    # Never sent where Waiting on you would not be; in Only me it is not sent at all.
                    if preset == "quiet":
                        self.assertEqual(allowed_now(settings, capped, "claude:coord", 0), "off")


class ViewAsks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = build(asks=True)
        cls.view = cls.data["view"]
        cls.by_name = {a.get("name"): a for a in cls.view["agents"]}

    def test_items_carry_the_contract_fields(self):
        lead = self.by_name["docs-lead"]
        asks = lead["asks"]
        self.assertEqual(asks["state"], "questions")
        self.assertEqual(asks["original"], DOCS_LEAD_REPORT)
        self.assertEqual(asks["source_at"], lead["last_assistant"]["at"])
        self.assertEqual(len(asks["items"]), 4)
        for item in asks["items"]:
            self.assertEqual(set(item) - {"image", "earlier", "source_at", "from_report"},
                             {"id", "text", "context", "quote", "choices", "kind", "asked_by",
                              "answer_to", "answered", "urgency"})
            self.assertIn(item["quote"], asks["original"])
            self.assertEqual(item["asked_by"], {"identity": lead["identity"], "name": "docs-lead"})
            self.assertEqual(item["answer_to"], {"identity": lead["identity"], "name": "docs-lead", "where": "Docs / Docs v2"})
        self.assertEqual(len({i["id"] for i in asks["items"]}), 4)
        question = next(q for q in self.view["questions"] if q["identity"] == lead["identity"])
        self.assertEqual([i["id"] for i in question["items"]], [i["id"] for i in asks["items"]], "0.9.5 questions carry the items")

    def test_report_and_unsure_agents(self):
        notes, check = self.by_name["release-notes"], self.by_name["import-check"]
        self.assertEqual((notes["tier"], notes["asks"]["state"], notes["asks"]["items"]), ("fyi_finished", "none", []))
        self.assertTrue(notes["asks"]["summary"].startswith("Release notes for 2.3"))
        self.assertEqual(check["asks"]["state"], "maybe")
        self.assertNotEqual(check["tier"], "waiting_on_you")

    def test_permission_dialog_stays_one_item_with_its_own_choices(self):
        ui = self.by_name["ui-builder"]
        [item] = ui["asks"]["items"]
        self.assertEqual(item["kind"], "permission")
        self.assertEqual([c["value"] for c in item["choices"]], ui["question"]["options"])
        self.assertIn(item["quote"], ui["asks"]["original"])
        self.assertEqual(item["answer_to"]["name"], "web-lead", "routed as in 0.9.5: its lead answers")

    def test_a_worker_with_a_registered_lead_has_no_items_for_alex(self):
        self.assertNotIn("asks", self.by_name["etl"])
        self.assertNotIn("asks", self.by_name["docs-writer"], "nothing to answer and not in the list")

    def test_a_view_never_parses_an_unchanged_message_again(self):
        from herdr_attention import asks
        collector = self.data["collector"]
        collector.view(local=False)
        misses = asks._extract.cache_info().misses, asks.not_for_alex.cache_info().misses
        collector.view(local=False)
        collector.view(local=False)
        self.assertEqual((asks._extract.cache_info().misses, asks.not_for_alex.cache_info().misses), misses)

    def test_the_whole_message_is_read_not_only_2000_characters(self):
        collector = self.data["collector"]
        lead = self.by_name["docs-lead"]["identity"]
        collector.engine.set_last_assistant(lead, V095, at=self.by_name["docs-lead"]["last_assistant"]["at"] + 60)
        agent = next(a for a in collector.view(local=False)["agents"] if a["identity"] == lead)
        self.assertGreater(len(V095), 2000)
        self.assertEqual(len(agent["last_assistant"]["text"]), 2000)
        self.assertEqual(agent["asks"]["original"], V095)
        self.assertEqual(len(agent["asks"]["items"]), 1)
        self.assertIn("say if you want it", agent["asks"]["items"][0]["quote"])
        collector.engine.set_last_assistant(lead, DOCS_LEAD_REPORT, at=self.by_name["docs-lead"]["last_assistant"]["at"])


def lead_of(collector):
    return next(a for a in collector.view(local=False)["agents"] if a.get("name") == "docs-lead")


class Answering(unittest.TestCase):
    """POST /api/asks/answer: one item at a time through the reply routing."""

    def setUp(self):
        from tests.test_replies import FakePrompt
        self.data = build(asks=True)
        self.c = self.data["collector"]
        self.sender = FakePrompt()
        self.c.replies._sender = self.sender
        self.items = lead_of(self.c)["asks"]["items"]

    def test_the_fixture_answered_the_first_item_through_the_route(self):
        self.assertEqual({k: self.items[0]["answered"][k] for k in ("text", "delivery")}, {"text": "yes", "delivery": "sent"})
        self.assertEqual([i["answered"] for i in self.items[1:]], [None, None, None])

    def test_one_answer_names_the_question_and_leaves_the_others_open(self):
        result = self.c.answer_ask(self.items[2]["id"], " Herdr Docs ")
        self.assertEqual(result["status"], "sent")
        self.assertEqual(result["answered"]["delivery"], "sent")
        self.assertEqual(self.sender.calls,
                         [("w3:p2", 'Answer to "What name should the landing page use for the product?": Herdr Docs')])
        lead = lead_of(self.c)
        answered = [i["answered"] for i in lead["asks"]["items"]]
        self.assertEqual(answered[2]["text"], "Herdr Docs")
        self.assertEqual([answered[1], answered[3]], [None, None], "the other items stay open")
        self.assertEqual(lead["tier"], "waiting_on_you")
        self.assertFalse(lead["acked"], "the notification stays open while items are open")
        self.assertEqual({k: lead["last_reply"][k] for k in ("text", "to")}, {"text": "Herdr Docs", "to": "docs-lead"})

    def test_the_last_open_item_marks_the_notification_read(self):
        for item, answer in zip(self.items[1:], ("B) full runnable files", "Herdr Docs", "no")):
            self.c.answer_ask(item["id"], answer)
        self.assertEqual(len(self.sender.calls), 3)
        self.assertTrue(lead_of(self.c)["acked"])
        self.assertNotEqual(lead_of(self.c).get("tier"), "waiting_on_you")

    def test_a_busy_recipient_gets_it_queued_not_interrupted(self):
        from tests.helpers import agent
        herdr = self.c.client
        herdr.set(dict(herdr.agent("w3:p2"), agent_status="working"))
        result = self.c.answer_ask(self.items[2]["id"], "Herdr Docs")
        self.assertEqual(result["status"], "queued")
        self.assertEqual(self.sender.calls, [])
        self.assertEqual(result["answered"]["delivery"], "queued")
        self.assertIsNotNone(agent)

    def test_refusals(self):
        with self.assertRaisesRegex(ValueError, "no longer open"):
            self.c.answer_ask("not-an-item", "yes")
        with self.assertRaisesRegex(ValueError, "empty"):
            self.c.answer_ask(self.items[1]["id"], "   ")
        with self.assertRaisesRegex(ValueError, "already answered"):
            self.c.answer_ask(self.items[0]["id"], "no")
        self.assertEqual(self.sender.calls, [])

    def test_a_failed_delivery_can_be_answered_again(self):
        from herdr_attention.herdr import HerdrError
        self.sender.fail = HerdrError("cannot reach Herdr")
        result = self.c.answer_ask(self.items[1]["id"], "A) short snippets")
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["answered"], "nothing is recorded as answered")
        self.assertEqual(self.c.answer_ask(self.items[1]["id"], "A) short snippets")["status"], "sent")

    def test_the_permission_item_is_routed_as_in_0_9_5(self):
        ui = next(a for a in self.c.view(local=False)["agents"] if a.get("name") == "ui-builder")
        [item] = ui["asks"]["items"]
        result = self.c.answer_ask(item["id"], "1. Yes")
        self.assertEqual(result["recipient"]["name"], "web-lead")
        self.assertIn(result["status"], ("sent", "queued"))
        self.assertEqual(self.c.replies.get(result["id"])["text"], 'Answer to "Run npm test in ./cart?": 1. Yes')

    def test_a_new_message_replaces_the_items_and_the_reply_line(self):
        self.c.answer_ask(self.items[2]["id"], "Herdr Docs")
        lead = lead_of(self.c)
        self.c.engine.set_last_assistant(lead["identity"], "Published. Should I also post it on the blog?",
                                         at=self.c.clock() + 30)
        self.c.clock.advance(60)
        lead = lead_of(self.c)
        self.assertEqual([i["text"] for i in lead["asks"]["items"]], ["Should I also post it on the blog?"])
        self.assertIsNone(lead["asks"]["items"][0]["answered"])
        self.assertIsNone(lead["last_reply"], "the reply is older than the agent's current message")


class AiWording(unittest.TestCase):
    """The AI summary that already runs may word the items; every quote is checked twice."""

    def test_the_summarizer_keeps_only_asks_with_a_quote(self):
        from herdr_attention.summarize import verify
        out = verify({"headline": "Report", "asks": [
            {"text": "Add a nearly-full phone alert?", "quote": "say if you want it", "kind": "decision"},
            {"text": "Invented?", "quote": "this sentence is not in the material"},
            {"text": "No quote"}]}, V095, None, None)
        self.assertEqual([a["text"] for a in out["asks"]], ["Add a nearly-full phone alert?"])
        self.assertEqual(len([d for d in out["dropped"] if d.startswith("ask without")]), 2)

    def test_items_take_the_ai_wording_only_for_a_quote_in_the_last_message(self):
        from herdr_attention.asks import for_agent
        agent = finished(V095, tier="waiting_on_you", summary={"status": "complete", "current": True,
            "headline": "0.9.5 is live; one decision is open", "asks": [
                {"text": "Add a phone alert when an account is nearly full?", "context": "More notifications.",
                 "quote": "Adding one means more phone notifications, so I left it out; say if you want it.", "kind": "decision"},
                {"text": "Restart the shell?", "quote": "Please restart the shell.", "kind": "question"}]})
        asks = for_agent(agent, V095, None, {})
        self.assertEqual(asks["summary"], "0.9.5 is live; one decision is open")
        self.assertEqual([i["text"] for i in asks["items"]], ["Add a phone alert when an account is nearly full?"])
        self.assertEqual(asks["items"][0]["context"], "More notifications.")
        self.assertEqual([c["label"] for c in asks["items"][0]["choices"]], ["Yes", "No"], "choices still from the text")
        stale = dict(agent, summary=dict(agent["summary"], current=False))
        self.assertTrue(for_agent(stale, V095, None, {})["items"][0]["text"].startswith("Adding one means"))


class AnswersKept(unittest.TestCase):
    def test_answers_survive_a_restart(self):
        import tempfile
        from herdr_attention import asks
        from herdr_attention.reply import ensure_schema
        from herdr_attention.store import Store
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "state.db")
            store = Store(path)
            ensure_schema(store)
            store._exec("INSERT INTO replies (at, identity, text, when_policy, status) VALUES (1, 'claude:a', 'x', 'now', 'sent')")
            asks.save_answer(store, "item-1", "claude:a", "yes", 5.0, 1)
            store.close()
            again = Store(path)
            self.assertEqual(asks.load_answers(again), {"item-1": {"text": "yes", "at": 5.0, "delivery": "sent"}})
            again.close()


class AnswerRoute(unittest.TestCase):
    """Same login and CSRF as every other write on the phone listener."""

    def setUp(self):
        from herdr_attention.api import PhoneServer, make_handler, serve
        from tests.test_api import make_collector
        self.collector = make_collector()
        self.server = PhoneServer(("127.0.0.1", 0), make_handler(self.collector, local=False))
        self.port = self.server.server_address[1]
        serve(self.server)

    def tearDown(self):
        self.server.stopping.set()
        self.server.shutdown()
        self.server.server_close()

    def request(self, body, cookie=None, csrf=None):
        import http.client
        import json
        from herdr_attention.ui_contract import API
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{self.port}"}
        if cookie:
            headers["Cookie"] = cookie
        if csrf:
            headers["X-CSRF-Token"] = csrf
        conn.request("POST", API["asks_answer"], body=json.dumps(body), headers=headers)
        response = conn.getresponse()
        data = json.loads(response.read() or b"{}")
        cookie_out = response.getheader("Set-Cookie")
        conn.close()
        return response.status, data, cookie_out

    def test_needs_login_and_csrf_and_refuses_an_unknown_item(self):
        import http.client
        import json
        from tests.test_api import PASSPHRASE
        self.assertEqual(self.request({"item": "x", "text": "yes"})[0], 401)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("POST", "/login", body=json.dumps({"passphrase": PASSPHRASE}),
                     headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{self.port}"})
        response = conn.getresponse()
        csrf = json.loads(response.read())["csrf"]
        cookie = response.getheader("Set-Cookie").split(";")[0]
        conn.close()
        self.assertEqual(self.request({"item": "x", "text": "yes"}, cookie=cookie)[0], 403)
        status, data, _ = self.request({"item": "x", "text": "yes"}, cookie=cookie, csrf=csrf)
        self.assertEqual(status, 400)
        self.assertIn("no longer open", data["error"])


if __name__ == "__main__":
    unittest.main()
