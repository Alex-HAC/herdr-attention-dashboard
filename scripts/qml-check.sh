#!/bin/bash
# Check the desktop plugin's QML before it reaches the shell:
#  1. scan every .qml file for duplicate property/handler assignments
#  2. load and instantiate every file in the real Quickshell engine, using
#     Omarchy's own Commons/Ui modules, in a throwaway instance with no
#     windows. The plugin's CLI calls are pointed at /bin/true.
#
# Usage: scripts/qml-check.sh [plugin-dir]
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
plugin=$(cd "${1:-$here/../plugin/alexvdb.herdr-attention}" && pwd)
shell_dir=${OMARCHY_SHELL_DIR:-/usr/share/omarchy/shell}

echo "== duplicate assignment scan"
python3 "$here/qml_dupes.py" "$plugin"/*.qml

echo "== JSON manifest"
python3 -c 'import json,sys; m=json.load(open(sys.argv[1])); assert m["id"]=="alexvdb.herdr-attention", m["id"]; print("manifest ok:", m["id"], m["version"])' "$plugin/manifest.json"

if ! command -v qs >/dev/null || [[ ! -d $shell_dir/Commons ]]; then
  echo "== real-engine load SKIPPED (quickshell or $shell_dir missing)"
  exit 2
fi

echo "== real-engine load (quickshell $(qs --version | awk '{print $2}'))"
harness=$(mktemp -d "${XDG_RUNTIME_DIR:-/tmp}/hat-qml.XXXXXX")
trap 'rm -rf "$harness"' EXIT
ln -s "$shell_dir/Commons" "$harness/Commons"
ln -s "$shell_dir/Ui" "$harness/Ui"
ln -s "$plugin/Tokens.js" "$harness/Tokens.js"   # the fixtures use the real palette()

cat > "$harness/shell.qml" <<QML
import QtQuick
import Quickshell
import "Tokens.js" as Tokens

ShellRoot {
  Component.onCompleted: {
    var dir = "file://$plugin/"
    var sample = {
      identity: "claude:test", name: "tester", agent: "claude", title: "Sample task",
      pane_id: "w9:p1", workspace_id: "w9", workspace_label: "Sample", status: "blocked",
      present: true, status_since: Date.now() / 1000 - 300, since_known: true,
      kind: "approval", label: "Approval needed", confidence: "medium",
      evidence: { herdr_status: "blocked", rule: "bash_permission_prompt" },
      event_id: 1, acked: false, seen_late: false, state: "needs_you", state_label: "Needs you",
      tab_id: "w9:t1", tab_label: "Builders", cwd: "~/x", last_activity_at: Date.now() / 1000 - 60,
      last_seen: Date.now() / 1000, first_seen: Date.now() / 1000 - 600,
      last_done: { at: Date.now() / 1000 - 900, title: "Earlier task", seen_late: false },
      metrics: { context: { available: true, value: 150400, window: 400000, percent: 37.6, exact: false, kind: "estimate" },
                 tokens: { available: true, value: 535000, exact: true, parts: { input: 1, cache_read: 2, cache_write: 0, output: 3 } },
                 age: { available: true, value: 3600, exact: true, source: "session start in the trace" },
                 active: { available: true, value: 600, exact: true } },
      summary: { status: "complete", current: true, headline: "Wants to run the tests", model: "sonnet", effort: "medium",
                 resolved_model: "claude-sonnet-5-5", needs_you: { kind: "approval", ask: "Run tests?", options: ["Yes", "No"] } }
    }
    var detail = { activity: [{ at: Date.now() / 1000 - 60, kind: "status", status: "blocked", title: "Sample task", live: true },
                              { at: Date.now() / 1000 - 600, kind: "seen", status: "working", title: "Sample task", live: false }] }
    var workspace = { id: "w9", label: "Sample", agent_count: 1, attention: 1, stalled: 0, muted: false,
                      counts: { needs_you: 1, working: 0, done: 0, waiting: 0, parked: 0, unknown: 0 },
                      last_activity_at: Date.now() / 1000 - 60, tabs: [{ id: "w9:t1", label: "Builders", agents: ["claude:test"] }] }
    var fakePanel = { view: { agents: [sample], workspaces: [workspace], features: { acknowledge: true },
                              status_colors: { colors: { working: "#e0af68", blocked: "#ff7a93", done: "#449dab", idle: "#9ece6a", unknown: "#a9b1d6" } },
                              links: [{ from: "claude:test", to: "hidden-1", kind: "forked_from", hidden: true }] },
                      details: { "claude:test": detail }, expandedIdentity: "claude:test", nowSec: Date.now() / 1000,
                      herdrConnected: true, urgent: "#c33", accent: "#36c", warnColor: "#c93", goodColor: "#3a6",
                      fontFamily: "monospace", service: null, toggleExpanded: function() {} }
    // An unusual light theme: every panel colour must come from these tokens (view.theme.tokens).
    var theme = { tokens: { "text": "#102030", "text-strong": "#000814", "muted": "#203040", "needs": "#a01020",
                            "needs-ink": "#fffcf0", "accent": "#3050a0", "accent-ink": "#fffcf1", "caution": "#a05010",
                            "done": "#106050", "raised": "#e8e4dc", "bg": "#fbf8f0", "line": "#d0ccc4",
                            "level-ok": "#207020", "level-mid": "#806000", "level-full": "#b00020", "pressed": "#dcd8d0" } }
    // Arrange (view.layout): pinned first, then the saved order; hidden accounts and blocks left out;
    // the full account is flagged where Alex put it.
    function quota(id, name, pct, level, flag) {
      return { id: id, name: name, worst_pct: pct, level: level, rings: pct === null ? [] : [{ pct: pct, level: level }],
               flag: flag, reset_label: "resets 18:00", agents_label: "1 agent", stale: false }
    }
    var accounts = [quota("claude:personal", "Claude Personal", 40, "low", null),
                    quota("codex", "Codex Work", 100, "wall", { id: "full", label: "Full" }),
                    quota("agy", "Antigravity", 95, "high", { id: "nearly_full", label: "Nearly full" }),
                    quota("grok", "Grok", null, "low", null)]
    var themedView = { agents: [sample], workspaces: [workspace], counts: { attention: 1, waiting_on_you: 1 },
                       features: { acknowledge: true }, collector: { herdr: { connected: true } }, links: [], theme: theme,
                       quotas: { header: { label: "Codex Work 100% wk" }, accounts: accounts },
                       layout: { account_order: ["codex", "claude:personal"], account_pinned: ["agy"], account_hidden: ["grok"],
                                 section_order: ["quotas", "other_updates", "needs_action"], section_hidden: ["other_updates"] },
                       notifications: { items: [
                         { id: 7, tier: "fyi_finished", actionable: false, read: false, what: "Finished the tests", agent: { name: "tester" }, at_label: "1 min ago" },
                         { id: 8, tier: "waiting_on_you", actionable: true, read: false, what: "Approve the push", agent: { name: "tester" }, at_label: "now" }] } }
    var levelTokens = Tokens.palette(themedView)   // the whole set, as the panel passes it
    var themedService = { view: themedView, online: true, outputs: {}, actionError: "", offlineReason: "", cli: "/bin/true" }
    function same(a, b) { return String(a).toLowerCase() === String(b).toLowerCase() }
    var cases = [
      { file: "Service.qml", props: {} },
      { file: "RelationshipLine.qml", props: { width: 420, connection: { text: "inferred: fixture brief", dashed: true } } },
      { file: "RelationshipLine.qml", props: { width: 420, connection: { text: "declared fixture", dashed: false } } },
      { file: "AgentRow.qml", props: { agent: sample, expanded: true, detail: detail, inWorkspace: true,
                                       output: { text: "line\\nline", loading: false, error: "" } } },
      { file: "AgentRow.qml", themed: true, props: { agent: sample, view: themedView, expanded: true, detail: detail,
                                       output: { text: "out", loading: false, error: "" } } },
      { file: "WorkspaceCard.qml", props: { workspace: workspace, panel: fakePanel, open: true } },
      { file: "AccountRow.qml", props: { width: 390, account: accounts[1], tokens: levelTokens } },
      { file: "AccountRow.qml", props: { width: 390, account: accounts[2], tokens: levelTokens } },
      { file: "AccountRow.qml", props: { width: 390, account: accounts[3], tokens: levelTokens } },
      { file: "UpdateRow.qml", props: { width: 390, note: themedView.notifications.items[0] } },
      { file: "TokenButton.qml", props: { text: "Arrange", tokens: levelTokens } },
      // v0.9.7 account icons: a mark with a badge, text only (no mark: nothing), no icon, and an agent row with one.
      { file: "AccountIcon.qml", icon: "mark", props: { icon: { account: "claude:main", label: "Claude Main", glyph: "claude", color: "yellow", badge: "M", image: null }, tokens: levelTokens, size: 14 } },
      { file: "AccountIcon.qml", icon: "text", props: { icon: { account: "copilot", label: "Copilot", glyph: null, color: "neutral", badge: null, image: null }, tokens: levelTokens } },
      { file: "AccountIcon.qml", icon: "none", props: { icon: null, tokens: levelTokens } },
      // A badge is plain text: '<b' must show as the two characters, not start a bold tag.
      { file: "AccountIcon.qml", icon: "markup", props: { icon: { account: "x", label: "<b>Label & co</b>", glyph: "circle", color: "blue", badge: "<b", image: null }, tokens: levelTokens, size: 14 } },
      { file: "AgentRow.qml", props: { agent: Object.assign({}, sample, { icon: { account: "codex", label: "Codex Work", glyph: "codex", color: "red", badge: "Wk", image: null } }),
                                       expanded: false, detail: detail, inWorkspace: true, output: { text: "", loading: false, error: "" } } },
      { file: "Panel.qml", props: {} },
      { file: "Panel.qml", themed: true, props: { service: themedService } }
    ]
    var failed = 0
    for (var i = 0; i < cases.length; i++) {
      var c = Qt.createComponent(dir + cases[i].file, Component.PreferSynchronous)
      if (c.status !== Component.Ready) {
        console.error("LOAD_FAIL " + cases[i].file + ": " + c.errorString())
        failed++
        continue
      }
      var o = c.createObject(null, cases[i].props)
      if (!o) { console.error("CREATE_FAIL " + cases[i].file); failed++; continue }
      console.log("CREATED " + cases[i].file)
      if (cases[i].file === "RelationshipLine.qml") {
        var dashed = cases[i].props.connection.dashed
        if (o.segmentCount !== (dashed ? 4 : 1) || o.opacity !== (dashed ? 0.65 : 1)) {
          console.error("RELATION_STYLE_FAIL")
          failed++
        } else console.log("RELATION_STYLE " + (dashed ? "dashed fainter" : "solid"))
      }
      if (cases[i].file === "Service.qml") {
        o.handleLine(JSON.stringify({ version: 1, agents: [sample], counts: { attention: 1, needs_you: 1 },
                                     workspaces: [], collector: { herdr: { connected: true } } }))
        console.log("SERVICE_VIEW " + (o.view && o.view.agents.length === 1 ? "ok" : "missing"))
      }
      if (cases[i].themed) {
        var t = theme.tokens
        var colours = cases[i].file === "AgentRow.qml"
          ? [[o.foreground, t.text], [o.dim, t.muted], [o.toneColor, t.needs], [o.urgentColor, t.needs], [o.accent, t.accent]]
          : [[o.foreground, t.text], [o.dim, t.muted], [o.urgent, t.needs], [o.accent, t.accent], [o.strong, t["text-strong"]]]
        var wrong = colours.filter(function(pair) { return !same(pair[0], pair[1]) })
        if (wrong.length) { console.error("THEME_FAIL " + cases[i].file + " " + JSON.stringify(wrong.map(String))); failed++ }
        else console.log("THEME_TOKENS " + cases[i].file)
      }
      if (cases[i].file === "AccountRow.qml") {
        var acc = cases[i].props.account
        var want = acc.level === "wall" ? levelTokens.levelFull : acc.level === "low" ? levelTokens.levelOk : levelTokens.levelMid
        var flagWant = !acc.flag ? levelTokens.muted : acc.flag.id === "full" ? levelTokens.levelFull : levelTokens.levelMid
        if (!same(o.levelColor, want) || !same(o.flagColor, flagWant) || o.hasLimits !== (acc.rings.length > 0)) {
          console.error("ACCOUNT_FAIL " + acc.id + " " + o.levelColor + " " + o.flagColor); failed++
        } else console.log("ACCOUNT_ROW " + acc.id + " " + (acc.flag ? acc.flag.label : "no flag") + " height " + Math.round(o.implicitHeight))
      }
      if (cases[i].file === "Panel.qml" && cases[i].themed) {
        var ids = []
        for (var k = 0; k < o.accounts.length; k++) ids.push(o.accounts[k].id)
        var arranged = o.blockKey === "quotas,needs_action" && ids.join(",") === "agy,codex,claude:personal"
                       && o.hiddenText === "Hidden: Other updates (1)" && o.needsUpdates.length === 1
        if (!arranged) { console.error("ARRANGE_FAIL " + o.blockKey + " | " + ids.join(",") + " | " + o.hiddenText); failed++ }
        else console.log("ARRANGE_ORDER blocks " + o.blockKey + " accounts " + ids.join(",") + " | " + o.hiddenText)
      }
      if (cases[i].file === "AccountIcon.qml") {
        var mark = cases[i].icon === "mark" || cases[i].icon === "markup"
        var okIcon = cases[i].icon === "markup"
          ? (o.visible && o.shownBadge === "<b" && o.badgeFormat === Text.PlainText && o.badgeTextWidth > 0)
          : mark ? (o.visible && o.width === 14 && same(o.tone, levelTokens.iconYellow) && o.badge === "M" && o.badgeFormat === Text.PlainText)
                 : (!o.visible && o.width === 0)
        if (!okIcon) { console.error("ACCOUNT_ICON_FAIL " + cases[i].icon + " " + o.visible + " " + o.width + " " + o.tone); failed++ }
        else console.log("ACCOUNT_ICON " + cases[i].icon + (cases[i].icon === "markup" ? " badge shown as plain text: " + o.shownBadge
                                                          : mark ? " " + o.tone : " nothing drawn"))
      }
      if (cases[i].file === "WorkspaceCard.qml") {
        console.log("WORKSPACE_SECTIONS " + o.sections.length + " height " + Math.round(o.implicitHeight))
      }
      o.destroy()
    }
    console.log(failed ? "QML_CHECK_FAILED" : "QML_CHECK_PASSED")
    Qt.callLater(Qt.quit)
  }
}
QML

log=$(cd "$harness" && HERDR_ATTENTION_CLI=/bin/true timeout 40 qs -p "$harness" --no-color 2>&1 || true)
echo "$log" | grep -E 'LOAD_FAIL|CREATE|RELATION_STYLE|SERVICE_VIEW|WORKSPACE_SECTIONS|THEME_|ACCOUNT_|ARRANGE_|QML_CHECK|ERROR|WARN qml|multiple times|TypeError|ReferenceError' \
  | grep -v 'Failed to register with host portal' || true

if echo "$log" | grep -qiE 'multiple times|Duplicate'; then
  echo "FAILED: duplicate assignment reported by the engine"; exit 1
fi
if echo "$log" | grep -F "$plugin" | grep -qE 'TypeError|ReferenceError|is not a type|is not defined|Cannot assign'; then
  echo "FAILED: runtime errors in plugin files"; exit 1
fi
echo "$log" | grep -q QML_CHECK_PASSED || { echo "FAILED"; exit 1; }
echo "qml check passed"
