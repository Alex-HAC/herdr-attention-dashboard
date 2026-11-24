// node tests/js/model.test.js — tests the panel's pure helpers (Model.js).
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../../plugin/alexvdb.herdr-attention/Model.js"), "utf8")
  .replace(/^\.pragma library\s*$/m, "");
const Model = {};
vm.runInNewContext(source, Model);
// Values from the sandbox have their own Array prototype; compare plain copies.
const plain = (x) => JSON.parse(JSON.stringify(x));

let passed = 0;
function test(name, fn) {
  fn();
  passed++;
}

test("duration", () => {
  assert.equal(Model.duration(5), "5s");
  assert.equal(Model.duration(125), "2m");
  assert.equal(Model.duration(3 * 3600 + 7 * 60), "3h07");
  assert.equal(Model.duration(3 * 86400), "3d");
});

test("since marks unknown start", () => {
  assert.equal(Model.since({ status_since: 100, since_known: true }, 400), "5m");
  assert.equal(Model.since({ status_since: 100, since_known: false }, 400), "≥5m");
  assert.equal(Model.since({}, 400), "");
});

test("groups: unread alerts first, read alerts drop to other", () => {
  const view = { agents: [
    { identity: "a", kind: "approval", acked: false },
    { identity: "b", kind: "working" },
    { identity: "c", kind: "completion", acked: true },
    { identity: "d", kind: "idle" },
  ] };
  const g = Model.groups(view);
  assert.deepEqual(plain(g.attention.map(a => a.identity)), ["a"]);
  assert.deepEqual(plain(g.working.map(a => a.identity)), ["b"]);
  assert.deepEqual(plain(g.other.map(a => a.identity)), ["c", "d"]);
  assert.deepEqual(plain(Model.groups(null)), { attention: [], working: [], other: [] });
});

test("hero text covers offline and empty states", () => {
  assert.match(Model.heroMeta(null, false, "watch stopped"), /Collector offline · watch stopped/);
  const offline = { collector: { herdr: { connected: false, error: "no socket" } } };
  assert.match(Model.heroMeta(offline, true, ""), /Herdr not reachable/);
  const none = { collector: { herdr: { connected: true } }, workspaces: [{ watched: false }], counts: {} };
  assert.match(Model.heroMeta(none, true, ""), /No workspaces watched/);
  const busy = { collector: { herdr: { connected: true } }, workspaces: [{ watched: true }],
                 counts: { needs_you: 2, working: 3 } };
  assert.equal(Model.heroMeta(busy, true, ""), "2 waiting on you · 3 working");
});

test("phone text", () => {
  assert.match(Model.phoneText({ notify: { ready: false, detail: "no token" }, presence: {} }), /off · no token/);
  assert.match(Model.phoneText({ notify: { ready: true, mode: "auto", away: false, held: 2 },
                                 presence: { state: "present", delay_seconds: 300 } }), /held while you are active \(after 5 min idle\) · 2 held/);
});

test("evidence lines are plain strings", () => {
  const lines = Model.evidenceLines({ confidence: "low", seen_late: true,
    evidence: { herdr_status: "working", working_minutes: 20, working_since_known: false, threshold_minutes: 15,
                output_unchanged_minutes: 18, output_checked_every_seconds: 60, note: "guess" } });
  assert.ok(lines.includes("Working for at least 20 min (limit 15)"));
  assert.ok(lines.includes("Same screen in several checks over 18 min (every 60 s)"));
  assert.ok(lines.includes("Confidence: low"));
  assert.ok(lines.some(l => l.startsWith("Seen late")));
});

test("toggled and clampStep", () => {
  assert.deepEqual(plain(Model.toggled(["w2"], "w1", true)), ["w1", "w2"]);
  assert.deepEqual(plain(Model.toggled(["w1", "w2"], "w1", false)), ["w2"]);
  assert.deepEqual(plain(Model.toggled(["w1"], "w1", true)), ["w1"]);
  assert.equal(Model.clampStep(5, -5, 5, 240), 5);
  assert.equal(Model.clampStep(15, 5, 5, 240), 20);
});

