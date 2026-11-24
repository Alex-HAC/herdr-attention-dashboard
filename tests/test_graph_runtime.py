"""v0.9.5: the node graph shows service, model and effort; recorded values only (390 px first)."""

import copy
import json
import unittest

from tests.test_graph_readability import busy_fixture
from tests.test_typing_survives_refresh import reading
from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER, run

NODES = """
await document.fonts.ready;
graph.autoFit = true; graph.userMoved = false; autoFit();
await new Promise(r => setTimeout(r, 150));
const rect = e => e.getBoundingClientRect();
const shown = e => !!e && getComputedStyle(e).display !== 'none' && e.getAttribute('visibility') !== 'hidden';
const overlap = (a,b) => a.left < b.right - .5 && a.right > b.left + .5 && a.top < b.bottom - .5 && a.bottom > b.top + .5;
const groups = [...document.querySelectorAll('svg.graph .node')].filter(g => getComputedStyle(g).display !== 'none');
const nodes = groups.map(g => {
  const svc = g.querySelector('.svc'), label = g.querySelector('.node-label'), role = g.querySelector('.role-label');
  const line2 = label.querySelector('.nl-line2');
  return {id: g.dataset.id, aria: g.getAttribute('aria-label'), title: g.querySelector('title').textContent,
    svc: svc.getAttribute('visibility') === 'visible' ? svc.textContent : null, svcShown: shown(svc),
    name: label.querySelector('.nl-name') ? label.querySelector('.nl-name').textContent : null, labelShown: shown(label),
    line2: !!line2 && shown(label) && shown(line2), meta: label.querySelector('.nl-meta') ? label.querySelector('.nl-meta').textContent : null,
    on: (label.querySelector('.nl-pips.on') || {textContent: ''}).textContent.trim().length,
    off: (label.querySelector('.nl-pips.off') || {textContent: ''}).textContent.trim().length,
    role: shown(role) && role.textContent ? role.textContent : null};
});
const pills = groups.map(g => [g, g.querySelector('.svc')]).filter(([, p]) => shown(p));
const labels = [...document.querySelectorAll('svg.graph .node-label, svg.graph .role-label, svg.graph .region-label')]
  .filter(e => e.textContent && shown(e));
const collisions = [];
for (const [g, p] of pills) {
  const r = rect(p.querySelector('rect'));
  for (const l of labels) if (overlap(r, rect(l))) collisions.push([g.dataset.id, l.textContent]);
  for (const d of document.querySelectorAll('svg.graph .node .body')) if (d.closest('.node') !== g && overlap(r, rect(d))) collisions.push([g.dataset.id, 'dot']);
  for (const [, q] of pills) if (q !== p && overlap(r, rect(q.querySelector('rect')))) collisions.push([g.dataset.id, 'pill']);
}
const pillPx = pills.length ? Math.min(...pills.map(([, p]) => parseFloat(p.querySelector('text').getAttribute('font-size')) * graph.transform.k)) : null;
const metaPx = Math.min(99, ...[...document.querySelectorAll('svg.graph .nl-line2')].filter(shown).map(e => rect(e).height));
document.body.dataset.reading = JSON.stringify({nodes, collisions, pillPx, metaPx,
  scroll: document.documentElement.scrollWidth, width: window.innerWidth});
"""


def nodes_by_name(r):
    return {n["name"] or n["id"]: n for n in r["nodes"]}


def without_runtime(data, keep_service=False):
    data = copy.deepcopy(data)
    for agent in data["view"]["agents"]:
        if keep_service:
            agent["runtime"] = dict(agent["runtime"], model=None, effort=None)
        else:
            agent.pop("runtime", None)
    return data


