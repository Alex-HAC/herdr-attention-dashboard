// Sample data for scripts/qml-preview.sh (renders the panel parts offscreen).
// Colours: Tokens.palette(view), as in the panel. Blocks and accounts: the saved
// Arrange order (view.layout), through the same Model helpers the panel uses.
import QtQuick
import QtQuick.Window
import qs.Commons
import qs.Ui
import "file://@PLUGIN@"
import "file://@PLUGIN@/Contract.js" as Contract
import "file://@PLUGIN@/Model.js" as Model
import "file://@PLUGIN@/Tokens.js" as Tokens

Window {
  id: win
  width: @WIDTH@; height: col.implicitHeight + 28; visible: true
  color: tokens.bg
  readonly property real now: Date.now() / 1000

  function a(id, name, kind, state, label, tab, title, since, extra) {
    var o = { identity: id, name: name, agent: id.split(":")[0], title: title, pane_id: "w1:p" + id.length,
              workspace_id: "w1", workspace_label: "Website", status: state, present: true,
              status_since: now - since, since_known: true, kind: kind, label: label, state: state,
              state_label: Model.STATE_LABEL[state], confidence: "medium", tab_label: tab, cwd: "~/Projects/web",
              evidence: { herdr_status: state === "needs_you" ? "blocked" : state }, event_id: null, acked: false,
              last_activity_at: now - since, last_seen: now, first_seen: now - 7200 }
    for (var k in extra) o[k] = extra[k]
    return o
  }
  function quota(id, name, pct, level, flag, reset, agents) {
    return { id: id, name: name, worst_pct: pct, level: level, flag: flag, rings: pct === null ? [] : [{ pct: pct, level: level }],
             reset_label: reset, agents_label: agents, stale: false }
  }
  readonly property var agents: [
    a("codex:ui", "ui-builder", "approval", "needs_you", "Approval needed", "Builders", "Build the cart page", 300,
      { event_id: 3, evidence: { herdr_status: "blocked", rule: "bash_permission_prompt" },
        // v0.9.7: recorded accounts carry the icon object (tests/icon_fixture.py); the others have none.
        icon: { account: "codex", label: "Codex Work", glyph: "codex", color: "red", badge: "Wk", image: null } }),
    a("claude:lead", "web-lead", "stall", "working", "Possibly stalled", "Lead", "Review the checkout plan", 1500,
      { evidence: { herdr_status: "working", working_minutes: 25, working_since_known: true, threshold_minutes: 15,
                    output_unchanged_minutes: 17, output_checked_every_seconds: 60 },
        icon: { account: "claude:main", label: "Claude Code Main", glyph: "claude", color: "yellow", badge: "M", image: null } }),
    a("codex:api", "api-worker", "working", "working", "Working", "Builders", "Wire the payments API", 600, {}),
    a("claude:docs", "docs-writer", "idle", "waiting", "Idle", "Docs", "Write the API guide", 720,
      { last_done: { at: now - 720, title: "Write the API guide", seen_late: false } }),
    a("claude:old", "old-spike", "idle", "parked", "Idle", "Spikes", "Spike: payment SDK", 9000, { last_activity_at: null })
  ]
  readonly property var workspace: ({ id: "w1", label: "Website", agent_count: 5, attention: 2, stalled: 1, muted: false,
    counts: { needs_you: 1, working: 2, done: 0, waiting: 1, parked: 1, unknown: 0 }, last_activity_at: now - 300,
    tabs: [{ id: "t1", label: "Lead", agents: ["claude:lead"] }, { id: "t2", label: "Builders", agents: ["codex:ui", "codex:api"] },
           { id: "t3", label: "Docs", agents: ["claude:docs"] }, { id: "t4", label: "Spikes", agents: ["claude:old"] }] })
  readonly property var other: ({ id: "w2", label: "Data pipeline", agent_count: 2, attention: 1, stalled: 0, muted: true,
    counts: { needs_you: 0, working: 1, done: 1, waiting: 0, parked: 0, unknown: 0 }, last_activity_at: now - 3300, tabs: [] })
  readonly property var detail: ({ activity: [
    { at: now - 1500, kind: "title", status: "working", title: "Review the checkout plan", live: true },
    { at: now - 3600, kind: "status", status: "working", title: "Plan the checkout redesign", live: true },
    { at: now - 7200, kind: "seen", status: "idle", title: "Plan the checkout redesign", live: false }] })
  // Alex's Arrange: Quotas first; Antigravity pinned; Grok hidden; Codex is full but stays in his place.
  readonly property var view: ({ agents: win.agents, workspaces: [win.workspace, win.other], features: { acknowledge: true },
    quotas: { header: { label: "Antigravity 95% wk" }, accounts: [
      quota("claude:personal", "Claude Personal", 41, "low", null, "resets 18:00", "2 agents"),
      quota("codex", "Codex Work", 100, "wall", { id: "full", label: "Full" }, "resets Fri 09:00", "1 agent"),
      quota("agy", "Antigravity", 95, "high", { id: "nearly_full", label: "Nearly full" }, "resets 21:30", "No agents"),
      quota("grok", "Grok Bot", 12, "low", null, "resets Sun", "No agents"),
      quota("new", "New service", null, "low", null, "No limit data", "No agents")] },
    layout: { account_order: ["claude:personal", "codex"], account_pinned: ["agy"], account_hidden: ["grok"],
              section_order: ["quotas", "needs_action", "status_reports", "latest_visuals", "other_updates"],
              section_hidden: ["other_updates"] },
    notifications: { items: [
      { id: 11, tier: "waiting_on_you", actionable: true, read: false, what: "ui-builder needs approval to run the cart tests",
        body: "Bash: npm test -- cart", agent: { name: "ui-builder" }, at_label: "5 min ago" },
      { id: 12, tier: "fyi_finished", actionable: false, read: false, what: "docs-writer finished", agent: { name: "docs-writer" }, at_label: "12 min ago" }] } })
  readonly property var tokens: Tokens.palette(view)
  readonly property var sections: Contract.UI.arrange.sections
  readonly property var blocks: Model.panelBlocks(view, sections)
  readonly property var hidden: Model.hiddenBlocks(view, sections, { needs_action: true, quotas: true, other_updates: true })
  readonly property var panelStub: ({ view: win.view, tokens: win.tokens,
    details: { "claude:lead": win.detail }, expandedIdentity: "claude:lead", nowSec: win.now, herdrConnected: true,
    fontFamily: Style.font.family, service: null, toggleExpanded: function() {} })

  Component {
    id: quotasPart
    Column {
      width: col.width
      spacing: Tokens.space["1"]
      TokenButton { tokens: win.tokens; text: Model.sectionLabel(win.sections, "quotas") + " · " + win.view.quotas.header.label }
      Repeater {
        model: Model.shownAccounts(win.view, win.sections)
        delegate: AccountRow { required property var modelData; account: modelData; tokens: win.tokens; width: col.width }
      }
    }
  }
  Component {
    id: needsPart
    Column {
      width: col.width
      spacing: Tokens.space["2"]
      Repeater {
        model: Model.unreadUpdates(win.view, true)
        delegate: UpdateRow { required property var modelData; note: modelData; tokens: win.tokens }
      }
      PanelSectionHeader { color: win.tokens.muted; text: "To look at (2)" }
      AgentRow { agent: win.agents[0]; view: win.view; width: col.width; nowSec: win.now }
    }
  }

  // The grab holds only the items, not the window colour: paint the theme background.
  Rectangle { anchors.fill: parent; color: win.tokens.bg }

  Column {
    id: col
    x: 14; y: 14; width: win.width - 28
    spacing: Tokens.space["4"]
    Row {
      spacing: Style.space(14)
      Text { text: "󰚩"; color: win.tokens.needs; font.pixelSize: Style.font.display; anchors.verticalCenter: parent.verticalCenter }
      Column {
        anchors.verticalCenter: parent.verticalCenter
        Text { text: "Herdr agents"; color: win.tokens.textStrong; font.pixelSize: Tokens.type.title; font.bold: true }
        Text { text: "2 WORKSPACES · 7 AGENTS · 1 WAITING ON YOU"; color: win.tokens.muted; font.pixelSize: Tokens.type.micro; font.bold: true; font.letterSpacing: 1.2 }
      }
    }
    Text { width: parent.width; wrapMode: Text.WordWrap; text: "Phone alerts held while you are active (after 5 min idle)"; color: win.tokens.muted; font.pixelSize: Tokens.type.small }
    Repeater {
      model: win.blocks
      delegate: Loader { required property string modelData; width: col.width; sourceComponent: modelData === "quotas" ? quotasPart : needsPart }
    }
    Text { width: parent.width; wrapMode: Text.WordWrap; color: win.tokens.muted; font.pixelSize: Tokens.type.small
           text: Contract.UI.arrange.labels.hidden_blocks + " " + win.hidden.map(function(id) { return Model.sectionLabel(win.sections, id) + " (1)" }).join(", ") }
    PanelSectionHeader { color: win.tokens.muted; text: "Workspaces (2)" }
    WorkspaceCard { workspace: win.workspace; panel: win.panelStub; open: true; width: col.width }
    WorkspaceCard { workspace: win.other; panel: win.panelStub; open: false; width: col.width }
  }
  Timer { interval: 900; running: true; onTriggered: win.contentItem.grabToImage(function(r) { r.saveToFile("@OUT@"); console.log("SAVED"); Qt.quit() }) }
}
