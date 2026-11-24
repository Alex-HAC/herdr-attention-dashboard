"""v0.9.8.6: a workspace bubble shrinks back after a big fold of subagents closes; an open fold
draws at most 40 of them; the graph "To look at" rows are compact with the read hint once."""
import copy
import json
import unittest

from tests.test_typing_survives_refresh import reading
from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER, run

PARENT = "claude:d972f190-dbcd-5fa2-bdaf-9cc06883ed8f"   # web-lead, workspace w1


def fold_fixture(n=150):
    data = page_fixture()
    agents = data["view"]["agents"]
    parent = next(a for a in agents if a["identity"] == PARENT)
    kids = []
    for i in range(n):
        kid = copy.deepcopy(parent)
        kid.update({"identity": f"{PARENT}/subagent/a{i:04d}", "name": f"subagent a{i:04d}", "pane_id": None,
                    "terminal_id": None, "event_id": None, "in_process": True, "in_process_parent": PARENT,
                    "folded": True, "fold_parent": PARENT, "status": "done", "state": "done", "kind": "done",
                    "tier": None, "question": None, "asks": None, "icon": None, "last_activity_at": 1000 + i,
                    "finished_subagents": [], "finished_subagents_label": None})
        kids.append(kid)
    parent["finished_subagents"] = [k["identity"] for k in kids]
    parent["finished_subagents_label"] = f"Finished subagents · {n}"
    agents.extend(kids)
    return data


SCRIPT = """
const wait = (ms) => new Promise(r => setTimeout(r, ms));
await wait(400);
const ws = () => graph.regions['w1'];
const out = {};
out.before = {R: Math.round(ws().R), nodes: Object.keys(graph.nodes).length, k: graph.transform.k};
graph.panel = 'view'; graphRefresh(true); await wait(200);
const fold = document.querySelector('[data-reading-key="subagents:PARENT"]');
fold.open = true; fold.dispatchEvent(new Event('toggle')); await wait(400);
out.open = {R: Math.round(ws().R), nodes: Object.keys(graph.nodes).length, k: graph.transform.k,
  drawn: (document.querySelector('[data-reading-key="fold-drawn"]') || {}).textContent || null};
const f2 = document.querySelector('[data-reading-key="subagents:PARENT"]');
f2.open = false; f2.dispatchEvent(new Event('toggle')); await wait(400);
out.closed = {R: Math.round(ws().R), nodes: Object.keys(graph.nodes).length, k: graph.transform.k};
document.body.dataset.reading = JSON.stringify(out);
""".replace("PARENT", PARENT)


@unittest.skipUnless(BROWSER, "no Chromium found")
class FoldGrowsAndShrinks(unittest.TestCase):
    def test_bubble_shrinks_back_and_the_camera_refits(self):
        r = reading(run(fold_fixture(), "", 1280, 900, script=SCRIPT))
        b, o, c = r["before"], r["open"], r["closed"]
        self.assertEqual(o["nodes"], b["nodes"] + 40, "an open fold draws at most 40 subagents")
        self.assertIn("40 newest of 150 drawn", o["drawn"] or "")
        self.assertGreater(o["R"], b["R"] * 1.5)
        self.assertEqual(c["nodes"], b["nodes"])
        self.assertLessEqual(c["R"], b["R"] * 1.15, (b, c))       # back to (about) its old size
        self.assertGreater(o["k"], 0)
        self.assertLess(o["k"], b["k"])                            # zoomed out to fit the big group
        self.assertGreater(c["k"], o["k"])                         # and back in after it closed


ATTENTION = """
await new Promise(r => setTimeout(r, 300));
graph.panel = 'attention'; graphRefresh(true);
await new Promise(r => setTimeout(r, 300));
const rows = [...document.querySelectorAll('.att-panel-list > li')];
document.body.dataset.reading = JSON.stringify({
  rows: rows.map(li => ({h: Math.round(li.getBoundingClientRect().height), name: li.querySelector('.att-who b')?.textContent,
    btnW: Math.round(li.querySelector('.att-item').getBoundingClientRect().width), liW: Math.round(li.getBoundingClientRect().width),
    where: li.querySelector('.att-where')?.textContent, hint: !!li.querySelector('.read-state-hint')})),
  sectionHints: [...document.querySelectorAll('.attention-group > .section-hint')].map(p => p.textContent),
  wide: document.documentElement.scrollWidth});
"""


@unittest.skipUnless(BROWSER, "no Chromium found")
class AttentionPanelRows(unittest.TestCase):
    def test_rows_are_compact_full_width_with_the_hint_once(self):
        data = page_fixture()
        hint = "Clears when the pane is viewed in Herdr or the agent moves on."
        for a in data["view"]["agents"]:
            a["read_action"] = {"available": False, "reason": hint}
        for width in (390, 481):
            with self.subTest(width=width):
                r = reading(run(data, "", width, 844, script=ATTENTION))
                self.assertTrue(r["rows"], r)
                for row in r["rows"]:
                    self.assertTrue(row["name"], row)
                    self.assertTrue(row["where"], row)
                    self.assertLess(row["h"], 120, row)                       # was ~250 px
                    self.assertGreaterEqual(row["btnW"], row["liW"] - 40, row)   # the row is not squeezed
                    self.assertFalse(row["hint"], row)                        # the hint is on the section
                self.assertTrue(r["sectionHints"])
                self.assertTrue(all(h == hint for h in r["sectionHints"]))


if __name__ == "__main__":
    unittest.main()
