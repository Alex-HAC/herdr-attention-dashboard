import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland

// One per shell (bars exist per monitor, this does not).
//
// * Keeps a `herdr-attention watch` process open and exposes the latest
//   dashboard data as `view`.
// * Reports keyboard/mouse idle to the collector for phone alert gating.
//   Only "idle"/"active" leaves this file: no keystrokes, no window info.
// * Runs the panel's actions (mark read, settings, output) through the CLI.
Item {
  id: root

  // Injected by the shell.
  property var shell: null
  property var manifest: null

  // HERDR_ATTENTION_CLI lets the QML load test point at a harmless stub.
  readonly property string cli: Quickshell.env("HERDR_ATTENTION_CLI") || (Quickshell.env("HOME") + "/.local/bin/herdr-attention")

  property var view: null
  property bool online: false
  property string offlineReason: "starting"
  property real lastUpdate: 0
  property var outputs: ({})       // identity -> { text, loading, error, read_at }
  property string actionError: ""

  // Presence probe: idle after this many seconds without input. The
  // collector adds it back, so its phone delay stays exact.
  readonly property int probeSeconds: 30
  property real idleSinceMs: 0

  function handleLine(line) {
    var text = String(line || "").trim()
    if (text === "") return
    var data
    try { data = JSON.parse(text) } catch (e) { return }
    if (data.type === "ping") {
      root.online = true
      return
    }
    if (data.type === "offline") {
      root.online = false
      root.offlineReason = String(data.error || "collector not running")
      return
    }
    root.view = data
    root.online = true
    root.offlineReason = ""
    root.lastUpdate = Date.now()
  }

  function reportPresence() {
    var idle = idleMonitor.isIdle
    var seconds = idle && root.idleSinceMs > 0 ? Math.round((Date.now() - root.idleSinceMs) / 1000) : 0
    Quickshell.execDetached([root.cli, "presence", idle ? "idle" : "active", "--idle-seconds", String(seconds)])
  }

  function runAction(args) {
    if (actionProc.running) {
      actionQueue.push(args)
      return
    }
    root.actionError = ""
    actionProc.command = [root.cli].concat(args)
    actionProc.running = true
  }
  property var actionQueue: []

  function openNotifications(eventId) {
    var wrapper = Quickshell.env("HOME") + "/.local/bin/herdr-attention-overview"
    var args = eventId === undefined || eventId === null
      ? ["--notifications"] : ["--notification", String(eventId)]
    Quickshell.execDetached([wrapper].concat(args))
  }

  // Opens a named overview view (quotas, settings) through the same wrapper.
  function openOverview(route) {
    Quickshell.execDetached([Quickshell.env("HOME") + "/.local/bin/herdr-attention-overview", "--route", String(route)])
  }

  // v0.9.8: the overview window on an agent's live terminal (read-only).
  function openTerminal(identity) {
    if (!identity) return
    Quickshell.execDetached([Quickshell.env("HOME") + "/.local/bin/herdr-attention-overview", "--terminal", String(identity)])
  }

  function ack(eventId) { if (eventId) runAction(["ack", String(eventId)]) }

  function setSetting(key, value) { runAction(["settings", "set", key, JSON.stringify(value)]) }

  property string pendingOutput: ""

  function fetchOutput(identity) {
    var next = Object.assign({}, root.outputs)
    next[identity] = { text: "", loading: true, error: "" }
    root.outputs = next
    if (outputProc.running) {
      root.pendingOutput = identity
      return
    }
    outputProc.identity = identity
    outputProc.command = [root.cli, "output", identity]
    outputProc.running = true
  }

  function clearOutput(identity) {
    var next = Object.assign({}, root.outputs)
    delete next[identity]
    root.outputs = next
  }

  Process {
    id: feed
    command: [root.cli, "watch"]
    running: true
    stdout: SplitParser {
      onRead: function(line) { root.handleLine(line) }
    }
    onExited: function(exitCode) {
      root.online = false
      root.offlineReason = exitCode === 127 ? "herdr-attention is not installed" : "watch stopped"
      feedRestart.restart()
    }
  }

  Timer {
    id: feedRestart
    interval: 5000
    onTriggered: feed.running = true
  }

  IdleMonitor {
    id: idleMonitor
    enabled: true
    timeout: root.probeSeconds
    // Count real input only; a playing video does not mean you are here.
    respectInhibitors: false
    onIsIdleChanged: {
      root.idleSinceMs = isIdle ? Date.now() - root.probeSeconds * 1000 : 0
      root.reportPresence()
    }
  }

  // Heartbeat, so the collector can tell "idle" from "plugin not running".
  Timer {
    interval: 60000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: { if (root) root.reportPresence() }
  }

  Process {
    id: actionProc
    stderr: StdioCollector {
      id: actionErr
      waitForEnd: true
    }
    onExited: function(exitCode) {
      if (exitCode !== 0) root.actionError = String(actionErr.text || "action failed").trim()
      if (root.actionQueue.length > 0) {
        var next = root.actionQueue.shift()
        Qt.callLater(function() { root.runAction(next) })
      }
    }
  }

  Process {
    id: outputProc
    property string identity: ""
    stdout: StdioCollector {
      id: outputOut
      waitForEnd: true
    }
    onExited: function(exitCode) {
      var next = Object.assign({}, root.outputs)
      var entry = { text: "", loading: false, error: "" }
      if (exitCode === 0) {
        try {
          var data = JSON.parse(outputOut.text)
          entry.text = String(data.text || data.note || "")
          entry.redactions = data.redactions || 0
          entry.read_at = data.read_at || 0
        } catch (e) {
          entry.error = "could not read output"
        }
      } else {
        entry.error = "could not read output"
      }
      next[outputProc.identity] = entry
      root.outputs = next
      if (root.pendingOutput !== "") {
        var queued = root.pendingOutput
        root.pendingOutput = ""
        Qt.callLater(function() { root.fetchOutput(queued) })
      }
    }
  }
}
