"""v0.9.6 question items in the UI at 390 px: one row per question, answered one by one.

Data: page_fixture(asks=True) (the contract shape, docs/briefs/V0.9.6-ITEMS-CONTRACT.md):
docs-lead asks four things (one answered yes/no, an A/B, a free text, a decision),
release-notes reports with nothing to answer, import-check may want something.
"""
import json
import unittest

from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER, run

# The answer route comes from the server worker's contract key; until it is merged
# the tests fall back to the documented path. Answers like the server: the first
# call fails when asked to, the rest are sent.
ANSWER_STUB = """
UI.api.asks_answer ||= "/api/asks/answer";
const sent = [];
const stub = window.fetch;
window.fetch = async (path, init) => {
  const url = new URL(String(path), "http://x");
  if (url.pathname === UI.api.asks_answer && init?.method === "POST") {
    const body = JSON.parse(init.body); sent.push(body);
    if (window.__failNext) { window.__failNext = false;
      return {ok: true, status: 200, json: async () => ({answered: {text: body.text, at: Date.now() / 1000, delivery: "failed"}, error: "agent pane closed"})}; }
    return {ok: true, status: 200, json: async () => ({answered: {text: body.text, at: Date.now() / 1000, delivery: "sent"}})};
  }
  return stub(path, init);
};
const wait = (ms) => new Promise((ok) => setTimeout(ok, ms));
const row = (id) => document.querySelector(`main [data-reading-key="ask:${id}"]`);
const report = (out) => { const target = window.parent !== window ? window.parent.document.body : document.body; target.dataset.probe = JSON.stringify(out); };
window.__probed = true;
"""