test("workspace helpers", () => {
  const counts = { needs_you: 1, working: 2, done: 0, waiting: 1, parked: 0, unknown: 0 };
  assert.equal(Model.countsText(counts), "1 need you · 2 working · 1 waiting");
  assert.equal(Model.countsText({}), "no agents running");
  const segs = plain(Model.barSegments(counts));
  assert.deepEqual(segs.map(s => s.state), ["needs_you", "working", "waiting"]);
  assert.equal(segs.reduce((n, s) => n + s.fraction, 0), 1);
  const view = { agents: [
    { identity: "a", workspace_id: "w1", state: "working", kind: "working" },
    { identity: "b", workspace_id: "w1", state: "needs_you", kind: "approval", acked: false },
    { identity: "c", workspace_id: "w2", state: "done", kind: "completion", acked: true },
  ] };
  assert.deepEqual(plain(Model.workspaceSections(view, "w1")).map(s => s.state), ["needs_you", "working"]);
  assert.deepEqual(plain(Model.attentionItems(view)).map(a => a.identity), ["b"]);
});

test("facts say unknown instead of guessing", () => {
  const lines = Model.factLines({ status_since: 100, since_known: false, present: true, last_seen: 100, first_seen: 100 }, 700, true);
  assert.ok(lines.includes("In this state: ≥10m (started before the collector saw it)"));
  assert.ok(lines.includes("Last activity: no activity seen yet"));
  assert.ok(lines.includes("Last seen: here now"));
  assert.ok(lines.includes("Tab: unknown"));
  assert.ok(lines.includes("Role: not declared · Team: not declared · Task: not declared"));
});

test("timeline lines mark late findings", () => {
  const lines = Model.timelineLines({ activity: [
    { at: 640, kind: "title", status: "working", title: "New", live: true },
    { at: 100, kind: "seen", status: "idle", title: "Old", live: false }] }, 700, 5);
  assert.equal(lines[0], "1m ago · New task title: New");
  assert.equal(lines[1], "≈ 10m ago · Seen idle: Old");
});

test("tabs text and overview line", () => {
  const view = { workspaces: [{}], agents: [{ identity: "a", name: "lead" }, { identity: "b", agent: "codex", pane_id: "w1:p2" }],
                 counts: { needs_you: 1, state_working: 1 } };
  const ws = { tabs: [{ label: "Lead", agents: ["a"] }, { label: "Workers", agents: ["b"] }] };
  assert.equal(Model.tabsText(ws, view), "Lead: lead\nWorkers: codex w1:p2");
  assert.equal(Model.overviewLine(view), "1 workspace · 2 agents · 1 waiting on you · 1 working");
});

test("metric and link lines say exact, estimate or unavailable", () => {
  const agent = { identity: "a", workspace_id: "w1", metrics: {
    context: { available: true, value: 150400, window: 400000, percent: 37.6, exact: false, kind: "estimate (as of the latest request)" },
    tokens: { available: true, value: 535000, exact: true, parts: { input: 50000, cache_read: 470000, cache_write: 0, output: 15000 } },
    age: { available: true, value: 3 * 3600 + 300, exact: true, source: "session start in the trace" },
    active: { available: true, value: 600, exact: false } } };
  const lines = plain(Model.metricLines(agent));
  assert.equal(lines[0], "Context: 150k tokens of 400k (37.6%) · estimate (as of the latest request)");
  assert.equal(lines[1], "Tokens this session: 535k (in 50k, cache read 470k, cache write 0, out 15k) · exact");
  assert.equal(lines[2], "Age: 3 h 05 min · session start in the trace");
  assert.equal(lines[3], "Active (observed working): ≥ 10 min");
  const none = plain(Model.metricLines({ metrics: { context: { available: false, reason: "no trace" }, tokens: { available: false } } }));
  assert.equal(none[0], "Context: unavailable (no trace)");
  const view = { agents: [agent, { identity: "b", name: "kid", workspace_id: "w2", workspace_label: "Other" }],
                 links: [{ from: "b", to: "a", kind: "spawned_by", hidden: false }, { from: "a", to: "hidden-1", kind: "forked_from", hidden: true }] };
  assert.deepEqual(plain(Model.linkLines(agent, view)),
    ["Link: spawned kid (in Other)", "Link: forked from a hidden or unknown agent"]);
  const declared = { agents: view.agents, links: [{ from: "a", to: "b", kind: "reports_to", provenance: ["manual"], hidden: false, cycle: true }] };
  assert.deepEqual(plain(Model.linkLines(agent, declared)), ["Link: reports to kid (in Other) (manual) · part of a cycle"]);
});

