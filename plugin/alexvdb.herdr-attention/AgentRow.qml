import QtQuick
import qs.Commons
import qs.Ui
import "Model.js" as Model
import "Tokens.js" as Tokens

// One agent. Collapsed: dot, name, what it means, how long, then its tab (or
// workspace) and task title. Expanded: facts, evidence, timeline, recent
// output on request, actions. Read-only towards the agent.
CursorSurface {
  id: row

  property var agent: ({})
  property var output: null
  property var detail: null
  property bool expanded: false
  property bool canAck: true
  property bool inWorkspace: false
  property bool herdrConnected: true
  property var view: null
  // Colour tokens: the server's live Omarchy theme (Herdr's status colours), Tokens.js as fallback.
  readonly property var tokens: Tokens.palette(view)
  property real nowSec: Date.now() / 1000
  property string fontFamily: Style.font.family
  property color urgentColor: tokens.needs
  property color warnColor: tokens.caution
  property color goodColor: tokens.done
  foreground: tokens.text
  accent: tokens.accent

  signal toggleRequested()
  signal ackRequested()
  signal outputRequested()
  signal terminalRequested()
  signal notificationRequested()
  property bool canOpenNotification: false

  readonly property color dim: tokens.muted
  readonly property string tone: agent.kind === "stall" ? "warn" : Model.stateTone(agent.state || "")
  // State colours are Herdr's status colours from the live theme tokens.
  readonly property color toneColor: Tokens.stateColor(tokens, agent.kind === "stall" ? "caution" : (agent.state || ""))
  // Inside a workspace the section already names the state; say only what adds to it.
  readonly property string headline: {
    if (agent.quota_wall) return agent.quota_wall.provider + " quota-walled"
    if (agent.launch && agent.launch.sandbox) return "Sandbox: " + agent.launch.sandbox
    var ai = Model.summaryHeadline(agent)
    if (ai !== "" && Model.isAlert(agent.kind) && !agent.acked) return ai
    if (Model.needsYou(agent.kind) || agent.kind === "stall") return agent.label + (agent.acked ? " (read)" : "")
    if (inWorkspace) return Model.lastActivity(agent, nowSec)
    return agent.label || ""
  }
  readonly property string context: (inWorkspace ? (agent.tab_label || "tab unknown") : agent.workspace_label)
                                    + " · " + (agent.title || "no task title")

  width: parent ? parent.width : 0
  implicitHeight: content.implicitHeight + Tokens.space["3"] * 2

  MouseArea {
    anchors.fill: parent
    cursorShape: Qt.PointingHandCursor
    onClicked: row.toggleRequested()
  }

  Column {
    id: content
    x: Style.spacing.rowPaddingX
    y: Tokens.space["3"]
    width: parent.width - Style.spacing.rowPaddingX * 2
    spacing: Tokens.space["1"]

    Row {
      width: parent.width
      spacing: Tokens.space["3"]

      Rectangle {
        width: Style.space(8)
        height: width
        radius: width / 2
        anchors.verticalCenter: parent.verticalCenter
        color: row.toneColor
        opacity: row.agent.acked ? 0.4 : 1.0
      }

      // v0.9.7: the subscription's icon when the account is recorded; nothing (width 0) otherwise.
      AccountIcon {
        id: accountMark
        anchors.verticalCenter: parent.verticalCenter
        icon: row.agent.icon || null
        tokens: row.tokens
        size: 14
      }

      Text {
        width: parent.width * 0.32 - (accountMark.visible ? accountMark.width + Tokens.space["3"] : 0)
               - (modelMark.visible ? modelMark.width + Tokens.space["3"] : 0)
        text: Model.displayName(row.agent)
        textFormat: Text.PlainText
        elide: Text.ElideRight
        color: row.foreground
        font.family: row.fontFamily
        font.pixelSize: Tokens.type.body
        font.bold: Model.needsYou(row.agent.kind)
      }

      // v0.9.8.6: the same text as the web chip for every service, "Opus 5.5 · high" (badge.label),
      // else the service by name when the model is not recorded; never CC / CX.
      Text {
        id: modelMark
        readonly property var info: row.agent.runtime && row.agent.runtime.badge ? row.agent.runtime.badge : null
        readonly property string badge: info && (info.label || info.text) ? String(info.label || info.text) : ""
        visible: badge !== ""
        width: visible ? Math.min(implicitWidth, Style.space(64)) : 0
        anchors.verticalCenter: parent.verticalCenter
        text: badge
        textFormat: Text.PlainText
        elide: Text.ElideRight
        color: row.dim
        font.family: row.fontFamily
        font.pixelSize: Tokens.type.small
      }

      Text {
        width: parent.width * 0.44
        text: row.headline
        textFormat: Text.PlainText
        elide: Text.ElideRight
        color: Model.isAlert(row.agent.kind) && !row.agent.acked ? row.toneColor : row.dim
        font.family: row.fontFamily
        font.pixelSize: Tokens.type.small
      }

      Text {
        width: parent.width * 0.24 - Style.space(8) - Tokens.space["3"] * 3
        horizontalAlignment: Text.AlignRight
        text: Model.since(row.agent, row.nowSec)
        textFormat: Text.PlainText
        color: row.dim
        font.family: row.fontFamily
        font.pixelSize: Tokens.type.small
      }
    }

    Text {
      width: parent.width
      leftPadding: Style.space(8) + Tokens.space["3"] + (accountMark.visible ? accountMark.width + Tokens.space["3"] : 0)
      text: row.context
      textFormat: Text.PlainText
      elide: Text.ElideRight
      color: row.dim
      font.family: row.fontFamily
      font.pixelSize: Tokens.type.small
    }

    Column {
      visible: row.expanded
      width: parent.width
      spacing: Tokens.space["1"]
      topPadding: Tokens.space["2"]

      Repeater {
        model: row.expanded ? Model.summaryLines(row.agent)
                              .concat(Model.factLines(row.agent, row.nowSec, row.herdrConnected))
                              .concat(Model.evidenceLines(row.agent)) : []
        delegate: Text {
          required property string modelData
          width: parent ? parent.width : 0
          leftPadding: Style.space(8) + Tokens.space["3"]
          text: modelData
          textFormat: Text.PlainText
          wrapMode: Text.WordWrap
          color: row.dim
          font.family: row.fontFamily
          font.pixelSize: Tokens.type.small
        }
      }

      Repeater {
        model: row.expanded ? Model.connectionRows(row.agent, row.view) : []
        delegate: RelationshipLine {
          required property var modelData
          x: Style.space(8) + Tokens.space["3"]
          width: parent ? Math.max(0, parent.width - x) : 0
          connection: modelData
          foreground: row.dim
        }
      }

      Text {
        visible: Model.connectionRows(row.agent, row.view).length > 0
        leftPadding: Style.space(8) + Tokens.space["3"]
        text: "Lines: solid = declared / trace · dashed, fainter = inferred"
        textFormat: Text.PlainText
        color: row.dim
        font.family: row.fontFamily
        font.pixelSize: Tokens.type.small
        width: parent.width
        wrapMode: Text.WordWrap
      }

      Text {
        visible: row.expanded
        leftPadding: Style.space(8) + Tokens.space["3"]
        topPadding: Tokens.space["2"]
        text: "Timeline"
        textFormat: Text.PlainText
        color: row.foreground
        font.family: row.fontFamily
        font.pixelSize: Tokens.type.small
        font.bold: true
      }

      Repeater {
        model: row.expanded ? (row.detail ? Model.timelineLines(row.detail, row.nowSec, 8) : ["Loading…"]) : []
        delegate: Text {
          required property string modelData
          width: parent ? parent.width : 0
          leftPadding: Style.space(8) + Tokens.space["3"]
          text: modelData
          textFormat: Text.PlainText
          wrapMode: Text.WordWrap
          color: row.dim
          font.family: row.fontFamily
          font.pixelSize: Tokens.type.small
        }
      }

      Row {
        spacing: Tokens.space["4"]
        leftPadding: Style.space(8) + Tokens.space["3"]
        topPadding: Tokens.space["1"]

        TokenButton {
          tokens: row.tokens
          visible: row.canAck && !!row.agent.event_id && !row.agent.acked
          text: "Mark read"
          onClicked: row.ackRequested()
        }
        TokenButton {
          tokens: row.tokens
          visible: row.canOpenNotification && !!row.agent.event_id
          text: "Open notification"
          tooltipText: "Opens this notification in the browser"
          onClicked: row.notificationRequested()
        }
        TokenButton {
          tokens: row.tokens
          visible: row.agent.present
          text: row.output && !row.output.loading ? "Refresh output" : "Show output"
          onClicked: row.outputRequested()
        }
        // v0.9.8: the live, coloured terminal view in the overview window (read-only).
        TokenButton {
          tokens: row.tokens
          visible: row.agent.present && !!row.agent.pane_id
          text: "Live terminal"
          tooltipText: "Opens a live, read-only view of this agent's terminal"
          onClicked: row.terminalRequested()
        }
      }

      Rectangle {
        visible: !!row.output
        width: parent.width
        height: visible ? outputText.implicitHeight + Tokens.space["3"] * 2 : 0
        color: row.tokens.raised
        radius: Tokens.radius.sm

        Text {
          id: outputText
          x: Tokens.space["3"]
          y: Tokens.space["3"]
          width: parent.width - Tokens.space["3"] * 2
          text: !row.output ? "" : row.output.loading ? "Reading…" : row.output.error ? row.output.error
                : (row.output.text || "(no output)") + (row.output.redactions ? "\n[" + row.output.redactions + " value(s) redacted]" : "")
          textFormat: Text.PlainText
          wrapMode: Text.WrapAnywhere
          color: row.foreground
          font.family: "monospace"
          font.pixelSize: Tokens.type.small
        }
      }
    }
  }
}
