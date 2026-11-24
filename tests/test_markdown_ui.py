"""v0.9.6 safe markdown for agent text (app.js mdBlock / mdLine), at 390 px.

Bold, italic, code, lists, tables as plain rows, links as their text; agent
markup stays text, never HTML. Shown here on a notification's quoted evidence.
"""
import unittest

from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER, run

SCRIPT = """
window.__probed = true;
const out = {};
location.hash = hashLink("notification", "%(nid)s");
await new Promise((ok) => setTimeout(ok, 500)); render(true);
const md = document.querySelector("main [aria-current=true] .notif-evidence .md");
out.images = document.querySelectorAll("main img[src='x'], main svg[onload], main [onerror]").length;
out.title = document.title;
out.text = md.textContent;
out.strong = [...md.querySelectorAll("strong")].map((s) => s.textContent);
out.em = [...md.querySelectorAll("em")].map((e) => e.textContent);
out.code = [...md.querySelectorAll(":not(pre) > code")].map((c) => c.textContent);
out.pre = md.querySelector("pre")?.textContent;
out.rows = [...md.querySelectorAll(".md-row")].map((p) => p.textContent);
out.headRow = md.querySelector(".md-row-head")?.textContent;
out.lists = [...md.querySelectorAll("ul, ol")].map((l) => [l.tagName, l.children.length]);
out.heading = md.querySelector(".md-h")?.textContent;
out.scrollWidth = document.documentElement.scrollWidth;
const target = window.parent !== window ? window.parent.document.body : document.body;
target.dataset.probe = JSON.stringify(out);
"""

SAMPLE = "\n".join([
    "## Status", "**Done** and *checked*: `make test` passed.", "",
    "- one", "- two with **bold**", "", "1. first", "2. second", "",
    "| Check | Result |", "|---|:---:|", "| Full suite | 735 passed |", "",
    "See [the report](https://example.invalid/r).", "",
    "```", "pytest -q **not bold**", "```", "",
    # (No closing script tag: it would end the harness's own fixture script.)
    '<img src=x onerror="document.title=1"> **<svg onload="document.title=2">x</svg>** [y](javascript:alert(1)) and an **unclosed marker',
])


@unittest.skipUnless(BROWSER, "no Chromium found")
class AgentTextMarkdown(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data = page_fixture()
        note = data["notifications"][0]
        note["evidence"] = SAMPLE
        cls.probe = run(data, "/notifications", 390, 900, script=SCRIPT % {"nid": note["id"]})["probe"]

    def test_bold_italic_code_lists_and_heading(self):
        self.assertEqual(self.probe["heading"], "Status")
        self.assertIn("Done", self.probe["strong"])
        self.assertIn("bold", self.probe["strong"])
        self.assertEqual(self.probe["em"], ["checked"])
        self.assertEqual(self.probe["code"], ["make test"])
        self.assertEqual(self.probe["lists"], [["UL", 2], ["OL", 2]])

    def test_tables_are_plain_rows_and_links_are_text(self):
        self.assertEqual(self.probe["rows"], ["Check · Result", "Full suite · 735 passed"])
        self.assertEqual(self.probe["headRow"], "Check · Result")
        self.assertIn("See the report.", self.probe["text"])
        self.assertNotIn("example.invalid", self.probe["text"])
        self.assertNotIn("javascript:", self.probe["text"])

    def test_no_literal_markers_but_code_stays_exact(self):
        self.assertEqual(self.probe["pre"], "pytest -q **not bold**")
        outside_code = self.probe["text"].replace(self.probe["pre"], "")
        self.assertNotIn("**", outside_code)
        self.assertNotIn("##", outside_code)
        self.assertIn("an unclosed marker", outside_code)

    def test_agent_markup_is_text_never_html(self):
        self.assertEqual(self.probe["images"], 0)
        self.assertNotIn(self.probe["title"], ("1", "2"))
        self.assertIn('<img src=x onerror="document.title=1">', self.probe["text"])
        self.assertLessEqual(self.probe["scrollWidth"], 390)


if __name__ == "__main__":
    unittest.main()
