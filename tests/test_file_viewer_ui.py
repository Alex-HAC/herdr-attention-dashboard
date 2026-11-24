"""v0.9.8.8 file viewer on screen: paths in a question become chips; a tap opens the read-only
viewer (safe markdown, path, when it changed, copy, diff vs original); missing files say so."""
import json
import time
import unittest

from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER, run

ORIGINAL = "team-registry/docs/working-styles.md"
DRAFT = "work/next-20261119/gates-review/docs/team-registry/docs/working-styles-agents-section.md"
ASK = f"Compare {ORIGINAL} with the draft {DRAFT}, and see missing/notes.md?"
TEXT = ("# Working styles\n\nHow agents **work** with Alex.\n\n<img src=x onerror=\"window.__pwned=1\">"
        "<svg onload=\"window.__pwned=2\"></svg>\n\n- one\n- two\n\n[a link](javascript:alert(1))\n")
NOW = time.time()


def fixture():
    f = page_fixture()

    def swap(node):
        if isinstance(node, dict):
            return {k: swap(v) for k, v in node.items()}
        if isinstance(node, list):
            return [swap(v) for v in node]
        return ASK if node == "Run npm test in ./cart?" else node
    f["view"] = swap(f["view"])
    doc = lambda p, text: {"requested": p, "path": f"/home/user/Projects/{p}", "shown": f"~/Projects/{p}",
                           "name": p.split("/")[-1], "mtime": NOW - 3600, "size": len(text), "kind": "markdown", "text": text}
    f["files"] = {
        ORIGINAL: doc(ORIGINAL, TEXT),
        DRAFT: doc(DRAFT, TEXT + "\n## Agents\n\nAsk before merging.\n"),
        f"{DRAFT}|{ORIGINAL}": dict(doc(DRAFT, ""), against=doc(ORIGINAL, ""), diff={
            "rows": [{"op": "skip", "n": 9}, {"op": "same", "text": "- two"}, {"op": "add", "text": "## Agents"},
                     {"op": "add", "text": "Ask before merging."}], "added": 2, "removed": 0, "same": False}),
    }
    return f


SCRIPT = """
  const wait=ms=>new Promise(r=>setTimeout(r,ms));
  const facts={};
  try {
    await wait(150);
    const chips=[...document.querySelectorAll('.question-card [data-file]')];
    facts.chips=chips.map(c=>c.dataset.file);
    facts.agent=chips[0]?.closest('[data-agent-identity]')?.dataset.agentIdentity || null;
    chips[0].click(); await wait(150);
    const sheet=()=>document.getElementById('file-sheet');
    facts.open={title:sheet().querySelector('#file-title').textContent, path:sheet().querySelector('.file-path').value,
      meta:sheet().querySelector('.file-meta').textContent, heading:sheet().querySelector('.file-md .md-h')?.textContent,
      bold:sheet().querySelector('.file-md strong:not(.md-h strong)')?.textContent || sheet().querySelectorAll('.file-md strong').length,
      imgs:sheet().querySelectorAll('img,svg,script,a[href^="javascript"]').length, pwned:window.__pwned || 0,
      raw:sheet().querySelector('.file-body').textContent.includes('<img src=x'),
      buttons:[...sheet().querySelectorAll('.file-tools .btn')].map(b=>b.textContent),
      focus:document.activeElement?.className, fullWidth:sheet().querySelector('.file-panel').getBoundingClientRect().width,
      asked:window.__files.slice()};
    [...sheet().querySelectorAll('.file-tools .btn')].find(b=>b.textContent.startsWith('Diff')).click(); await wait(150);
    facts.diff={asked:window.__files.at(-1), add:[...sheet().querySelectorAll('.fd-add')].map(r=>r.textContent),
      skip:sheet().querySelector('.fd-skip')?.textContent, sum:sheet().querySelector('.file-diff-sum')?.textContent};
    sheet().querySelector('.file-close').click(); await wait(80);
    facts.closed={sheet:!!sheet(), focus:document.activeElement?.dataset?.file || null};
    chips[2].click(); await wait(150);
    facts.missing={text:sheet().querySelector('.file-error')?.textContent, role:sheet().querySelector('.file-error')?.getAttribute('role')};
    document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true})); await wait(60);
    facts.escaped=!sheet();
  } catch (e) { facts.error=String(e)+' '+e.stack; }
  window.__calls.push(['__facts', JSON.stringify(facts)]);
"""


def facts(page):
    for path, value in page["probe"].get("calls", []):
        if path == "__facts":
            return json.loads(value)
    raise AssertionError("no facts")


@unittest.skipUnless(BROWSER, "no Chromium")
class FileViewer(unittest.TestCase):
    def test_a_path_in_a_question_opens_the_viewer_on_a_phone(self):
        f = facts(run(fixture(), "/questions", 390, 844, script=SCRIPT))
        self.assertNotIn("error", f, f.get("error"))
        self.assertEqual(f["chips"], [ORIGINAL, DRAFT, "missing/notes.md"])
        self.assertTrue(f["agent"], "the chip knows its agent, for relative paths")
        o = f["open"]
        self.assertEqual((o["title"], o["path"]), ("working-styles.md", "/home/user/Projects/" + ORIGINAL))
        self.assertTrue(o["meta"].startswith("Changed "), o["meta"])
        self.assertEqual(o["heading"], "Working styles")
        self.assertEqual((o["imgs"], o["pwned"]), (0, 0), "agent text never becomes HTML")
        self.assertTrue(o["raw"], "the tags show as text")
        self.assertEqual(o["buttons"], ["Copy path", "Diff vs draft", "Plain text"])
        self.assertIn("file-close", o["focus"])
        self.assertEqual(o["fullWidth"], 390, "full screen on a phone")
        self.assertIn("identity=", o["asked"][0])
        d = f["diff"]
        self.assertIn(f"against={ORIGINAL.replace('/', '%2F')}", d["asked"])
        self.assertEqual(d["add"], ["+## Agents", "+Ask before merging."])
        self.assertEqual(d["skip"], "9 unchanged lines")
        self.assertIn("+2 −0", d["sum"])
        self.assertEqual(f["closed"], {"sheet": False, "focus": ORIGINAL})
        self.assertEqual(f["missing"], {"text": "File not found", "role": "alert"})
        self.assertTrue(f["escaped"])

    def test_desktop_panel(self):
        script = """const wait=ms=>new Promise(r=>setTimeout(r,ms)); await wait(150);
          document.querySelector('.question-card [data-file]').click(); await wait(150);
          const r=document.querySelector('#file-sheet .file-panel').getBoundingClientRect();
          window.__calls.push(['__facts', JSON.stringify({w:r.width, left:r.left})]);"""
        f = facts(run(fixture(), "/questions", 1280, 800, script=script))
        self.assertEqual(f["w"], 920)
        self.assertEqual(f["left"], 180)


if __name__ == "__main__":
    unittest.main()
