"""v0.9.7: where account icons show in the views (390 x 844, icon fixture).

Every icon comes from accountIcon() (class acct-icon, data-account); a view never draws one itself.
An agent without a recorded account shows nothing new. Compact places show one mark per agent.
"""

import copy
import re
import unittest
from pathlib import Path

from tests.icon_fixture import icon_fixture
from tests.test_graph_runtime import NODES, nodes_by_name
from tests.test_typing_survives_refresh import reading
from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER, run

PLUGIN = Path(__file__).resolve().parent.parent / "plugin" / "alexvdb.herdr-attention"

GRAPH = NODES.replace("document.body.dataset.reading = JSON.stringify({nodes,", """
const marks = groups.map(g => {
  const fo = g.querySelector('.acct-fo'), icon = fo.querySelector('.acct-icon');
  return {id: g.dataset.id, account: icon ? icon.dataset.account : null, img: !!(icon && icon.querySelector('img')),
    shown: shown(fo) && !!icon, pill: g.querySelector('.svc').getAttribute('visibility') === 'visible',
    px: icon ? Math.round(icon.getBoundingClientRect().width) : 0};
});
for (const g of groups) {
  const fo = g.querySelector('.acct-fo');
  if (!shown(fo) || !fo.firstChild) continue;
  const r = rect(fo);
  for (const l of labels) if (overlap(r, rect(l))) collisions.push([g.dataset.id, l.textContent]);
  for (const d of document.querySelectorAll('svg.graph .node .body')) if (d.closest('.node') !== g && overlap(r, rect(d))) collisions.push([g.dataset.id, 'dot']);
}
document.body.dataset.reading = JSON.stringify({marks, nodes,""")

PAGE = """
await new Promise(r => setTimeout(r, 500));
const icon = (el) => el ? el.querySelector('.acct-icon') : null;
const acct = (el) => { const i = icon(el); return i ? {account: i.dataset.account, text: i.classList.contains('acct-text'), img: !!i.querySelector('img')} : null; };
const runs = document.querySelector('[data-reading-key="runs-on"]');
const account = document.querySelector('[data-reading-key="runs-account"]');
const cards = Object.fromEntries([...document.querySelectorAll('.agent-with-focus')].map(c => [c.querySelector('.agent-name').firstChild.textContent,
  {corner: (c.querySelector('.avatar > .avatar-account') || {dataset: {}}).dataset.account || null, kind: (c.querySelector('.avatar > .kind') || {}).textContent || null}]));
const rows = Object.fromEntries([...document.querySelectorAll('.att-row')].map(r => [r.querySelector('.who').firstChild.textContent, acct(r.querySelector('.who'))]));
const question = document.querySelector('.question-card:not(.asks-card)');
const route = document.querySelector('.ask-route');
document.body.dataset.reading = JSON.stringify({
  runs: runs ? {chip: !!runs.querySelector('.svc-chip'), source: runs.querySelector('.runs-source').textContent,
    account: account ? {...acct(account), name: (account.querySelector('.runs-account-name') || {}).textContent || null} : null} : null,
  cards, rows,
  question: question ? {asker: acct(question.querySelector('.reply-row-head')), to: acct(question.querySelector('.recipient-chip')),
    toText: question.querySelector('.recipient-chip').textContent} : null,
  asksHead: acct(document.querySelector('.asks-head')),
  route: route ? {icons: [...route.querySelectorAll('.acct-icon')].map(i => i.dataset.account), title: route.title,
    text: [...route.childNodes].map(n => n.nodeType === 3 ? n.textContent : (n.lastChild ? n.lastChild.textContent : '')).join('')} : null,
  all: [...document.querySelectorAll('main .acct-icon')].filter(e => !e.closest('.quota-dials')).length,
  scroll: document.documentElement.scrollWidth, width: window.innerWidth});
"""


def agent(data, name):
    return next(a for a in data["view"]["agents"] if a.get("name") == name)


def with_icon(data, name, account_id):
    """Give one agent the icon of a quota account (as if its account were recorded)."""
    data = copy.deepcopy(data)
    icon = next(a for a in data["view"]["quotas"]["accounts"] if a["id"] == account_id)["icon"]
    agent(data, name)["icon"] = dict(icon)
    return data