LIST_SCRIPT = ANSWER_STUB + """
const out = {innerWidth: window.innerWidth};
const ids = %(items)s;
const block = document.querySelector("main #questions-for-you");
out.heading = block.querySelector("h2").textContent;
out.rows = [...block.querySelectorAll(".asks-card .ask-item")].map((li) => ({
  text: li.querySelector(".ask-text").textContent, route: li.querySelector(".ask-route").textContent,
  strong: [...li.querySelectorAll(".ask-text strong")].map((s) => s.textContent),
  quote: li.querySelector(".ask-quote .md")?.textContent || null,
  quoteStrong: [...li.querySelectorAll(".ask-quote strong")].map((s) => s.textContent),
  answered: li.classList.contains("is-answered"), replied: li.querySelector(".ask-replied")?.textContent || null,
  choices: [...li.querySelectorAll(".ask-choice")].map((b) => b.textContent), input: !!li.querySelector(".ask-input")}));
out.count = block.querySelector(".asks-card .asks-count").textContent;
out.terminalButtons = [...block.querySelectorAll(".question-card:not(.asks-card) .quick-send")].map((b) => b.textContent);
out.smallTargets = [...block.querySelectorAll(".ask-choice, .ask-send, .ask-input, .ask-original > summary")]
  .filter((el) => el.getBoundingClientRect().height < 44).map((el) => el.className);
out.rowsInCard = [...block.querySelectorAll(".ask-item")].every((li) => li.getBoundingClientRect().right <= block.getBoundingClientRect().right - 8);
// The attention rows, before any answer: calm for "maybe" and "done", red only with open questions.
const attention = (identity) => document.querySelector(`main .attention [data-reading-key^="attention:${identity}"]`);
const rowFacts = (identity) => { const r = attention(identity); return r && {dot: r.querySelector(".dot").className,
  reason: r.querySelector(".reason").textContent, detail: r.querySelector(".detail")?.textContent || "",
  reply: r.querySelector(".reply-delivery")?.textContent || null, replyButton: !!r.querySelector(".attention-row-actions .btn.primary")}; };
out.leadRow = rowFacts(%(lead)s); out.maybeRow = rowFacts(%(maybe)s); out.noneRow = rowFacts(%(none)s);
out.sendDisabled = row(ids[1]).querySelector(".ask-send").disabled;
// Choose B: selected, and Send names it. Nothing is sent yet.
[...row(ids[1]).querySelectorAll(".ask-choice")][1].click();
out.chosen = row(ids[1]).querySelector('.ask-choice[aria-checked="true"]')?.textContent;
out.sendLabel = row(ids[1]).querySelector(".ask-send").textContent;
out.sentBeforeSend = sent.length;
// Type in the free-text row; a live refresh keeps the words and the choice.
const input = row(ids[2]).querySelector(".ask-input");
input.focus(); input.value = "Docs Hub"; input.dispatchEvent(new Event("input", {bubbles: true}));
render(true); await refresh(true); render(true);
out.keptTyping = row(ids[2]).querySelector(".ask-input") === input && input.value === "Docs Hub";
out.keptChoice = row(ids[1]).querySelector('.ask-choice[aria-checked="true"]')?.textContent;
// The first send fails: the row says so and keeps its controls and choice.
window.__failNext = true;
row(ids[1]).querySelector(".ask-send").click();
await wait(300);
out.failed = row(ids[1]).querySelector(".error")?.textContent || "";
out.failedControls = !!row(ids[1]).querySelector(".ask-send");
out.choiceAfterFail = row(ids[1]).querySelector('.ask-choice[aria-checked="true"]')?.textContent;
// Send again: only this row is answered; the free-text row stays open with its words.
row(ids[1]).querySelector(".ask-send").click();
await wait(300);
out.repliedB = row(ids[1]).querySelector(".ask-replied")?.textContent || "";
out.freeStillOpen = row(ids[2]).querySelector(".ask-input")?.value === "Docs Hub";
out.decisionStillOpen = !!row(ids[3]).querySelector(".ask-send");
out.countAfter = document.querySelector("main .asks-card .asks-count").textContent;
out.headingAfter = document.querySelector("main #questions-for-you h2").textContent;
// Enter in the text box sends the typed words.
row(ids[2]).querySelector(".ask-input").dispatchEvent(new KeyboardEvent("keydown", {key: "Enter", bubbles: true}));
await wait(300);
out.repliedFree = row(ids[2]).querySelector(".ask-replied")?.textContent || "";
// A choice plus a note sends both.
row(ids[3]).querySelector(".ask-choice").click();
const note = row(ids[3]).querySelector(".ask-input");
note.value = "only the basic plan"; note.dispatchEvent(new Event("input", {bubbles: true}));
out.noteLabel = row(ids[3]).querySelector(".ask-send").textContent;
row(ids[3]).querySelector(".ask-send").click();
await wait(300);
out.sent = sent;
out.countDone = document.querySelector("main .asks-card .asks-count").textContent;
// Show original: rendered markdown, never raw markers.
const original = document.querySelector("main .asks-card .ask-original");
original.open = true;
out.originalText = original.querySelector(".md").textContent;
out.originalStrong = [...original.querySelectorAll("strong")].map((s) => s.textContent);
out.originalItems = [...original.querySelectorAll("ol li")].length;
out.anyRawStars = /\\*\\*/.test(document.querySelector("main").textContent);
out.anySentTo = /Sent to/.test(document.querySelector("main").textContent);
out.scrollWidth = document.documentElement.scrollWidth;
report(out);
"""


def fixture():
    data = page_fixture(asks=True)
    data["lead"] = next(a for a in data["view"]["agents"] if a["identity"] == data["ids"]["docs_lead"])
    return data


def page(script, route="/list", width=390, height=844, data=None):
    data = data or fixture()
    ids = data["ids"]
    values = {"items": json.dumps([i["id"] for i in data["lead"]["asks"]["items"]]), "lead": json.dumps(ids["docs_lead"]),
              "maybe": json.dumps(ids["import_check"]), "none": json.dumps(ids["release_notes"])}
    script = script.replace("%(nid)s", str(data.get("nid", 0)))
    return data, run(data, route, width, height, script=script % values)["probe"]


