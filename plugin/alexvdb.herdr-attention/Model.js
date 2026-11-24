.pragma library

// Pure helpers for the panel. No QML types here so they can be unit tested
// with node (tests/js/model.test.js).

var ALERT_KINDS = ["approval", "question", "blocked", "failure", "completion", "stall"]
var NEEDS_YOU = ["approval", "question", "blocked", "failure"]

function isAlert(kind) { return ALERT_KINDS.indexOf(kind) >= 0 }
function needsYou(kind) { return NEEDS_YOU.indexOf(kind) >= 0 }

function duration(seconds) {
  var s = Math.max(0, Math.floor(seconds))
  if (s < 60) return s + "s"
  if (s < 3600) return Math.floor(s / 60) + "m"
  if (s < 86400) {
    var m = Math.floor((s % 3600) / 60)
    return Math.floor(s / 3600) + "h" + (m < 10 ? "0" : "") + m
  }
  return Math.floor(s / 86400) + "d"
}

function ago(ts, nowSec) {
  if (!ts) return "?"
  return duration(nowSec - ts)
}

// "12m" when we watched the change happen, "≥12m" when the state was already
// there when the collector first looked (start time unknown).
function since(agent, nowSec) {
  if (!agent || !agent.status_since) return ""
  return (agent.since_known ? "" : "≥") + duration(nowSec - agent.status_since)
}

function tone(kind) {
  if (needsYou(kind)) return "urgent"
  if (kind === "completion") return "good"
  if (kind === "stall") return "warn"
  if (kind === "working") return "accent"
  return "dim"
}

function displayName(agent) {
  if (!agent) return ""
  return agent.name || ((agent.agent || "agent") + " " + (agent.pane_id || ""))
}

// Split agents into the three panel sections. The collector already sorts
// needs-you first; this keeps that order inside each group.
function agentTier(a) {
  if (!a) return null
  if (a.tier) return a.tier
  if (a.acked) return null
  if (isAlert(a.kind) && ["approval", "question", "blocked", "failure"].indexOf(a.kind) >= 0) return "waiting_on_you"
  if (a.kind === "completion") return "fyi_finished"
  if (a.kind === "stall") return "might_look"
  return null
}

function groups(view) {
  var out = { attention: [], working: [], other: [] }
  var agents = view && view.agents ? view.agents : []
  for (var i = 0; i < agents.length; i++) {
    var a = agents[i]
    if (agentTier(a)) out.attention.push(a)
    else if (a.kind === "working") out.working.push(a)
    else out.other.push(a)
  }
  return out
}

function badgeCount(view) {
  return view && view.counts ? (view.counts.attention || 0) : 0
}

function urgentCount(view) {
  return view && view.counts ? (view.counts.waiting_on_you || view.counts.needs_you || 0) : 0
}

function heroMeta(view, online, offlineReason) {
  if (!online) return "Collector offline" + (offlineReason ? " · " + offlineReason : "")
  if (!view) return "Waiting for data…"
  var herdr = view.collector && view.collector.herdr ? view.collector.herdr : {}
  if (!herdr.connected) return "Herdr not reachable" + (herdr.error ? " · " + herdr.error : "")
  var watched = 0
  for (var i = 0; i < (view.workspaces || []).length; i++) if (view.workspaces[i].watched) watched++
  if (watched === 0) return "No workspaces watched yet · open Settings below"
  var c = view.counts || {}
  var parts = []
  var you = c.waiting_on_you || c.needs_you
  if (you) parts.push(you + " waiting on you")
  if (c.waiting_on_coordinator) parts.push(c.waiting_on_coordinator + " waiting on a coordinator")
  if (c.fyi_finished || c.completion) parts.push((c.fyi_finished || c.completion) + " finished")
  if (c.might_look || c.stall) parts.push((c.might_look || c.stall) + " maybe need a look")
  if (c.working) parts.push(c.working + " working")
  return parts.length ? parts.join(" · ") : "All quiet"
}