def asks_fixture():
    """The fixture question as a plain question item (not a terminal dialog), so its row and route show."""
    data = icon_fixture()
    item = agent(data, "ui-builder")["asks"]["items"][0]
    item.update(kind="question", text="Which date range should the tests cover?", quote="Which date range should the tests cover?",
                choices=[])
    return data


@unittest.skipUnless(BROWSER, "no Chromium found")
class GraphNodes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = icon_fixture()
        cls.r = reading(run(cls.data, "", 390, 844, script=GRAPH))
        cls.marks = {m["id"]: m for m in cls.r["marks"]}

    def test_one_mark_per_node_account_icon_or_service_pill(self):
        for a in self.data["view"]["agents"]:
            m = self.marks[a["identity"]]
            with self.subTest(agent=a.get("name")):
                if a.get("icon"):
                    self.assertEqual(m["account"], a["icon"]["account"])
                    self.assertFalse(m["pill"], "the service pill gives way to the account icon")
                else:
                    self.assertIsNone(m["account"])
                    self.assertTrue(m["pill"])
        self.assertGreaterEqual(sum(m["shown"] for m in self.r["marks"]), 2)
        self.assertTrue(all(m["px"] == 14 for m in self.r["marks"] if m["shown"]))

    def test_icons_never_cover_a_label_or_another_dot_and_names_stay(self):
        self.assertEqual(self.r["collisions"], [])
        plain = reading(run(page_fixture(), "", 390, 844, script=GRAPH))
        names = lambda r: sorted((n["name"], n["labelShown"], n["role"]) for n in r["nodes"])
        self.assertEqual(names(self.r), names(plain))
        self.assertLessEqual(self.r["scroll"], self.r["width"])

    def test_tooltip_and_screen_reader_name_the_subscription(self):
        ui = nodes_by_name(self.r)["ui-builder"]
        self.assertIn("Codex (Codex Work), GPT-6.2 Sol", ui["aria"])
        self.assertIn("Codex (Codex Work), GPT-6.2 Sol (gpt-6.2-sol)", ui["title"])
        self.assertIn("Codex, GPT-6.2 Sol", nodes_by_name(self.r)["api-worker"]["aria"])   # no account: tool only

    def test_text_only_account_keeps_the_pill_and_an_image_shows_in_place(self):
        data = with_icon(with_icon(self.data, "api-worker", "copilot"), "docs-writer", "cursor")
        r = reading(run(data, "", 390, 844, script=GRAPH))
        marks = {m["id"]: m for m in r["marks"]}
        api, docs = agent(data, "api-worker")["identity"], agent(data, "docs-writer")["identity"]
        self.assertIsNone(marks[api]["account"])
        self.assertTrue(marks[api]["pill"])
        self.assertTrue(marks[docs]["img"])


