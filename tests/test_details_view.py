"""The agent details view: one component for the graph
sheet and the agent page. Status card, "Needs you" only when something is open, action bar, four
doors to Status, Activity, Usage and Links. FIXTURE-BASED, real Chromium at 390 and 1440 px."""

import copy
import json
import time
import unittest
import urllib.parse

from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER, run

WAIT = "const wait = (ms) => new Promise((ok) => setTimeout(ok, ms));\n"
REPORT = "\ndocument.body.dataset.reading = JSON.stringify(out);"


def reading(page):
    raw = page["dom"].split('data-reading="', 1)[1].split('"', 1)[0]
    return json.loads(raw.replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">"))


def q(identity):
    return urllib.parse.quote(identity, safe="")


def agent_named(data, name):
    return next(a for a in data["view"]["agents"] if a.get("name") == name)


@unittest.skipUnless(BROWSER, "no Chromium found")
class CalmAgent(unittest.TestCase):
    def test_no_needs_block_and_the_order_is_card_actions_doors(self):
        data = page_fixture()
        spike = agent_named(data, "old-spike")["identity"]
        script = WAIT + """await wait(300);
const root = document.querySelector('.details-main');
const kids = [...root.children].map((el) => ['status-card', 'needs-block', 'details-actions', 'doors'].find((c) => el.classList.contains(c)) || el.className);
const out = {kids, needs: !!document.querySelector('.needs-block'),
  cardHeight: Math.round(document.querySelector('.status-card').getBoundingClientRect().height),
  doors: [...document.querySelectorAll('.door .door-name')].map((d) => d.textContent.replace('›', '').trim()),
  explanation: !!document.querySelector('.watch-explanation'),
  toggle: document.querySelector('.status-card .watch-toggle')?.getAttribute('aria-pressed')};""" + REPORT
        for route in (f"/g/{q(spike)}", f"/a/{q(spike)}"):
            for width in (390, 1440):
                with self.subTest(route=route, width=width):
                    page = run(data, route, width, 900, script=script)
                    r = reading(page)
                    self.assertFalse(r["needs"], "nothing open: no Needs you block")
                    self.assertEqual(r["kids"], ["status-card", "details-actions", "doors"])
                    self.assertEqual(r["doors"], ["Status", "Activity", "Usage", "Links"])
                    self.assertFalse(r["explanation"], "the Watch closely sentence is a tooltip now")
                    self.assertEqual(r["toggle"], "false")
                    if width == 390:
                        self.assertLessEqual(r["cardHeight"], 240)
                    self.assertLessEqual(page["probe"]["scrollWidth"], page["probe"]["innerWidth"])


ANSWER_STUB = """
const realFetch = window.fetch;
window.fetch = async (path, init) => {
  const url = new URL(String(path), "http://x");
  if (url.pathname === UI.api.asks_answer && init?.method === "POST") {
    const body = JSON.parse(init.body);
    if (window.__failNext) { window.__failNext = false;
      return {ok: true, status: 200, json: async () => ({answered: {text: body.text, at: Date.now() / 1000, delivery: "failed"}, error: "agent pane closed"})}; }
    return {ok: true, status: 200, json: async () => ({answered: {text: body.text, at: Date.now() / 1000, delivery: "sent"}})};
  }
  return realFetch(path, init);
};
"""