@unittest.skipUnless(BROWSER, "no Chromium found")
class QuestionRows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data, cls.probe = page(LIST_SCRIPT)
        cls.items = cls.data["lead"]["asks"]["items"]

    def test_one_plain_row_per_question_with_who_and_where(self):
        self.assertEqual(self.probe.get("innerWidth"), 390, self.probe)
        rows = self.probe["rows"]
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[1]["text"], "Which style do you want for code samples: A) short snippets or B) full runnable files?")
        self.assertEqual(rows[0]["route"], "docs-lead asks. Your answer goes to docs-lead in Docs / Docs v2.")
        self.assertEqual(rows[1]["choices"], ["A) short snippets", "B) full runnable files"])
        self.assertEqual((rows[2]["choices"], rows[2]["input"]), ([], True))
        self.assertEqual(rows[3]["choices"], ["Yes", "No"])
        self.assertEqual(rows[3]["text"], "The search box needs a paid plan, so I left it out. "
                                          "Adding it costs 20 euros a month; say if you want it.")

    def test_the_agents_own_words_show_when_the_question_was_reworded(self):
        rows = self.probe["rows"]
        self.assertEqual([r["quote"] for r in rows[:3]], [None, None, None])   # same words: no repeat
        self.assertEqual(rows[3]["quote"], "Decision for Alex: the search box needs a paid plan, so I left it out. "
                                           "Adding it costs 20 euros a month; say if you want it.")
        self.assertEqual(rows[3]["quoteStrong"], ["Decision for Alex:"])
        self.assertTrue(rows[0]["answered"])
        self.assertRegex(rows[0]["replied"], r"^✓ You replied “yes” at \d")
        self.assertEqual(self.probe["count"], "1 of 4 answered")
        # Three open items plus the terminal dialog of ui-builder.
        self.assertEqual(self.probe["heading"], "Questions for you · 4")

    def test_terminal_permission_dialog_stays_as_in_095(self):
        question = next(q for q in self.data["view"]["questions"] if q["terminal"])
        self.assertEqual(self.probe["terminalButtons"], question["options"])

    def test_choice_selects_and_send_sends_only_that_row(self):
        self.assertTrue(self.probe["sendDisabled"])
        self.assertEqual(self.probe["chosen"], "B) full runnable files")
        self.assertEqual(self.probe["sendLabel"], "Send: B) full runnable files")
        self.assertEqual(self.probe["sentBeforeSend"], 0)
        ids = [i["id"] for i in self.items]
        self.assertEqual(self.probe["sent"], [
            {"item": ids[1], "text": "B) full runnable files"}, {"item": ids[1], "text": "B) full runnable files"},
            {"item": ids[2], "text": "Docs Hub"}, {"item": ids[3], "text": "yes: only the basic plan"}])
        self.assertRegex(self.probe["repliedB"], r"You replied “B\) full runnable files” at \d")
        self.assertTrue(self.probe["freeStillOpen"])
        self.assertTrue(self.probe["decisionStillOpen"])
        self.assertEqual(self.probe["countAfter"], "2 of 4 answered")
        self.assertEqual(self.probe["headingAfter"], "Questions for you · 3")
        self.assertRegex(self.probe["repliedFree"], r"You replied “Docs Hub” at \d")
        self.assertEqual(self.probe["noteLabel"], "Send")
        self.assertEqual(self.probe["countDone"], "4 of 4 answered")

    def test_failed_delivery_says_so_and_keeps_the_controls(self):
        self.assertIn("Not delivered: agent pane closed", self.probe["failed"])
        self.assertTrue(self.probe["failedControls"])
        self.assertEqual(self.probe["choiceAfterFail"], "B) full runnable files")

    def test_live_refresh_keeps_typing_and_choice(self):
        self.assertTrue(self.probe["keptTyping"])
        self.assertEqual(self.probe["keptChoice"], "B) full runnable files")

    def test_original_is_rendered_markdown(self):
        self.assertNotIn("**", self.probe["originalText"])
        self.assertIn("What changed", self.probe["originalStrong"])
        self.assertIn("Decision for Alex:", self.probe["originalStrong"])
        self.assertEqual(self.probe["originalItems"], 3)
        self.assertFalse(self.probe["anyRawStars"])

    def test_rows_are_calm_unless_a_question_is_open(self):
        lead, maybe, none = self.probe["leadRow"], self.probe["maybeRow"], self.probe["noneRow"]
        self.assertIn("s-needs_you", lead["dot"])
        self.assertEqual(lead["reason"], "1 of 4 answered")
        self.assertIn("Which style do you want for code samples", lead["detail"])
        self.assertFalse(lead["replyButton"])   # answered in the rows, not by a generic Reply
        self.assertNotIn("s-needs_you", maybe["dot"])
        self.assertEqual(maybe["reason"], "The agent may want something")
        self.assertIn("1,204 rows loaded", maybe["detail"])
        self.assertNotIn("s-needs_you", none["dot"])
        self.assertEqual(none["reason"], "Done, not reviewed")
        self.assertIn("docs/releases/2.3.md", none["detail"])
        self.assertNotIn("`", none["detail"])

    def test_last_reply_line_is_plain_and_local(self):
        self.assertRegex(self.probe["leadRow"]["reply"], r"^You replied “yes” at \d{1,2}[:.]\d\d")
        self.assertFalse(self.probe["anySentTo"])

    def test_fits_390_with_tap_size_controls(self):
        self.assertEqual(self.probe["smallTargets"], [])
        self.assertTrue(self.probe["rowsInCard"])
        self.assertLessEqual(self.probe["scrollWidth"], 390)


