import QtQuick
import qs.Commons
import qs.Ui
import "Model.js" as Model
import "Tokens.js" as Tokens

// One watched workspace: name, how many need a look, a bar of agent states,
// last activity. Click to open: its agents by state and how its tabs are
// arranged. `panel` is the owning Panel (colours, service, expanded rows).
CursorSurface {
  id: card

  property var panel: null
  property var workspace: ({})
  property bool open: false

  signal toggleRequested()

  // Colour tokens: the panel's live theme (view.theme.tokens), else the Tokens.js fallback.
  readonly property var tokens: panel && panel.tokens ? panel.tokens : Tokens.palette(panel ? panel.view : null)
  readonly property color dim: tokens.muted
  foreground: tokens.text
  accent: tokens.accent
  readonly property var segments: Model.barSegments(workspace.counts)
  readonly property var sections: open && panel ? Model.workspaceSections(panel.view, workspace.id) : []

  function stateColor(state) { return Tokens.stateColor(tokens, state) }

  width: parent ? parent.width : 0
  implicitHeight: body.implicitHeight + Tokens.space["3"] * 2
  bordered: true

  Column {
    id: body
    x: Style.spacing.rowPaddingX
    y: Tokens.space["3"]
    width: parent.width - Style.spacing.rowPaddingX * 2
    spacing: Tokens.space["2"]

    Item {
      width: parent.width
      height: header.implicitHeight

      MouseArea {
        anchors.fill: parent
        cursorShape: Qt.PointingHandCursor
        onClicked: card.toggleRequested()
      }

      Column {
        id: header
        width: parent.width - badge.width - Tokens.space["3"]
        spacing: Tokens.space["1"]

        Text {
          width: parent.width
          text: (card.open ? "▾ " : "▸ ") + card.workspace.label
          textFormat: Text.PlainText
          elide: Text.ElideRight
          color: card.foreground
          font.family: card.panel ? card.panel.fontFamily : Style.font.family
          font.pixelSize: Tokens.type.title
          font.bold: true
        }

        Text {
          width: parent.width
          text: Model.countsText(card.workspace.counts)
          textFormat: Text.PlainText
          elide: Text.ElideRight
          color: card.dim
          font.family: card.panel ? card.panel.fontFamily : Style.font.family
          font.pixelSize: Tokens.type.small
        }

        Row {
          id: stateBar
          width: parent.width
          height: Style.space(5)
          spacing: Style.space(2)
          visible: card.segments.length > 0

          Repeater {
            model: card.segments
            delegate: Rectangle {
              required property var modelData
              width: Math.max(Style.space(4), (stateBar.width - stateBar.spacing * (card.segments.length - 1)) * modelData.fraction)
              height: stateBar.height
              radius: height / 2
              color: card.stateColor(modelData.state)
            }
          }
        }

        Text {
          width: parent.width
          text: Model.workspaceFoot(card.workspace, card.panel ? card.panel.nowSec : Date.now() / 1000)
          textFormat: Text.PlainText
          elide: Text.ElideRight
          color: card.dim
          font.family: card.panel ? card.panel.fontFamily : Style.font.family
          font.pixelSize: Tokens.type.small
        }
      }

      Rectangle {
        id: badge
        visible: card.workspace.attention > 0
        anchors.right: parent.right
        anchors.top: parent.top
        width: visible ? Math.max(height, badgeLabel.implicitWidth + Style.space(10)) : 0
        height: Style.space(18)
        radius: height / 2
        readonly property bool urgent: !!(card.workspace.counts && card.workspace.counts.waiting_on_you)
        color: urgent ? card.tokens.needs : card.foreground

        Text {
          id: badgeLabel
          anchors.centerIn: parent
          text: String((card.workspace.counts || {}).waiting_on_you || card.workspace.attention || 0)
          color: badge.urgent ? card.tokens.needsInk : card.tokens.bg
          font.pixelSize: Tokens.type.small
          font.bold: true
        }
      }
    }

    Text {
      visible: (card.workspace.layout_hints || []).length > 0
      width: body.width
      text: card.open ? Model.layoutText(card.workspace) : "Layout: " + (card.workspace.layout_hints || []).length + " quiet suggestions"
      textFormat: Text.PlainText
      wrapMode: Text.WordWrap
      color: card.dim
      font.family: card.panel ? card.panel.fontFamily : Style.font.family
      font.pixelSize: Tokens.type.small
    }

    // ------------------------------------------------------------ opened
    Repeater {
      model: card.sections
      delegate: Column {
        id: group
        required property var modelData
        width: body.width
        spacing: Tokens.space["1"]
        topPadding: Tokens.space["2"]

        PanelSectionHeader {
          color: card.dim
          text: group.modelData.title
          foreground: card.foreground
          fontFamily: card.panel ? card.panel.fontFamily : Style.font.family
        }

        Repeater {
          model: group.modelData.items
          delegate: AgentRow {
            required property var modelData
            agent: modelData
            width: group.width
            inWorkspace: true
            expanded: !!card.panel && card.panel.expandedIdentity === modelData.identity
            output: card.panel && card.panel.service && card.panel.service.outputs ? (card.panel.service.outputs[modelData.identity] || null) : null
            detail: card.panel ? (card.panel.details[modelData.identity] || null) : null
            canAck: card.panel && card.panel.view && card.panel.view.features ? card.panel.view.features.acknowledge : true
            herdrConnected: card.panel ? card.panel.herdrConnected : true
            view: card.panel ? card.panel.view : null
            nowSec: card.panel ? card.panel.nowSec : Date.now() / 1000
            foreground: card.foreground
            accent: card.accent
            fontFamily: card.panel ? card.panel.fontFamily : Style.font.family
            urgentColor: card.tokens.needs
            warnColor: card.tokens.caution
            goodColor: card.tokens.done
            onToggleRequested: if (card.panel) card.panel.toggleExpanded(modelData)
            onAckRequested: {
              if (card.panel && card.panel.service) card.panel.service.ack(modelData.event_id)
              if (card.panel) card.panel.expandedIdentity = ""   // close the row, back to the list
            }
            onOutputRequested: if (card.panel && card.panel.service) card.panel.service.fetchOutput(modelData.identity)
            onTerminalRequested: if (card.panel && card.panel.service) card.panel.service.openTerminal(modelData.identity)
          }
        }
      }
    }

    Text {
      visible: card.open && card.workspace.agent_count === 0
      text: "No agents running here right now."
      textFormat: Text.PlainText
      color: card.dim
      font.family: card.panel ? card.panel.fontFamily : Style.font.family
      font.pixelSize: Tokens.type.small
    }

    PanelSectionHeader {
      color: card.dim
      visible: card.open && card.workspace.tabs && card.workspace.tabs.length > 0
      topPadding: Tokens.space["2"]
      text: "Arranged by tab"
      foreground: card.foreground
      fontFamily: card.panel ? card.panel.fontFamily : Style.font.family
    }

    Text {
      visible: card.open && card.workspace.tabs && card.workspace.tabs.length > 0
      width: body.width
      text: card.open && card.panel ? Model.tabsText(card.workspace, card.panel.view)
            + "\nHerdr does not report who leads or assigns work; relationships are unknown." : ""
      textFormat: Text.PlainText
      wrapMode: Text.WordWrap
      color: card.dim
      font.family: card.panel ? card.panel.fontFamily : Style.font.family
      font.pixelSize: Tokens.type.small
    }
  }
}