@unittest.skipUnless(BROWSER, "no Chromium found")
class Pages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = icon_fixture()
        cls.ui, cls.api = agent(cls.data, "ui-builder")["identity"], agent(cls.data, "api-worker")["identity"]

    def page(self, route, data=None):
        return reading(run(data or self.data, route, 390, 844, script=PAGE))

    def test_inspector_runs_on_has_the_account_line_and_no_second_mark(self):
        r = self.page(f"/g/{self.ui}")
        self.assertEqual(r["runs"]["account"], {"account": "codex", "text": False, "img": False, "name": "Codex Work"})
        self.assertFalse(r["runs"]["chip"])
        self.assertTrue(r["runs"]["source"].endswith("Account: recorded at launch."))
        r = self.page(f"/g/{self.api}")
        self.assertIsNone(r["runs"]["account"])
        self.assertFalse(r["runs"]["chip"])   # v0.9.7.4: no CC / CX chip; the service is named in words
        self.assertNotIn("Account", r["runs"]["source"])

    def test_agent_page_gets_runs_on_with_the_account(self):
        r = self.page(f"/a/{self.ui}")
        self.assertEqual(r["runs"]["account"]["account"], "codex")
        self.assertLessEqual(r["scroll"], r["width"])
        r = self.page(f"/a/{self.api}")
        self.assertIsNotNone(r["runs"])
        self.assertIsNone(r["runs"]["account"])

    def test_text_only_account_shows_its_label_once_in_runs_on(self):
        r = self.page(f"/a/{self.api}", with_icon(self.data, "api-worker", "copilot"))
        self.assertEqual(r["runs"]["account"], {"account": "copilot", "text": True, "img": False, "name": None})

    def test_workspace_cards_put_the_icon_in_the_avatar_corner_instead_of_the_kind(self):
        r = self.page("/w/w1")
        self.assertEqual(r["cards"]["ui-builder"], {"corner": "codex", "kind": None})
        self.assertEqual(r["cards"]["web-lead"], {"corner": "claude:main", "kind": None})
        self.assertEqual(r["cards"]["api-worker"], {"corner": None, "kind": None})   # v0.9.7.4: model badge by the name
        self.assertLessEqual(r["scroll"], r["width"])

    def test_list_rows_and_the_question_card_name_both_sides(self):
        r = self.page("/list")
        self.assertEqual(r["rows"]["ui-builder"]["account"], "codex")
        self.assertTrue(all(v is None for k, v in r["rows"].items() if k not in ("ui-builder", "web-lead", "etl")))
        self.assertEqual(r["question"]["asker"]["account"], "codex")
        self.assertEqual(r["question"]["to"]["account"], "claude:main")
        self.assertTrue(r["question"]["toText"].startswith("To: "))
        self.assertIn("web-lead (Website / Lead)", r["question"]["toText"])
        self.assertLessEqual(r["scroll"], r["width"])

    def test_question_rows_route_with_both_icons_inline(self):
        r = self.page("/questions", asks_fixture())
        self.assertEqual(r["asksHead"]["account"], "codex")
        self.assertEqual(r["route"]["icons"], ["codex", "claude:main"])
        self.assertEqual(r["route"]["title"], "ui-builder asks. Your answer goes to web-lead in Website / Lead.")
        self.assertEqual(r["route"]["text"], r["route"]["title"])
        self.assertLessEqual(r["scroll"], r["width"])

    def test_a_side_without_a_recorded_account_shows_its_name_only(self):
        data = asks_fixture()
        agent(data, "ui-builder")["asks"]["items"][0]["answer_to"].pop("icon")
        r = self.page("/questions", data)
        self.assertEqual(r["route"]["icons"], ["codex"])
        self.assertEqual(r["route"]["text"], r["route"]["title"])


@unittest.skipUnless(BROWSER, "no Chromium found")
class NothingNewWithoutAccounts(unittest.TestCase):
    def test_no_recorded_account_means_no_icon_anywhere(self):
        data = page_fixture()
        ui = agent(data, "ui-builder")["identity"]
        for route in ("/list", "/w/w1", f"/a/{ui}", f"/g/{ui}"):
            with self.subTest(route=route):
                r = reading(run(data, route, 390, 844, script=PAGE))
                self.assertEqual(r["all"], 0)
        r = reading(run(data, "/w/w1", 390, 844, script=PAGE))
        self.assertEqual(r["cards"]["ui-builder"], {"corner": None, "kind": None})


class PanelPlainText(unittest.TestCase):
    """Review: a badge can be any 1-2 printable characters ('<', '&'). The panel draws badge and label as
    plain text only. scripts/qml-check.sh also renders the badge '<b' and checks it shows as typed."""

    def text_blocks(self, name):
        text = (PLUGIN / name).read_text()
        return text, [text[m.start():text.index("}", m.start())] for m in re.finditer(r"\bText \{", text)]

    def test_every_text_in_the_icon_renderer_and_agent_rows_is_plain(self):
        for name in ("AccountIcon.qml", "AgentRow.qml"):
            text, blocks = self.text_blocks(name)
            with self.subTest(file=name):
                self.assertTrue(blocks)
                for block in blocks:
                    self.assertIn("textFormat: Text.PlainText", block)
                self.assertNotRegex(text, r"RichText|StyledText|MarkdownText|AutoText")

    def test_the_account_label_is_never_drawn_as_text(self):
        text, blocks = self.text_blocks("AccountIcon.qml")
        self.assertIn("Accessible.name: icon && icon.label", text)
        self.assertFalse(any("label" in block for block in blocks))

    def test_qml_check_renders_a_markup_badge(self):
        check = (Path(__file__).resolve().parent.parent / "scripts" / "qml-check.sh").read_text()
        self.assertIn('badge: "<b"', check)
        self.assertIn("o.badgeFormat === Text.PlainText", check)


if __name__ == "__main__":
    unittest.main()