PAGES_SCRIPT = ANSWER_STUB + """
const out = {};
const text = () => document.querySelector("main").textContent;
// Agent page: the rows replace "Open the agent and decide"; no "none yet" placeholder.
location.hash = hashLink("agent", %(lead)s); await wait(400); render(true);
out.agentRows = document.querySelectorAll("main .summary .ask-item").length;
out.agentDeadEnd = /Open the agent and decide/.test(text());
out.agentNoneYet = /none yet/.test(text());
out.agentReply = document.querySelector("main .reply-delivery")?.textContent || null;
// A report with no question.
location.hash = hashLink("agent", %(none)s); await wait(400); render(true);
out.doneState = document.querySelector("main .asks-none .asks-state")?.textContent;
out.doneSummary = document.querySelector("main .asks-none > .md")?.textContent;
out.doneCode = [...document.querySelectorAll("main .asks-none > .md code")].map((c) => c.textContent);
out.donePills = [...document.querySelectorAll("main .detail-pills .pill")].map((p) => p.className);
// A long "maybe" message is cut at a word boundary, with More.
location.hash = hashLink("agent", %(maybe)s); await wait(400); render(true);
const maybe = () => document.querySelector("main .asks-maybe .md");
out.maybeState = document.querySelector("main .asks-maybe .asks-state")?.textContent;
out.maybeCut = maybe().querySelector("p").textContent;
out.maybeMore = maybe().querySelector(".md-more")?.textContent;
maybe().querySelector(".md-more").click(); await wait(50);
out.maybeFull = maybe().querySelector("p").textContent;
out.maybeLess = maybe().querySelector(".md-more")?.textContent;
render(true); await refresh(true); render(true);
out.maybeStaysOpen = maybe().querySelector(".md-more")?.textContent;
// Focused notification: the rows, not the dead end.
location.hash = hashLink("notification", String(%(nid)s)); await wait(500); render(true);
const card = document.querySelector("main [aria-current=true]");
out.notifRows = card ? card.querySelectorAll(".ask-item").length : -1;
out.notifDeadEnd = card ? /Open the agent and decide/.test(card.textContent) : null;
// The graph question card counts open questions and shows the rows.
location.hash = hashLink("graph"); await wait(500); render(true);
out.graphTitle = document.querySelector(".graph-questions summary")?.textContent;
out.graphRows = document.querySelectorAll(".graph-questions .ask-item").length;
// Clock words are English; the time uses the device format.
out.clockOld = clock(Date.now() / 1000 - 3 * 86400);
out.weekdays = UI.ask_items.weekdays;
out.replyTo = repliedText({text: "yes", at: Date.now() / 1000, to: "web-lead"}, {name: "docs-lead"});
report(out);
"""