function phoneText(view) {
  if (!view || !view.notify) return ""
  var n = view.notify
  var p = view.presence || {}
  if (!n.ready) return "Phone alerts off · " + (n.detail || "")
  if (n.mode === "never") return "Phone alerts paused (mode: never)"
  if (n.mode === "always") return "Phone alerts: sent right away"
  if (p.state === "unknown") return "Phone alerts: presence unknown, counts as " + (p.counts_as || "away")
  if (n.away) return "Phone alerts: on (you look away)"
  var mins = Math.round((p.delay_seconds || 300) / 60)
  return "Phone alerts held while you are active (after " + mins + " min idle)" + (n.held ? " · " + n.held + " held" : "")
}

// Human-readable evidence lines. Values come from the collector's rules.
function evidenceLines(agent) {
  var e = agent && agent.evidence ? agent.evidence : {}
  var lines = []
  if (e.herdr_status) lines.push("Herdr status: " + e.herdr_status)
  if (e.rule) lines.push("Matched screen rule: " + e.rule)
  if (e.working_minutes !== undefined)
    lines.push("Working for " + (e.working_since_known ? "" : "at least ") + e.working_minutes + " min (limit " + e.threshold_minutes + ")")
  if (e.output_unchanged_minutes !== undefined)
    lines.push("Same screen in " + (e.output_checks || "several") + " checks over " + e.output_unchanged_minutes + " min (every " + e.output_checked_every_seconds + " s)")
  if (e.output_check) lines.push("Output check " + e.output_check)
  if (e.reason) lines.push("Reason: " + e.reason + (e.last_status ? " (was " + e.last_status + ")" : ""))
  if (e.note) lines.push(e.note)
  if (agent && agent.confidence) lines.push("Confidence: " + agent.confidence)
  if (agent && agent.seen_late) lines.push("Seen late: it happened while the collector or Herdr was offline")
  return lines
}

function clampStep(value, delta, low, high) {
  var v = Number(value) + delta
  return Math.max(low, Math.min(high, v))
}

function toggled(list, id, on) {
  var out = []
  var seen = false
  for (var i = 0; i < (list || []).length; i++) {
    if (list[i] === id) { seen = true; if (on) out.push(id) }
    else out.push(list[i])
  }
  if (on && !seen) out.push(id)
  out.sort()
  return out
}

// Workspace-centred helpers (second iteration) -------------------------------

var STATES = ["needs_you", "working", "done", "waiting", "parked", "unknown", "out_of_quota"]
var STATE_LABEL = { needs_you: "Needs you", working: "Working", done: "Done, not reviewed",
                    waiting: "Waiting", parked: "Parked", unknown: "Unclear" }
var STATE_SHORT = { needs_you: "need you", working: "working", done: "done",
                    waiting: "waiting", parked: "parked", unknown: "unclear" }

function stateTone(state) {
  if (state === "needs_you") return "urgent"
  if (state === "out_of_quota") return "warn"
  if (state === "working") return "accent"
  if (state === "done") return "good"
  if (state === "waiting") return "warn"
  return "dim"
}

function countsText(counts) {
  var parts = []
  for (var i = 0; i < STATES.length; i++) {
    var s = STATES[i]
    if (counts && counts[s]) parts.push(counts[s] + " " + STATE_SHORT[s])
  }
  return parts.length ? parts.join(" · ") : "no agents running"
}

// [{ state, label, fraction }] for the coloured bar.
function barSegments(counts) {
  var total = 0
  for (var i = 0; i < STATES.length; i++) total += (counts && counts[STATES[i]]) || 0
  var out = []
  for (var j = 0; j < STATES.length; j++) {
    var n = (counts && counts[STATES[j]]) || 0
    if (n) out.push({ state: STATES[j], label: STATE_SHORT[STATES[j]], fraction: n / Math.max(1, total) })
  }
  return out
}

// Same agents the web counts: live, and not folded under a parent
// (finished in-process subagents, content.decorate_subagents).
function shownAgent(a) {
  return !!a && a.present !== false && !a.folded
}