@unittest.skipUnless(BROWSER, "no Chromium found")
class NeedsYou(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data = page_fixture(asks=True)
        lead = agent_named(data, "docs-lead")
        # Two open question items: the first is answered (fixture), answer the third too.
        lead["asks"]["items"][2]["answered"] = {"text": "Docs Hub", "at": time.time() - 60, "delivery": "sent"}
        ids = [i["id"] for i in lead["asks"]["items"]]
        cls.ids = ids
        script = WAIT + ANSWER_STUB + """await wait(300);
const out = {};
const row = (id) => document.querySelector(`.needs-block [data-reading-key="ask:${id}"]`);
const card = document.querySelector('.status-card');
out.next = card.nextElementSibling?.className || '';
out.head = document.querySelector('.needs-head').textContent;
out.rows = document.querySelectorAll('.needs-block .needs-row').length;
out.answeredHere = !!row(IDS[0]);
out.replies = document.querySelectorAll('.details-main .reply-open').length;
// Choose B and send: the row becomes a tick line, the count goes down.
row(IDS[1]).querySelectorAll('.ask-choice')[1].click(); await wait(60);
out.sendLabel = row(IDS[1]).querySelector('.ask-send').textContent;
row(IDS[1]).querySelector('.ask-send').click(); await wait(500);
out.tick = row(IDS[1])?.querySelector('.tick-line')?.textContent || null;
out.tickControls = !!row(IDS[1])?.querySelector('.ask-send');
out.headAfter = document.querySelector('.needs-head').textContent;
// A failed send keeps the row open with the reason and the typed draft.
window.__failNext = true;
const input = row(IDS[3]).querySelector('.ask-input');
input.focus(); input.value = 'only if it is cheap'; input.dispatchEvent(new Event('input'));
row(IDS[3]).querySelector('.ask-send').click(); await wait(500);
out.failed = row(IDS[3]).querySelector('.error')?.textContent || '';
out.draft = row(IDS[3]).querySelector('.ask-input')?.value;
out.headFailed = document.querySelector('.needs-head').textContent;
""".replace("IDS", json.dumps(ids)) + REPORT
        cls.page = run(data, f"/a/{q(lead['identity'])}", 390, 1400, script=script)
        cls.r = reading(cls.page)

    def test_block_sits_directly_under_the_status_card(self):
        self.assertIn("needs-block", self.r["next"])
        self.assertEqual(self.r["head"], "Needs you · 2")
        self.assertEqual(self.r["rows"], 2, "older answers are not in the block")
        self.assertFalse(self.r["answeredHere"])

    def test_sending_draws_a_tick_and_counts_down(self):
        self.assertEqual(self.r["sendLabel"], "Send: B) full runnable files")
        self.assertIn("You replied", self.r["tick"] or "")
        self.assertFalse(self.r["tickControls"])
        self.assertEqual(self.r["headAfter"], "Needs you · 1")

    def test_failed_send_keeps_the_row_and_the_draft(self):
        self.assertIn("agent pane closed", self.r["failed"])
        self.assertEqual(self.r["draft"], "only if it is cheap")
        self.assertEqual(self.r["headFailed"], "Needs you · 1")

    def test_one_reply_on_the_screen(self):
        self.assertLessEqual(self.r["replies"], 1)

    def test_no_horizontal_scroll(self):
        self.assertLessEqual(self.page["probe"]["scrollWidth"], self.page["probe"]["innerWidth"])
        self.assertEqual(self.page["probe"]["overflowing"], [])


@unittest.skipUnless(BROWSER, "no Chromium found")
class FinishedNotReviewed(unittest.TestCase):
    def setUp(self):
        self.data = page_fixture(asks=True)
        notes = agent_named(self.data, "release-notes")
        notes["read_action"] = {"available": True, "reason": None, "remote_reason": None}
        self.notes = notes

    def press(self, route):
        script = WAIT + """await wait(300);
const out = {checks: document.querySelectorAll('.needs-block .btn-check').length,
  line: document.querySelector('.finished-row .check-text')?.textContent || ''};
document.querySelector('.needs-block .btn-check').click();
await wait(150); out.done = document.querySelector('.btn-check')?.classList.contains('is-done') || false;
await wait(700); out.hash = location.hash;""" + REPORT
        page = run(self.data, route, 390, 900, script=script)
        return reading(page), page["probe"]

    def test_one_check_button_acks_and_closes_the_sheet(self):
        r, probe = self.press(f"/g/{q(self.notes['identity'])}")
        self.assertEqual(r["checks"], 1)
        self.assertTrue(r["line"].startswith("Finished: Release notes 2.3"))
        self.assertTrue(r["done"], "the check fills after the server confirmed")
        self.assertIn([f"/api/events/{self.notes['event_id']}/ack", "POST"], probe["calls"])
        self.assertEqual(r["hash"], "#/")

    def test_on_the_page_it_goes_back(self):
        r, probe = self.press(f"/a/{q(self.notes['identity'])}")
        self.assertIn([f"/api/events/{self.notes['event_id']}/ack", "POST"], probe["calls"])
        self.assertEqual(r["hash"], "#/w/w3")

    def test_a_failed_ack_shows_no_tick(self):
        self.data["ackFails"] = True
        r, probe = self.press(f"/a/{q(self.notes['identity'])}")
        self.assertFalse(r["done"])
        self.assertEqual(r["hash"], f"#/a/{q(self.notes['identity'])}")


@unittest.skipUnless(BROWSER, "no Chromium found")
class LinksScreen(unittest.TestCase):
    def test_each_agent_once_and_gone_folded(self):
        data = page_fixture()
        lead = data["ids"]["lead"]
        now = time.time()
        for i in range(120):   # a parked coordinator with many past workers, spawned and coordinated
            gone = f"claude:gone-worker-{i:03d}"
            base = {"provenance": ["observed-spawn"], "source": "seen spawning", "since": now - 3600, "hidden": False,
                    "from_hidden": False, "label": None, "cycle": False, "inferred": False, "dashed": False,
                    "from_name": f"gone-worker-{i:03d}", "to_name": "web-lead"}
            data["view"]["links"].append({**base, "from": gone, "to": lead, "kind": "reports_to"})
            data["view"]["links"].append({**base, "from": gone, "to": lead, "kind": "spawned_by"})
        script = WAIT + """await wait(300);
document.querySelector('[data-door=links]').click(); await wait(300);
const screen = document.querySelector('.details-screen');
const out = {height: Math.round(screen.getBoundingClientRect().height),
  door: null, gone: document.querySelector('[data-reading-key=workers-gone] > summary')?.textContent,
  goneOpen: document.querySelector('[data-reading-key=workers-gone]')?.open,
  live: [...screen.querySelectorAll('.worker-chips .chip')].map((c) => c.textContent),
  goneRows: [...screen.querySelectorAll('.gone-list li')].map((li) => li.textContent)};
""" + REPORT
        r = reading(run(data, f"/g/{q(lead)}", 420, 900, script=script))
        self.assertLess(r["height"], 1500)
        self.assertEqual(r["gone"], "120 gone")
        self.assertFalse(r["goneOpen"])
        self.assertEqual(sorted(r["live"]), ["etl", "ui-builder"])
        self.assertEqual(len(r["goneRows"]), 120, "each gone worker once")
        self.assertEqual(len(set(r["goneRows"])), 120)
        self.assertTrue(all(row.endswith("coordinates, spawned") for row in r["goneRows"]), r["goneRows"][:2])


# Every label of section 2.6 of the brief, and the one screen it lives on.
LABELS = {
    "main": ["Watch closely", "Terminal", "Reply", "Page", "Needs you · 1", "Mark read"],
    "status": ["Status report", "Steer", "Ask coordinator for status", "AI summary", "Summarize longer",
               "Ask summary agent", "Summary details", "Why this state", "Herdr status", "Latest visuals"],
    "activity": ["All", "Alerts", "Possibly stalled · Review the checkout plan", "Messages", "Show messages",
                 "Show terminal lines"],
    "usage": ["Runs on", "Context", "Tokens this session", "cache read", "Age", "Active", "How this is measured",
              "not money"],
    "links": ["role: coordinator", "team: checkout", "Coordinates", "How these links are known",
              "Declare role, team or reporting line", "Relationship history"],
}


@unittest.skipUnless(BROWSER, "no Chromium found")
class EveryLabelOnce(unittest.TestCase):
    def test_labels_live_on_exactly_one_screen(self):
        data = page_fixture()
        lead = data["ids"]["lead"]
        script = WAIT + """await wait(300);
const body = document.querySelector('.sheet-body');
const text = () => [body.textContent, ...[...body.querySelectorAll('[aria-label]')].map((el) => el.getAttribute('aria-label'))].join(' | ');
const out = {main: text()};
for (const screen of ['status', 'activity', 'usage', 'links']) {
  document.querySelector(`[data-door=${screen}]`).click(); await wait(250);
  body.querySelectorAll('details').forEach((d) => { d.open = true; });
  out[screen] = text();
  out[screen + 'Card'] = !!body.querySelector('.status-card');
  document.querySelector('.screen-back').click(); await wait(250);
}
out.back = !!body.querySelector('.status-card');""" + REPORT
        r = reading(run(data, f"/g/{q(lead)}", 1440, 1000, script=script))
        self.assertTrue(r["back"])
        for screen in ("status", "activity", "usage", "links"):
            self.assertFalse(r[screen + "Card"], f"the status card is not repeated on {screen}")
        for home, labels in LABELS.items():
            for label in labels:
                where = [screen for screen in LABELS if label in r[screen]]
                self.assertEqual(where, [home], f"{label!r} found on {where}")


@unittest.skipUnless(BROWSER, "no Chromium found")
class Routes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = page_fixture()
        cls.lead = q(cls.data["ids"]["lead"])
        cls.script = WAIT + """await wait(600);
const out = {screen: document.querySelector('.details-screen')?.dataset.screen || null,
  mainShown: !!document.querySelector('.details-main') && getComputedStyle(document.querySelector('.details-main')).display !== 'none',
  calls: window.__calls.map((c) => c[0].split('?')[0]), term: !!document.querySelector('.term-sheet:not([hidden])'),
  conversation: !!document.querySelector('#agent-conversation')};""" + REPORT

    def screen(self, suffix, width=390):
        return reading(run(self.data, f"/a/{self.lead}{suffix}", width, 900, script=self.script))

    def test_new_screen_routes(self):
        for name in ("status", "activity", "usage", "links"):
            with self.subTest(name=name):
                r = self.screen(f"/{name}")
                self.assertEqual(r["screen"], name)
                self.assertFalse(r["mainShown"], "phone: the screen replaces the main view")

    def test_wide_page_shows_main_and_screen(self):
        r = self.screen("/usage", 1440)
        self.assertEqual(r["screen"], "usage")
        self.assertTrue(r["mainShown"])

    def test_old_deep_links_land_on_their_screen(self):
        conversation = self.screen("/conversation")
        self.assertEqual(conversation["screen"], "activity")
        self.assertIn("/api/conversation", conversation["calls"])
        self.assertNotIn("/api/output", conversation["calls"])
        output = self.screen("/output")
        self.assertEqual(output["screen"], "activity")
        self.assertIn("/api/output", output["calls"])
        self.assertEqual(self.screen("/report")["screen"], "status")
        terminal = self.screen("/terminal")
        self.assertIsNone(terminal["screen"])
        self.assertIn("/api/terminal", terminal["calls"])

    def test_plain_link_is_the_main_screen(self):
        r = self.screen("")
        self.assertIsNone(r["screen"])
        self.assertNotIn("/api/conversation", r["calls"])
        self.assertNotIn("/api/output", r["calls"])


@unittest.skipUnless(BROWSER, "no Chromium found")
class NoSideScroll(unittest.TestCase):
    def test_details_and_workspace_fit_390(self):
        data = page_fixture(asks=True)
        long = agent_named(data, "web-lead")
        long["name"] = "scope-night-app-port-with-a-really-long-name"
        long["runtime"]["badge"]["label"] = "Opus 5.5 · extra high effort"
        routes = [f"/a/{q(long['identity'])}", f"/g/{q(long['identity'])}", "/w/w1", "/w/w3",
                  f"/a/{q(data['ids']['docs_lead'])}", f"/a/{q(data['ids']['ui'])}"]
        for route in routes:
            with self.subTest(route=route):
                probe = run(data, route, 390, 900)["probe"]
                self.assertLessEqual(probe["scrollWidth"], probe["innerWidth"])
                self.assertEqual(probe["overflowing"], [])


@unittest.skipUnless(BROWSER, "no Chromium found")
class SheetResets(unittest.TestCase):
    def test_another_node_opens_on_the_main_screen_and_escape_goes_back(self):
        data = page_fixture()
        ids = data["ids"]
        script = WAIT + f"""await wait(300);
document.querySelector('[data-door=usage]').click(); await wait(250);
const out = {{usage: document.querySelector('.details-screen')?.dataset.screen}};
document.dispatchEvent(new KeyboardEvent('keydown', {{key: 'Escape', bubbles: true}})); await wait(250);
out.afterEscape = document.querySelector('.details-screen')?.dataset.screen || null;
out.sheetOpen = !document.querySelector('.sheet').hidden;
document.querySelector('[data-door=links]').click(); await wait(250);
location.hash = '#/g/{q(ids["ui"])}'; await wait(400);
out.other = document.querySelector('.details-screen')?.dataset.screen || null;
out.card = !!document.querySelector('.sheet .status-card');""" + REPORT
        r = reading(run(data, f"/g/{q(ids['lead'])}", 1440, 900, script=script))
        self.assertEqual(r["usage"], "usage")
        self.assertIsNone(r["afterEscape"])
        self.assertTrue(r["sheetOpen"], "Escape on a screen goes back, it does not close the sheet")
        self.assertIsNone(r["other"])
        self.assertTrue(r["card"])


@unittest.skipUnless(BROWSER, "no Chromium found")
class RoundTwo(unittest.TestCase):
    """0.9.11.0: the 0.9.10.0 waiting and last-action data in the details view, and the polish items."""

    def test_waiting_line_under_the_pill_in_the_ring_colour(self):
        data = page_fixture()
        spike = agent_named(data, "old-spike")
        spike["waiting_for"] = {"kind": "shell", "label": "Running: pytest -q", "since": time.time() - 180}
        lead = data["ids"]["lead"]
        links = [l for l in data["view"]["links"] if l["to"] == lead and l["kind"] == "reports_to"]
        links[0]["last_action"] = {"kind": "task", "from": lead, "to": links[0]["from"], "at": time.time() - 240}
        links[0]["waiting"] = True
        script = WAIT + """await wait(300);
const line = document.querySelector('.status-card .sc-waiting');
const out = {text: line?.textContent || null, after: line?.previousElementSibling?.className || null,
  colour: line ? getComputedStyle(line).color : null, needs: !!document.querySelector('.needs-block'),
  shell: getComputedStyle(document.documentElement).getPropertyValue('--shell').trim(),
  big: tokens(1141.7e6), mid: tokens(12.4e6), huge: tokens(23.5e9)};
const probe = document.createElement('span'); probe.style.color = 'var(--shell)'; document.body.append(probe);
out.shellColour = getComputedStyle(probe).color;""" + REPORT
        r = reading(run(data, f"/a/{q(spike['identity'])}", 390, 900, script=script))
        self.assertEqual(r["text"], "Running: pytest -q, for 3m")
        self.assertEqual(r["after"], "sc-state", "right under the state pill")
        self.assertEqual(r["colour"], r["shellColour"], "the colour the graph uses for a shell wait")
        self.assertFalse(r["needs"], "waiting is not a Needs-you item")
        self.assertEqual((r["big"], r["mid"], r["huge"]), ("1.14B", "12.4M", "23.5B"))
        links_script = WAIT + """await wait(300);
document.querySelector('[data-door=links]').click(); await wait(250);
const chips = [...document.querySelectorAll('.worker-chips .chip')];
const out = {titles: chips.map((c) => c.title), awaited: chips.filter((c) => c.classList.contains('is-awaited')).length,
  how: document.querySelector('[data-reading-key=workers-how]')?.textContent || ''};""" + REPORT
        r = reading(run(data, f"/g/{q(lead)}", 1440, 900, script=links_script))
        self.assertTrue(any("sent task 4m ago" in t and "coordinator is waiting for this worker" in t for t in r["titles"]), r["titles"])
        self.assertEqual(r["awaited"], 1)
        self.assertIn("sent task 4m ago", r["how"])

    def test_check_rows_stack_on_a_phone_and_sit_beside_on_desktop(self):
        data = page_fixture()
        lead = data["ids"]["lead"]   # possibly stalled: one row with a check
        script = WAIT + """await wait(300);
const text = document.querySelector('.needs-block .check-text').getBoundingClientRect();
const check = document.querySelector('.needs-block .btn-check').getBoundingClientRect();
const out = {below: check.top >= text.bottom - 1, right: Math.round(check.right), textWidth: Math.round(text.width),
  row: Math.round(document.querySelector('.needs-block .check-line').getBoundingClientRect().width)};
// Report from inside the iframe too: only the iframe is really 390 px wide (the harness's probe slot).
if (window.parent !== window) { window.__probed = true; window.parent.document.body.dataset.probe = JSON.stringify(out); }""" + REPORT
        phone = run(data, f"/a/{q(lead)}", 390, 900, script=script)["probe"]
        self.assertTrue(phone["below"], "the check goes under the text")
        self.assertGreaterEqual(phone["textWidth"], phone["row"] - 2, "the text takes the full width")
        desk = reading(run(data, f"/a/{q(lead)}", 1440, 900, script=script))
        self.assertFalse(desk["below"], "side by side on a wide screen")

    def test_status_report_is_an_ai_button_and_the_team_status_a_pill(self):
        data = page_fixture()
        lead = agent_named(data, "web-lead")
        lead["status_report"] = {"id": 3, "status": "complete", "finished_at": time.time() - 600, "out_of_date": True,
                                 "summary": {"status": "Stuck", "members": [lead["identity"]], "done": ["Built page"],
                                             "in_progress": [], "left": ["Review"], "stuck": []}}
        script = WAIT + """await wait(300);
const button = document.querySelector('.status-report-button');
const pill = document.querySelector('.report-age .pill');
const out = {ai: button.classList.contains('btn-ai'), spark: !!button.querySelector('use[href="#i-spark"]'),
  pill: pill?.textContent, caution: pill?.classList.contains('s-caution'), line: document.querySelector('.report-age').textContent,
  stray: !!document.querySelector('.coordinator-report > .report-status')};""" + REPORT
        r = reading(run(data, f"/a/{q(lead['identity'])}/status", 390, 1200, script=script))
        self.assertTrue(r["ai"]); self.assertTrue(r["spark"])
        self.assertEqual(r["pill"], "Team status: Stuck")
        self.assertTrue(r["caution"])
        self.assertIn("Out of date", r["line"])
        self.assertFalse(r["stray"])


if __name__ == "__main__":
    unittest.main()