@unittest.skipUnless(BROWSER, "no Chromium found")
class OtherSurfaces(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data = fixture()
        check = next(a for a in data["view"]["agents"] if a["identity"] == data["ids"]["import_check"])
        cls.long_text = ("The customer import finished and I compared every row with the source export. " * 12
                         + "Could the skipped rows be **test data**?")
        check["asks"]["original"] = cls.long_text
        data["nid"] = next(n["id"] for n in data["notifications"] if n["agent"]["identity"] == data["ids"]["docs_lead"])
        cls.data, cls.probe = page(PAGES_SCRIPT, data=data)

    def test_agent_page_has_rows_and_no_dead_end(self):
        self.assertEqual(self.probe["agentRows"], 4, self.probe)
        self.assertFalse(self.probe["agentDeadEnd"])
        self.assertFalse(self.probe["agentNoneYet"])
        self.assertRegex(self.probe["agentReply"], r"^You replied “yes” at \d")

    def test_done_report_is_done_not_reviewed_without_red(self):
        self.assertEqual(self.probe["doneState"], "Done, not reviewed")
        self.assertEqual(self.probe["doneSummary"], "Release notes for 2.3 are written and saved as docs/releases/2.3.md.")
        self.assertEqual(self.probe["doneCode"], ["docs/releases/2.3.md"])
        self.assertFalse(any("needs_you" in c for c in self.probe["donePills"]))

    def test_maybe_says_so_and_long_text_is_cut_at_a_word_with_more(self):
        self.assertEqual(self.probe["maybeState"], "The agent may want something")
        cut = self.probe["maybeCut"]
        self.assertTrue(cut.endswith("…"), cut)
        shown = cut[:-1]
        self.assertTrue(self.long_text.startswith(shown))
        self.assertEqual(self.long_text[len(shown)], " ")   # never mid-word
        self.assertEqual(self.probe["maybeMore"], "More")
        self.assertEqual(self.probe["maybeFull"], self.long_text.replace("**", ""))
        self.assertEqual(self.probe["maybeLess"], "Less")
        self.assertEqual(self.probe["maybeStaysOpen"], "Less")

    def test_notification_and_graph_show_the_rows(self):
        self.assertEqual(self.probe["notifRows"], 4)
        self.assertFalse(self.probe["notifDeadEnd"])
        self.assertEqual(self.probe["graphTitle"], "Questions for you · 4")
        self.assertEqual(self.probe["graphRows"], 4)

    def test_clock_words_are_english_and_the_reply_names_another_recipient(self):
        self.assertIn(self.probe["clockOld"].split(" ")[0], self.probe["weekdays"])
        self.assertRegex(self.probe["replyTo"], r"^You replied “yes” to web-lead at \d")


MARKDOWN_SCRIPT = ANSWER_STUB + """
const out = {};
const original = document.querySelector("main .asks-card .ask-original");
original.open = true;
const md = original.querySelector(".md");
out.images = document.querySelectorAll("main img[src='x'], main svg[onload], main [onerror]").length;
out.title = document.title;
out.text = md.textContent;
out.rows = [...md.querySelectorAll(".md-row")].map((p) => p.textContent);
out.headRow = md.querySelector(".md-row-head")?.textContent;
out.em = [...md.querySelectorAll("em")].map((e) => e.textContent);
out.code = [...md.querySelectorAll("code")].map((c) => c.textContent);
out.pre = md.querySelector("pre")?.textContent;
out.item = document.querySelectorAll("main .asks-card .ask-text")[1].textContent;
out.rewordedQuote = document.querySelectorAll("main .asks-card .ask-item")[2].querySelector(".ask-quote .md")?.textContent || null;
out.itemStrong = [...document.querySelectorAll("main .asks-card .ask-text")[1].querySelectorAll("strong")].map((s) => s.textContent);
report(out);
"""


@unittest.skipUnless(BROWSER, "no Chromium found")
class MarkdownSubset(unittest.TestCase):
    """Tables as plain rows, links as text, code; agent markup is text, never HTML."""

    @classmethod
    def setUpClass(cls):
        data = fixture()
        # (No closing script tag: it would end the harness's own fixture script.)
        evil = ('<img src=x onerror="document.title=1"> **<svg onload="document.title=2">bold</svg>** '
                '[x](javascript:alert(1))')
        data["lead"]["asks"]["original"] = "\n".join([
            "| Check | Result |", "|---|:---:|", "| Full suite | 735 passed |", "",
            "See [the report](https://example.invalid/r) and *the notes*, run `make test`.", "",
            "```", "pytest -q **not bold**", "```", "", evil])
        data["lead"]["asks"]["items"][1]["text"] = evil
        data["lead"]["asks"]["items"][2]["text"] = "Name for the landing page?"   # reworded, as the AI may
        cls.data, cls.probe = page(MARKDOWN_SCRIPT, data=data)

    def test_tables_links_and_code(self):
        self.assertEqual(self.probe["rows"], ["Check · Result", "Full suite · 735 passed"])
        self.assertEqual(self.probe["headRow"], "Check · Result")
        self.assertIn("See the report and the notes, run make test.", self.probe["text"])
        self.assertNotIn("example.invalid", self.probe["text"])
        self.assertEqual(self.probe["em"], ["the notes"])
        self.assertIn("make test", self.probe["code"])
        self.assertEqual(self.probe["pre"], "pytest -q **not bold**")   # code stays exact

    def test_agent_markup_is_text_never_html(self):
        self.assertEqual(self.probe["images"], 0)
        self.assertNotIn(self.probe["title"], ("1", "2"))
        self.assertIn('<img src=x onerror=', self.probe["text"])
        self.assertIn('<svg onload="document.title=2">bold</svg>', self.probe["text"])
        self.assertNotIn("javascript:", self.probe["text"])
        self.assertIn('<img src=x onerror=', self.probe["item"])

    def test_reworded_question_keeps_the_exact_quote_in_its_row(self):
        self.assertEqual(self.probe["rewordedQuote"], "What name should the landing page use for the product?")
        # Item text is markdown too: bold, never literal stars.
        self.assertEqual(self.probe["itemStrong"], ['<svg onload="document.title=2">bold</svg>'])
        self.assertNotIn("**", self.probe["item"])


if __name__ == "__main__":
    unittest.main()


LATER_SCRIPT = ANSWER_STUB + r"""
const rows = [...document.querySelectorAll("main #questions-for-you .asks-card .ask-item")];
report({badges: rows.map((r) => r.querySelector(".ask-later")?.textContent || null),
        title: rows.map((r) => r.querySelector(".ask-later")?.title || null)[2]});
"""


@unittest.skipUnless(BROWSER, "no Chromium found")
class ForLaterBadge(unittest.TestCase):
    """v0.9.8.1: an ask the agent says can wait has a quiet "For later" badge and stays a card."""

    def test_badge_only_on_the_later_item(self):
        data = fixture()
        data["lead"]["asks"]["items"][2]["urgency"] = "later"
        _, probe = page(LATER_SCRIPT, data=data)
        self.assertEqual(probe["badges"], [None, None, "For later", None])
        self.assertEqual(probe["title"], "The agent says this can wait")