// Agents of one workspace grouped by state, in display order.
function workspaceSections(view, workspaceId) {
  var agents = view && view.agents ? view.agents : []
  var out = []
  for (var i = 0; i < STATES.length; i++) {
    var list = []
    for (var j = 0; j < agents.length; j++)
      if (shownAgent(agents[j]) && agents[j].workspace_id === workspaceId && agents[j].state === STATES[i]) list.push(agents[j])
    if (list.length) out.push({ state: STATES[i], title: (STATE_LABEL[STATES[i]] || list[0].state_label) + " · " + list.length, items: list })
  }
  return out
}

function attentionItems(view) {
  var agents = view && view.agents ? view.agents : []
  var out = []
  for (var i = 0; i < agents.length; i++) if (agentTier(agents[i])) out.push(agents[i])
  return out
}

function lastActivity(agent, nowSec) {
  if (!agent) return ""
  if (agent.last_activity_at) return "active " + duration(nowSec - agent.last_activity_at) + " ago"
  return "no activity seen yet"
}

function workspaceFoot(ws, nowSec) {
  var parts = [ws.last_activity_at ? "active " + duration(nowSec - ws.last_activity_at) + " ago" : "no activity seen yet"]
  if (ws.stalled) parts.push(ws.stalled + " possibly stalled")
  if (ws.muted) parts.push("phone alerts muted")
  return parts.join(" · ")
}

function tabsText(ws, view) {
  if (!ws || !ws.tabs) return ""
  var names = {}
  var agents = view && view.agents ? view.agents : []
  for (var i = 0; i < agents.length; i++) names[agents[i].identity] = displayName(agents[i])
  var parts = []
  for (var t = 0; t < ws.tabs.length; t++) {
    var members = []
    for (var m = 0; m < ws.tabs[t].agents.length; m++) members.push(names[ws.tabs[t].agents[m]] || "?")
    parts.push(ws.tabs[t].label + ": " + members.join(", "))
  }
  return parts.join("\n")
}

// Facts for an expanded agent row. Unavailable values say so.
function factLines(agent, nowSec, herdrConnected) {
  if (!agent) return []
  var lines = []
  lines.push("In this state: " + since(agent, nowSec) + (agent.since_known ? "" : " (started before the collector saw it)"))
  lines.push("Last activity: " + lastActivity(agent, nowSec))
  lines.push("Last seen: " + (!agent.present ? "gone" : herdrConnected ? "here now" : duration(nowSec - agent.last_seen) + " ago"))
  lines.push("Tab: " + (agent.tab_label || "unknown"))
  lines.push("Folder: " + (agent.cwd || "unknown"))
  var roles = agent.roles || {}
  function labelText(items) {
    if (!items || !items.length) return "not declared"
    var parts = []
    for (var i = 0; i < items.length; i++) parts.push(items[i].value + " (" + items[i].provenance + ")")
    return parts.join(", ")
  }
  lines.push("Role: " + labelText(roles.role) + " · Team: " + labelText(roles.team) + " · Task: " + labelText(roles.task))
  lines.push("Last finished: " + (agent.last_done ? duration(nowSec - agent.last_done.at) + " ago" + (agent.last_done.title ? " · " + agent.last_done.title : "") : "nothing seen yet"))
  return lines.concat(metricLines(agent))
}

function tokenText(n) {
  if (n === null || n === undefined) return "?"
  if (n < 1000) return String(n)
  if (n < 1000000) return (n / 1000).toFixed(n < 10000 ? 1 : 0) + "k"
  return (n / 1000000).toFixed(n < 10000000 ? 2 : 1) + "M"
}

function hoursText(seconds) {
  var m = Math.floor(seconds / 60)
  if (m < 60) return m + " min"
  var h = Math.floor(m / 60)
  if (h < 48) return h + " h " + (m % 60 < 10 ? "0" : "") + (m % 60) + " min"
  return Math.floor(h / 24) + " d " + (h % 24) + " h"
}

