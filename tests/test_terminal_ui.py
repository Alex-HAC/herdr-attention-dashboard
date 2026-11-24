"""v0.9.8 live terminal sheet: one tap from every agent surface, colours, follow, read-only, stops when closed."""

import json
import unittest
from pathlib import Path
from urllib.parse import quote

from tests.test_typing_survives_refresh import reading
from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER, run

E = "\x1b["
SCREEN = "\n".join(
    [f"{E}38;2;122;162;247m⏺{E}0m Reading {E}1mdocs/brief.md{E}0m"]
    + [f"  line {i} {E}2m· dim{E}0m" for i in range(60)]
    + [f"{E}38;2;68;68;68m╭──────────────╮{E}0m", f"{E}48;2;68;68;68m> {E}0m", "╰──────────────╯"])


def fixture():
    data = page_fixture()
    data["terminal"] = {"identity": "x", "screen": SCREEN, "hash": "h1", "read_at": 1794990000, "redactions": 0,
                        "history": "earlier line", "poll_ms": 1000}
    return data


OPEN = """
  await new Promise(r => setTimeout(r, 1400));
  const sheet = document.getElementById('terminal');
  const scroll = sheet && sheet.querySelector('.term-scroll');
  const spans = sheet ? [...sheet.querySelectorAll('.term-screen span')].map(s => s.getAttribute('style')) : [];
  const atBottom = scroll ? scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 30 : false;
  const rect = sheet ? sheet.getBoundingClientRect() : null;
  if (scroll) { scroll.scrollTop = 0; scroll.dispatchEvent(new Event('scroll')); }
  await new Promise(r => setTimeout(r, 100));
  const paused = sheet && sheet.querySelector('.term-live') && !sheet.querySelector('.term-live').hidden;
  const polls = window.__term || 0;
  const writes = window.__calls.filter(c => c[1] !== 'GET').map(c => c[0]);
  sheet && sheet.querySelector('.term-close').click();
  await new Promise(r => setTimeout(r, 2500));
  document.body.dataset.reading = JSON.stringify({
    open: !!sheet, history: sheet ? sheet.querySelector('.term-history').textContent : null,
    text: sheet ? sheet.querySelector('.term-screen').textContent.slice(0, 40) : null,
    truecolor: spans.some(s => s && s.includes('rgb(122,162,247)')), bold: spans.some(s => s && s.includes('font-weight:700')),
    box: sheet ? sheet.querySelector('.term-screen').textContent.includes('╭──') : false,
    atBottom, paused, polls, writes, fits: rect ? [Math.round(rect.width), Math.round(rect.height)] : null, win: [innerWidth, innerHeight],
    closed: !document.getElementById('terminal'), pollsAfter: (window.__term || 0) - polls, hash: location.hash});
"""

BUTTONS = """
  await new Promise(r => setTimeout(r, 900));
  const count = sel => document.querySelectorAll(sel).length;
  const before = {buttons: count('.term-open'), inCards: count('.agent-with-focus .term-open'),
    inQuestions: count('.question-card .term-open, .asks-head .term-open'), inAttention: count('.attention-row-actions .term-open')};
  const first = document.querySelector('.term-open');
  first && first.click();
  await new Promise(r => setTimeout(r, 600));
  document.body.dataset.reading = JSON.stringify({...before, opened: !!document.getElementById('terminal'), hash: location.hash});
"""


@unittest.skipUnless(BROWSER, "no Chromium found")
class TerminalSheet(unittest.TestCase):
    def test_phone_sheet_renders_colours_follows_and_stops_when_closed(self):
        data = fixture()
        ident = data["view"]["agents"][2]["identity"]
        shot = Path.home() / "agent-tools" / "terminal-sheet-sample-390.png"
        shot.parent.mkdir(exist_ok=True)
        r = reading(run(data, f"/a/{quote(ident, safe='')}/terminal", 390, 844, script=OPEN, theme="dark"))
        run(data, f"/a/{quote(ident, safe='')}/terminal", 390, 844, screenshot=shot, theme="dark")
        self.assertTrue(r["open"])
        self.assertTrue(r["truecolor"] and r["bold"] and r["box"])
        self.assertIn("earlier line", r["history"])
        self.assertIn("⏺ Reading docs/brief.md", r["text"])
        self.assertTrue(r["atBottom"], "follows the newest output")
        self.assertTrue(r["paused"], "scrolling up pauses follow and offers the jump back")
        self.assertGreaterEqual(r["polls"], 1)
        self.assertEqual(r["writes"], [], "the terminal view never posts anything")
        self.assertEqual(r["fits"], r["win"], "full screen on a phone")
        self.assertTrue(r["closed"])
        self.assertEqual(r["pollsAfter"], 0, "no polling once closed")
        self.assertNotIn("/terminal", r["hash"])

    def test_one_tap_from_list_cards_and_questions(self):
        data = fixture()
        r = reading(run(data, "/list", 390, 844, script=BUTTONS))
        self.assertGreaterEqual(r["inQuestions"], 1)
        self.assertTrue(r["opened"])
        ws = data["view"]["agents"][0]["workspace_id"]
        r = reading(run(data, f"/w/{ws}", 390, 844, script=BUTTONS))
        self.assertGreaterEqual(r["inCards"], 2)
        self.assertTrue(r["opened"])
        self.assertTrue(r["hash"].endswith("/terminal"))

    def test_agent_page_and_graph_inspector_have_the_button(self):
        data = fixture()
        ident = quote(data["view"]["agents"][2]["identity"], safe="")
        for route in (f"/a/{ident}", f"/g/{ident}"):
            with self.subTest(route=route):
                r = reading(run(data, route, 390, 844, script=BUTTONS))
                self.assertGreaterEqual(r["buttons"], 1)
                self.assertTrue(r["opened"])


if __name__ == "__main__":
    unittest.main()