test("summary lines only for the current item and labelled as AI", () => {
  const done = { kind: "approval", acked: false, summary: { status: "complete", current: true, headline: "Wants to run tests",
    model: "sonnet", resolved_model: "claude-sonnet-5-5", effort: "medium",
    needs_you: { kind: "approval", ask: "Run the test suite?", options: ["Yes", "No"] } } };
  assert.equal(Model.summaryHeadline(done), "AI: Wants to run tests");
  assert.deepEqual(plain(Model.summaryLines(done)), ["AI summary (claude-sonnet-5-5, effort medium): Wants to run tests",
    "Asks (approval): Run the test suite?", "  option: \u201cYes\u201d", "  option: \u201cNo\u201d"]);
  const old = { summary: { ...done.summary, current: false } };
  assert.equal(Model.summaryHeadline(old), "");
  assert.deepEqual(plain(Model.summaryLines(old)), []);
  assert.deepEqual(plain(Model.summaryLines({ summary: { status: "failed", current: true, error: "timed out" } })), ["AI summary failed: timed out"]);
});

test("Herdr status colours", () => {
  const view = { status_colors: { colors: { working: "#e0af68", blocked: "#ff7a93", done: "#449dab", idle: "#9ece6a", unknown: "#a9b1d6" } } };
  assert.equal(Model.herdrColor(view, { present: true, status: "working" }), "#e0af68");
  assert.equal(Model.herdrColor(view, { present: true, status: "done" }), "#449dab");
  assert.equal(Model.herdrColor(view, { present: false, kind: "failure" }), "#ff7a93");
  assert.equal(Model.stateHerdrColor(view, "parked"), "#9ece6a");
  assert.equal(Model.herdrColor({}, { present: true, status: "working" }), "");
});

test("layout hints stay separate from attention", () => {
  const workspace = { layout_hints: [{ tab_id: "w4:t1", message: "7 panes (limit 4)" }], attention: 0 };
  assert.match(Model.layoutText(workspace), /w4:t1: 7 panes/);
  assert.equal(workspace.attention, 0);
  assert.equal(Model.layoutText({}), "");
});

test("empty workspaces use live counts even when historical agents remain", () => {
  const view = {workspaces:[{id:"live",agent_count:2,live_agent_count:1},{id:"old",agent_count:1,live_agent_count:0},{id:"shell",agent_count:0}]};
  assert.deepEqual(plain(Model.activeWorkspaces(view).map(w=>w.id)),["live"]);
  assert.deepEqual(plain(Model.emptyWorkspaces(view).map(w=>w.id)),["old","shell"]);
});

test("inferred desktop relationships carry dashed style and evidence; declared remain solid", () => {
  const a = {identity: "worker", name: "worker"}, b = {identity: "parent", name: "parent"};
  const view = {agents: [a,b], links: [{from: "worker", to: "parent", kind: "reports_to", provenance: ["inferred"],
    inferred: true, dashed: true, confidence: .97, source: "inferred: brief.md:3 names coordinator"}]};
  let rows = Model.connectionRows(a, view);
  assert.equal(rows[0].dashed, true);
  assert.match(rows[0].text, /inferred: brief.md:3/);
  assert.match(rows[0].text, /97%/);
  view.links[0] = {...view.links[0], provenance: ["manual"], inferred: false, dashed: false};
  rows = Model.connectionRows(a, view);
  assert.equal(rows[0].dashed, false);
  assert.equal(rows[0].inferred, false);
  assert.match(rows[0].text, /manual/);
});

test("notification deep links from the panel", () => {
  const view = { desktop: { url: "http://127.0.0.1:9000", notifications_hash: "#/n", notification_hash: "#/n/{id}" }, notifications: { unread: 3 } };
  assert.equal(Model.notificationLink(view), "http://127.0.0.1:9000/#/n");
  assert.equal(Model.notificationLink(view, 42), "http://127.0.0.1:9000/#/n/42");
  assert.equal(Model.notificationLink({}), "");
  assert.equal(Model.notificationLink(null), "");
  assert.equal(Model.notificationsText(view), "Notifications · 3 unread");
  assert.equal(Model.notificationsText({}), "Notifications");
});

test("panel counts and lists skip folded and gone agents, like the web", () => {
  const view = { workspaces: [{}], counts: {}, agents: [
    { identity: "a", workspace_id: "w1", state: "working", present: true },
    { identity: "s1", workspace_id: "w1", state: "done", present: true, in_process: true, folded: true },
    { identity: "s2", workspace_id: "w1", state: "done", present: true, in_process: true, folded: true },
    { identity: "g", workspace_id: "w1", state: "waiting", present: false },
  ] };
  assert.equal(Model.overviewLine(view), "1 workspace · 1 agent");
  assert.deepEqual(plain(Model.workspaceSections(view, "w1")).map(s => [s.state, s.items.length]), [["working", 1]]);
});