// Trace metrics with how sure they are. "unavailable" rather than a guess.
function metricLines(agent) {
  var m = agent && agent.metrics ? agent.metrics : null
  if (!m) return []
  function mark(x) { return x.exact ? "exact" : (x.kind || "estimate") }
  var c = m.context, t = m.tokens, a = m.age, act = m.active
  var lines = []
  lines.push("Context: " + (c && c.available ? tokenText(c.value) + " tokens" + (c.window ? " of " + tokenText(c.window) + " (" + c.percent + "%)" : "")
             + " · " + mark(c) : "unavailable" + (c && c.reason ? " (" + c.reason + ")" : "")))
  lines.push("Tokens this session: " + (t && t.available ? tokenText(t.value) + " (in " + tokenText(t.parts.input) + ", cache read "
             + tokenText(t.parts.cache_read) + ", cache write " + tokenText(t.parts.cache_write) + ", out " + tokenText(t.parts.output) + ") · " + mark(t)
             : "unavailable" + (t && t.reason ? " (" + t.reason + ")" : "")))
  if (a && a.available) lines.push("Age: " + (a.exact ? "" : "≥ ") + hoursText(a.value) + " · " + a.source)
  if (act && act.available) lines.push("Active (observed working): " + (act.exact ? "" : "≥ ") + hoursText(act.value))
  return lines
}

function connectionRows(agent, view) {
  if (!agent || !view || !view.links) return []
  var names = {}
  for (var i = 0; i < (view.agents || []).length; i++) names[view.agents[i].identity] = view.agents[i]
  var out = []
  for (var j = 0; j < view.links.length; j++) {
    var l = view.links[j]
    if (l.from !== agent.identity && l.to !== agent.identity) continue
    var outgoing = l.from === agent.identity
    var other = names[outgoing ? l.to : l.from]
    var otherHidden = outgoing ? l.hidden : l.from_hidden
    var who = otherHidden ? (l.label || "a hidden or unknown agent") : other ? displayName(other) + (other.workspace_id !== agent.workspace_id ? " (in " + other.workspace_label + ")" : "") : "unknown"
    var verbs = { spawned_by: outgoing ? "spawned by " : "spawned ", forked_from: outgoing ? "forked from " : "forked into ",
                  reports_to: outgoing ? "reports to " : "coordinates " }
    var how = l.provenance ? " (" + l.provenance.join(" + ") + ")" : ""
    var inferred = !!(l.inferred || l.dashed || (l.provenance || []).indexOf("inferred") >= 0)
    out.push({ text: "Link: " + (verbs[l.kind] || l.kind + " ") + who + how + (l.cycle ? " · part of a cycle" : "")
                    + (inferred ? " · " + l.source + " · confidence " + Math.round(l.confidence * 100) + "%" : ""),
               inferred: inferred, dashed: inferred })
  }
  return out
}

function linkLines(agent, view) {
  return connectionRows(agent, view).map(function(row) { return row.text })
}

var STATUS_WORDS = { working: "Started working", idle: "Went idle", done: "Finished a turn",
                     blocked: "Waiting for you", unknown: "Status unclear" }

function timelineLines(detail, nowSec, limit) {
  if (!detail || !detail.activity) return []
  var out = []
  var items = detail.activity.slice(0, limit || 8)
  for (var i = 0; i < items.length; i++) {
    var it = items[i]
    var what = it.kind === "seen" ? "Seen " + (it.status || "with unclear status")
             : it.kind === "status" ? (STATUS_WORDS[it.status] || "Status: " + it.status)
             : it.kind === "title" ? "New task title"
             : it.kind === "gone" ? "Gone"
             : it.kind === "reply" ? (it.status === "sent" ? "Reply sent" : "Reply not sent") : it.kind
    out.push((it.live ? "" : "≈ ") + duration(nowSec - it.at) + " ago · " + what + (it.title ? ": " + it.title : ""))
  }
  return out
}