@unittest.skipUnless(BROWSER, "no Chromium found")
class NodesOnPhone(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = page_fixture()
        cls.view = {a.get("name") or a["identity"]: a for a in cls.data["view"]["agents"]}
        cls.r = reading(run(cls.data, "", 390, 844, script=NODES))

    def test_every_node_carries_its_service_pill_and_most_show_it(self):
        nodes = self.r["nodes"]
        self.assertEqual(len(nodes), 7)
        for n in nodes:
            agent = next(a for a in self.data["view"]["agents"] if a["identity"] == n["id"])
            # v0.9.8.6: the pill names the service ("Claude", "Codex"), never CC / CX and never the
            # model: the model is on line 2 for every node, so Claude and Codex nodes look the same.
            self.assertTrue(agent["runtime"]["service"]["name"].startswith(n["svc"].rstrip("…")), n["svc"])
            self.assertNotIn(n["svc"], ("CC", "CX"))
        self.assertGreaterEqual(sum(n["svcShown"] for n in nodes), 4)
        self.assertGreaterEqual(self.r["pillPx"], 8, "pill text stays readable on screen")

    def test_pills_never_cover_a_label_a_dot_or_another_pill(self):
        self.assertEqual(self.r["collisions"], [])
        self.assertLessEqual(self.r["scroll"], self.r["width"])

    def test_model_and_effort_line_shows_only_recorded_values(self):
        lines = [n for n in self.r["nodes"] if n["line2"]]
        self.assertGreaterEqual(len(lines), 3, "model and effort are visible at 390 px")
        self.assertGreaterEqual(self.r["metaPx"], 10.5)
        for n in lines:
            runtime = next(a for a in self.data["view"]["agents"] if a["identity"] == n["id"])["runtime"]
            with self.subTest(node=n["name"]):
                badge = next(a for a in self.data["view"]["agents"] if a["identity"] == n["id"])["runtime"]["badge"]
                # v0.9.8.6: a recorded model is always on line 2, with or without an account mark.
                if runtime["model"]:
                    self.assertTrue(n["meta"] and badge["text"].startswith(n["meta"].rstrip("…")), (badge, n["meta"]))
                else:
                    self.assertFalse(n["meta"], n)
                effort = runtime["effort"]
                self.assertEqual((n["on"], n["on"] + n["off"]), (effort["level"], 5) if effort else (0, 0))

    def test_tooltip_and_screen_reader_name_the_service_model_and_effort(self):
        ui = nodes_by_name(self.r)["ui-builder"]
        self.assertIn("Codex, GPT-6.2 Sol, effort high, 3 of 5", ui["aria"])
        self.assertIn("Codex, GPT-6.2 Sol (gpt-6.2-sol), effort high, 3 of 5", ui["title"])
        self.assertIn("context", ui["title"])
        docs = nodes_by_name(self.r)["docs-writer"]
        self.assertIn("Claude Code, Sonnet 5.5", docs["title"])
        self.assertNotIn("effort", docs["title"])           # not recorded: not shown

    def test_the_new_marks_never_displace_a_name_or_role(self):
        plain = reading(run(without_runtime(self.data), "", 390, 844, script=NODES))
        names = lambda r: sorted((n["name"], n["labelShown"], n["role"]) for n in r["nodes"])
        self.assertEqual(names(self.r), names(plain))
        self.assertTrue(all(n["labelShown"] for n in self.r["nodes"]))

    def test_default_worker_role_is_not_repeated_on_every_node(self):
        data = copy.deepcopy(self.data)
        for a in data["view"]["agents"]:
            if a.get("name") == "etl":
                a["roles"] = {"role": [{"value": "worker"}], "team": [], "task": []}
        r = reading(run(data, "", 390, 844, script=NODES))
        self.assertIsNone(nodes_by_name(r)["etl"]["role"])
        self.assertEqual(nodes_by_name(r)["web-lead"]["role"], "coordinator")


@unittest.skipUnless(BROWSER, "no Chromium found")
class NothingInvented(unittest.TestCase):
    def test_unknown_model_and_effort_draw_nothing(self):
        r = reading(run(without_runtime(page_fixture(), keep_service=True), "", 390, 844, script=NODES))
        self.assertFalse(any(n["line2"] for n in r["nodes"]))
        self.assertFalse(any("unknown" in (n["meta"] or "") for n in r["nodes"]))
        self.assertFalse(any("effort" in n["title"] for n in r["nodes"]))
        self.assertTrue(any(n["svcShown"] for n in r["nodes"]))

    def test_an_older_server_without_runtime_still_draws_the_graph(self):
        r = reading(run(without_runtime(page_fixture()), "", 390, 844, script=NODES))
        self.assertEqual(len(r["nodes"]), 7)
        self.assertFalse(any(n["svc"] or n["line2"] for n in r["nodes"]))

    def test_an_effort_word_off_the_scale_is_written_not_drawn_as_dots(self):
        data = page_fixture()
        for a in data["view"]["agents"]:
            if a["runtime"]["effort"]:
                a["runtime"]["effort"] = dict(a["runtime"]["effort"], id="ultra", label="ultra", level=None)
        r = reading(run(data, "", 390, 844, script=NODES))
        lines = [n for n in r["nodes"] if n["line2"]]
        self.assertTrue(lines)
        for n in lines:
            self.assertEqual(n["on"] + n["off"], 0)
        self.assertTrue(any(n["meta"].endswith("· ultra") for n in lines if n["name"] != "docs-writer"))


INSPECTOR = """
await new Promise(r => setTimeout(r, 400));
const box = document.querySelector('.sheet [data-reading-key="runs-on"]');
document.body.dataset.reading = JSON.stringify(box ? {
  text: box.querySelector('.runs-line').textContent, source: box.querySelector('.runs-source').textContent,
  pips: box.querySelectorAll('.pips i').length, on: box.querySelectorAll('.pips i.on').length,
  chip: (box.querySelector('.svc-chip') || {}).textContent || null, unknown: box.querySelectorAll('.runs-unknown').length,
  modelTitle: (box.querySelector('.runs-model') || {title: null}).title,
  scroll: document.documentElement.scrollWidth, width: window.innerWidth} : null);
"""


@unittest.skipUnless(BROWSER, "no Chromium found")
class InspectorRunsOn(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = page_fixture()
        cls.ids = {a.get("name"): a["identity"] for a in cls.data["view"]["agents"]}

    def open(self, name, data=None):
        return reading(run(data or self.data, f"/g/{self.ids[name]}", 390, 844, script="await new Promise(r => setTimeout(r, 400)); document.querySelector('[data-door=usage]').click();" + INSPECTOR))

    def test_trace_values_with_effort_dots_and_their_source(self):
        r = self.open("web-lead")
        self.assertIn("Runs on", r["text"])
        self.assertIn("Claude Code", r["text"])
        self.assertIn("Opus 5.5", r["text"])
        self.assertIn("extra high", r["text"])
        self.assertEqual((r["chip"], r["pips"], r["on"], r["unknown"]), (None, 5, 4, 0))   # v0.9.7.4: no CC chip
        self.assertEqual(r["modelTitle"], "claude-opus-5-5")
        self.assertTrue(r["source"].startswith("Model and effort: session trace, as of "), r["source"])
        self.assertLessEqual(r["scroll"], r["width"])

    def test_launch_record_values_say_they_were_set_at_launch(self):
        r = self.open("old-spike")
        self.assertIn("Haiku 5.5", r["text"])
        self.assertEqual(r["source"], "Model and effort: set at launch; the agent can change it later.")

    def test_a_missing_effort_is_a_quiet_unknown(self):
        r = self.open("docs-writer")
        self.assertIn("Sonnet 5.5", r["text"])
        self.assertIn("effort unknown", r["text"])
        self.assertEqual(r["pips"], 0)
        self.assertTrue(r["source"].startswith("Model: session trace, as of "), r["source"])
        self.assertNotIn("Effort:", r["source"])

    def test_nothing_recorded(self):
        r = self.open("web-lead", without_runtime(self.data, keep_service=True))
        self.assertIn("model unknown", r["text"])
        self.assertIn("effort unknown", r["text"])
        self.assertEqual(r["unknown"], 2)
        self.assertEqual(r["source"], "Model and effort are not recorded for this agent: no session trace and no launch record.")


@unittest.skipUnless(BROWSER, "no Chromium found")
class LegendAndBusyLayout(unittest.TestCase):
    def test_view_panel_explains_the_pill_and_the_dots(self):
        r = reading(run(page_fixture(), "", 390, 844, script="""
          graph.panel = 'view'; graphRefresh(true);
          await new Promise(r => setTimeout(r, 200));
          const legend = document.querySelector('[data-reading-key="legend-runtime"]');
          document.body.dataset.reading = JSON.stringify({text: legend ? legend.textContent : null,
            dots: legend ? legend.querySelectorAll('.pips i.on').length : 0,
            note: [...document.querySelectorAll('.sheet .note')].map(n => n.textContent).join(' | ')});
        """))
        self.assertIn("Sol 6.2", r["text"])
        self.assertNotIn("CC Claude Code", r["text"])
        self.assertIn("service by name", r["text"])
        self.assertIn("effort, low to max", r["text"])
        self.assertEqual(r["dots"], 3)
        self.assertIn("Without a recorded model the badge names the service", r["note"])

    def test_busy_phone_layout_pills_clear_everything(self):
        r = reading(run(busy_fixture(), "", 412, 915, script=NODES))
        self.assertEqual(r["collisions"], [])
        # v0.9.7.4: model names are wider than CC / CX, so fewer pills fit; the rest wait for zoom
        # (the model then stays on the node's second line).
        self.assertGreaterEqual(sum(n["svcShown"] for n in r["nodes"]), 4)
        self.assertFalse(any(n["role"] == "worker" for n in r["nodes"]))


if __name__ == "__main__":
    unittest.main()