// Arrange (v0.9.5): sections come from the generated contract, as in the panel.
const Contract = {};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../../plugin/alexvdb.herdr-attention/Contract.js"), "utf8")
  .replace(/^\.pragma library\s*$/m, ""), Contract);
const SECTIONS = Contract.UI.arrange.sections;
const account = (id, worst, extra) => Object.assign({ id, name: id, worst_pct: worst, level: worst >= 100 ? "wall" : worst >= 90 ? "high" : worst >= 70 ? "mid" : "low",
  flag: worst >= 100 ? { id: "full", label: "Full" } : worst >= 90 ? { id: "nearly_full", label: "Nearly full" } : null }, extra || {});
const records = [account("claude:personal", 40), account("codex", 100), account("agy", 95), account("grok", 10), account("new-one", 5)];

test("arrange layout: completed section order, Needs action never hidden, unknown ids dropped", () => {
  const l = plain(Model.arrangeLayout({ layout: { section_order: ["quotas", "bogus"], section_hidden: ["needs_action", "other_updates", "bogus"] } }, SECTIONS));
  assert.deepEqual(l.section_order, ["quotas", "needs_action", "status_reports", "latest_visuals", "other_updates"]);
  assert.deepEqual(l.section_hidden, ["other_updates"]);
  assert.deepEqual(plain(Model.arrangeLayout(null, SECTIONS)).section_order, plain(SECTIONS.map(s => s.id)));
});

test("accounts: pinned first, then the saved order, new ones last; hidden left out; full ones flagged, not moved", () => {
  const view = { quotas: { accounts: records }, layout: { account_order: ["grok", "codex", "claude:personal"],
                                                          account_pinned: ["agy"], account_hidden: ["claude:personal"] } };
  const shown = plain(Model.shownAccounts(view, SECTIONS));
  assert.deepEqual(shown.map(a => a.id), ["agy", "grok", "codex", "new-one"]);
  assert.equal(shown[2].flag.id, "full", "a full account keeps its place and carries the flag");
  assert.deepEqual(plain(Model.shownAccounts({ quotas: { accounts: records } }, SECTIONS)).map(a => a.id),
                   records.map(a => a.id), "nothing saved: the order the records give, never by how full");
  assert.deepEqual(plain(Model.shownAccounts({}, SECTIONS)), []);
});

test("panel blocks follow the saved order; hidden blocks are named only when they have content", () => {
  const view = { layout: { section_order: ["other_updates", "status_reports", "quotas", "needs_action"], section_hidden: ["quotas", "latest_visuals"] } };
  assert.deepEqual(plain(Model.panelBlocks(view, SECTIONS)), ["other_updates", "needs_action"]);
  assert.deepEqual(plain(Model.panelBlocks(null, SECTIONS)), ["needs_action", "quotas", "other_updates"]);
  assert.deepEqual(plain(Model.hiddenBlocks(view, SECTIONS, { quotas: true, latest_visuals: true })), ["quotas"]);
  assert.deepEqual(plain(Model.hiddenBlocks(view, SECTIONS, { quotas: false })), []);
  assert.equal(Model.sectionLabel(SECTIONS, "other_updates"), "Other updates");
});

test("quota colours: the web gauge rule (ok, mid for 70-99 %, full at the wall)", () => {
  assert.deepEqual(["low", "mid", "high", "wall", undefined].map(Model.quotaTone), ["levelOk", "levelMid", "levelMid", "levelFull", "levelOk"]);
  assert.deepEqual([null, { id: "nearly_full" }, { id: "full" }, { id: "stopped" }].map(Model.flagTone), ["", "levelMid", "levelFull", "levelFull"]);
});

test("unread updates split into needs action and other updates, like the web", () => {
  const view = { notify_tiers: { tiers: [{ id: "waiting_on_you", actionable: true }, { id: "fyi_finished", actionable: false }] },
    notifications: { items: [
      { id: 1, tier: "waiting_on_you", read: false },
      { id: 2, tier: "fyi_finished", read: false },
      { id: 3, tier: "fyi_finished", read: false, actionable: true },
      { id: 4, tier: "waiting_on_you", read: true },
      { id: 5, tier: "waiting_on_you", read: false, is_group_latest: false },
    ] } };
  assert.deepEqual(plain(Model.unreadUpdates(view, true)).map(n => n.id), [1, 3]);
  assert.deepEqual(plain(Model.unreadUpdates(view, false)).map(n => n.id), [2]);
  assert.deepEqual(plain(Model.unreadUpdates(null, true)), []);
});

console.log(`model.test.js: ${passed} passed`);