function overviewLine(view) {
  if (!view || !view.workspaces) return ""
  var w = view.workspaces.length
  var a = view.agents ? view.agents.filter(shownAgent).length : 0
  var c = view.counts || {}
  var parts = [w + " workspace" + (w === 1 ? "" : "s"), a + " agent" + (a === 1 ? "" : "s")]
  if (c.waiting_on_you || c.needs_you) parts.push((c.waiting_on_you || c.needs_you) + " waiting on you")
  if (c.state_working) parts.push(c.state_working + " working")
  return parts.join(" · ")
}

// AI summary (only when it is about the current item; never invented).
function summaryHeadline(agent) {
  var s = agent && agent.summary
  if (s && s.status === "complete" && s.current && s.headline) return "AI: " + s.headline
  return ""
}

function summaryLines(agent) {
  var s = agent && agent.summary
  if (!s || !s.current) return []
  if (s.status === "queued" || s.status === "running") return ["AI summary: being written…"]
  if (s.status === "failed") return ["AI summary failed: " + (s.error || "unknown error")]
  if (s.status !== "complete") return []
  var lines = ["AI summary (" + (s.resolved_model || s.model) + ", effort " + s.effort + "): " + (s.headline || "")]
  var n = s.needs_you
  if (n && n.kind && n.kind !== "none") {
    lines.push("Asks (" + n.kind + "): " + (n.ask || ""))
    for (var i = 0; i < (n.options || []).length; i++) lines.push("  option: \u201c" + n.options[i] + "\u201d")
  }
  return lines
}

// Herdr's own status colours (view.status_colors, read from Herdr's theme by
// the collector). Returns "" when unknown so callers keep their fallback.
var STATE_TO_HERDR = { needs_you: "blocked", working: "working", done: "done", waiting: "idle", parked: "idle", unknown: "unknown" }

function herdrStatus(agent) {
  if (!agent || !agent.present) return agent && agent.kind === "failure" ? "blocked" : "unknown"
  var s = agent.status
  return (s === "working" || s === "blocked" || s === "done" || s === "idle") ? s : "unknown"
}

function herdrColor(view, agent) {
  var c = view && view.status_colors && view.status_colors.colors
  return c && c[herdrStatus(agent)] ? c[herdrStatus(agent)] : ""
}

function stateHerdrColor(view, state) {
  var c = view && view.status_colors && view.status_colors.colors
  var key = STATE_TO_HERDR[state]
  return c && key && c[key] ? c[key] : ""
}


function layoutText(workspace) {
  var hints = workspace && workspace.layout_hints || []
  var lines = []
  for (var i = 0; i < hints.length; i++) lines.push(hints[i].tab_id + ": " + hints[i].message)
  return hints.length ? "Layout (quiet suggestions)\n" + lines.join("\n") : ""
}


function workspaceLiveCount(workspace) {
  return workspace.live_agent_count !== undefined ? workspace.live_agent_count : (workspace.agent_count || 0)
}

function activeWorkspaces(view) {
  return (view && view.workspaces || []).filter(function(w) { return workspaceLiveCount(w) > 0 })
}

function emptyWorkspaces(view) {
  return (view && view.workspaces || []).filter(function(w) { return workspaceLiveCount(w) === 0 })
}

// Desktop address and notification hashes come from the collector contract.
function notificationLink(view, eventId) {
  var desktop = view && view.desktop
  if (!desktop || !desktop.url || !desktop.notifications_hash) return ""
  var route = eventId === undefined || eventId === null ? desktop.notifications_hash
    : desktop.notification_hash ? desktop.notification_hash.replace("{id}", encodeURIComponent(eventId)) : ""
  return route ? String(desktop.url).replace(/\/+$/, "") + "/" + route : ""
}

function notificationsUnread(view) {
  return view && view.notifications && view.notifications.unread ? view.notifications.unread : 0
}

function notificationsText(view) {
  var n = notificationsUnread(view)
  return n ? "Notifications · " + n + " unread" : "Notifications"
}

// Arrange (v0.9.5) ------------------------------------------------------------
// The order Alex saved in the dashboard (view.layout from the layout.* settings).
// Same rules as app.js arrangeLayout() / arrangedAccounts(): pinned first, then
// his order, then new accounts in the order the records give. A nearly full or
// full account gets a flag; it never moves. `sections` is ui_contract.json
// arrange.sections (Contract.UI.arrange.sections in the panel).

// The blocks the bar panel has, by Arrange section id.
var PANEL_BLOCKS = ["needs_action", "quotas", "other_updates"]

function arrangeLayout(view, sections) {
  var l = view && view.layout ? view.layout : {}
  var ids = [], hideable = {}
  for (var i = 0; i < (sections || []).length; i++) {
    ids.push(sections[i].id)
    hideable[sections[i].id] = !!sections[i].hideable
  }
  var order = (l.section_order || []).filter(function(id) { return ids.indexOf(id) >= 0 })
  for (var j = 0; j < ids.length; j++) if (order.indexOf(ids[j]) < 0) order.push(ids[j])
  return { account_order: (l.account_order || []).slice(), account_pinned: (l.account_pinned || []).slice(),
           account_hidden: (l.account_hidden || []).slice(), section_order: order,
           section_hidden: (l.section_hidden || []).filter(function(id) { return hideable[id] === true }) }
}

function arrangedAccounts(accounts, layout) {
  function pos(id) { var i = layout.account_order.indexOf(id); return i < 0 ? Infinity : i }
  function pinned(id) { return layout.account_pinned.indexOf(id) >= 0 ? 0 : 1 }
  return (accounts || []).map(function(a, i) { return { a: a, i: i } }).sort(function(x, y) {
    return (pinned(x.a.id) - pinned(y.a.id)) || (pos(x.a.id) - pos(y.a.id)) || (x.i - y.i)
  }).map(function(x) { return x.a })
}

// Quota accounts to show, in Alex's order, without the ones he hid.
function shownAccounts(view, sections) {
  var layout = arrangeLayout(view, sections)
  var accounts = view && view.quotas && view.quotas.accounts ? view.quotas.accounts : []
  return arrangedAccounts(accounts, layout).filter(function(a) { return layout.account_hidden.indexOf(a.id) < 0 })
}

// The panel's blocks in Alex's order, without the hidden ones.
function panelBlocks(view, sections) {
  var layout = arrangeLayout(view, sections)
  return layout.section_order.filter(function(id) {
    return PANEL_BLOCKS.indexOf(id) >= 0 && layout.section_hidden.indexOf(id) < 0
  })
}

// Hidden panel blocks that have something to show, so one muted line can name them.
function hiddenBlocks(view, sections, present) {
  var layout = arrangeLayout(view, sections)
  return layout.section_order.filter(function(id) {
    return PANEL_BLOCKS.indexOf(id) >= 0 && layout.section_hidden.indexOf(id) >= 0 && !!(present && present[id])
  })
}

function sectionLabel(sections, id) {
  for (var i = 0; i < (sections || []).length; i++) if (sections[i].id === id) return sections[i].label
  return id
}

// Quota level (quotas.py quota_level) and flag -> colour token name, as the web gauges.
function quotaTone(level) {
  return level === "wall" ? "levelFull" : (level === "mid" || level === "high") ? "levelMid" : "levelOk"
}

function flagTone(flag) {
  if (!flag) return ""
  return flag.id === "nearly_full" ? "levelMid" : "levelFull"
}

// Same rule as app.js notifActionable(): the item's own flag, else its tier's.
function notifActionable(n, view) {
  if (n && n.actionable !== undefined && n.actionable !== null) return !!n.actionable
  var tiers = view && view.notify_tiers && view.notify_tiers.tiers ? view.notify_tiers.tiers : []
  for (var i = 0; i < tiers.length; i++) if (n && tiers[i].id === n.tier) return !!tiers[i].actionable
  return false
}

// Unread updates (latest of each group) that need action, or that do not.
function unreadUpdates(view, actionable) {
  var items = view && view.notifications && view.notifications.items ? view.notifications.items : []
  return items.filter(function(n) {
    return !n.read && n.is_group_latest !== false && notifActionable(n, view) === !!actionable
  })
}
