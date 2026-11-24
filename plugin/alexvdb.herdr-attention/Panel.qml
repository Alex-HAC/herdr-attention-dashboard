import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Contract.js" as Contract
import "Model.js" as Model
import "Tokens.js" as Tokens

// Bar icon with a count of things that need a look, and a read-only panel
// organised by workspace: what needs you, then each watched workspace with
// its agents by state, then settings (including which workspaces to watch).
// Nothing here can type into or steer an agent.
Panel {
  id: root
  moduleName: "alexvdb.herdr-attention"
  ipcTarget: "alexvdb.herdr-attention"

  property var service: null
  property string expandedIdentity: ""
  property var openWorkspaces: ({})
  property var details: ({})
  property int cursorIndex: -1
  property bool settingsOpen: false
  property bool emptyWorkspacesOpen: false
  property real nowSec: Date.now() / 1000

  readonly property var view: service ? service.view : null
  readonly property bool online: service ? service.online : false
  readonly property bool herdrConnected: !!view && !!view.collector && view.collector.herdr.connected
  readonly property var attention: Model.attentionItems(view)
  readonly property var workspaces: view && view.workspaces ? view.workspaces : []
  readonly property var activeWorkspaces: Model.activeWorkspaces(view)
  readonly property var emptyWorkspaces: Model.emptyWorkspaces(view)
  readonly property bool nothingWatched: online && !!view && workspaces.length === 0
  readonly property int badge: Model.badgeCount(view)
  readonly property bool urgentBadge: Model.urgentCount(view) > 0
  // Labels: ui_contract.json, generated into Contract.js (scripts/gen_ui_contract.py).
  readonly property var labels: Contract.UI.panel.labels

  // Arrange: the order and hidden lists Alex saved in the dashboard (view.layout).
  readonly property var sections: Contract.UI.arrange.sections
  readonly property var accounts: Model.shownAccounts(view, sections)
  readonly property var needsUpdates: Model.unreadUpdates(view, true).slice(0, 6)
  readonly property var otherUpdates: Model.unreadUpdates(view, false)
  readonly property var blockPresent: ({ needs_action: attention.length > 0 || needsUpdates.length > 0,
                                         quotas: online, other_updates: otherUpdates.length > 0 })
  // A string, so the block list changes only when the saved order does.
  readonly property string blockKey: Model.panelBlocks(view, sections).join(",")
  readonly property var hiddenBlocks: Model.hiddenBlocks(view, sections, blockPresent)
  readonly property string hiddenText: hiddenBlocks.length === 0 ? "" : Contract.UI.arrange.labels.hidden_blocks + " "
    + hiddenBlocks.map(function(id) {
        return Model.sectionLabel(sections, id) + (id === "other_updates" ? " (" + otherUpdates.length + ")" : "")
      }).join(", ")
  readonly property string cli: service && service.cli ? service.cli : Quickshell.env("HOME") + "/.local/bin/herdr-attention"

  // Every colour is a token of the live Omarchy theme from the server (view.theme.tokens,
  // with Herdr's status colours); Tokens.js holds the Tokyo Night fallback for when it is offline.
  readonly property var tokens: Tokens.palette(view)
  readonly property color foreground: tokens.text
  readonly property color strong: tokens.textStrong
  readonly property color urgent: tokens.needs          // waiting on you, and nothing else
  readonly property color accent: tokens.accent
  readonly property color dim: tokens.muted
  readonly property color warnColor: tokens.caution
  readonly property color goodColor: tokens.done
  function stateColor(state) { return Tokens.stateColor(tokens, state) }
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  function bindService() {
    if (service) return
    var host = bar && bar.shell ? bar.shell : null
    if (host && typeof host.serviceFor === "function") service = host.serviceFor(root.moduleName)
  }

  // Tier and preset wording comes from the server (view.notify_tiers, notify_tiers.py).
  readonly property var notifyTiers: view && view.notify_tiers ? view.notify_tiers : null
  readonly property var presetOptions: notifyTiers && notifyTiers.presets
    ? notifyTiers.presets.filter(function(p) { return p.id !== "custom" || notifyTiers.preset === "custom" })
        .map(function(p) { return { value: p.id, label: p.label } })
    : []
  readonly property string presetHint: notifyTiers && notifyTiers.preset_hint ? notifyTiers.preset_hint : ""
  readonly property var tierRows: notifyTiers && notifyTiers.tiers
    ? notifyTiers.tiers.map(function(t) {
        return { key: "notify_tiers." + t.id, label: t.label, hint: t.hint, fallback: !!t.enabled, locked: !!t.locked }
      })
    : []

  function settingValue(key, fallback) {
    var s = view && view.settings ? view.settings[key] : undefined
    return s === undefined || s === null ? fallback : s
  }

  function persistFold(key, value) {
    var entry = { id: root.moduleName }
    for (var existing in root.settings) if (existing !== "id") entry[existing] = root.settings[existing]
    entry[key] = value
    root.settings = entry
    if (root.bar && root.bar.shell && typeof root.bar.shell.updateEntryInline === "function")
      root.bar.shell.updateEntryInline(root.moduleName, entry)
  }

  function toggleWorkspace(id) {
    var next = Object.assign({}, openWorkspaces)
    if (next[id]) delete next[id]
    else next[id] = true
    openWorkspaces = next
  }

  function toggleExpanded(agent) {
    expandedIdentity = expandedIdentity === agent.identity ? "" : agent.identity
    if (expandedIdentity !== "") fetchDetail(expandedIdentity)
  }

  // Timeline and events for the open agent row (read-only CLI call).
  function fetchDetail(identity) {
    if (detailProc.running) return
    detailProc.identity = identity
    detailProc.command = [root.cli, "agent", identity]
    detailProc.running = true
  }

  function selectedAgent() {
    return cursorIndex >= 0 && cursorIndex < attention.length ? attention[cursorIndex] : null
  }

  function moveCursor(dy) {
    if (attention.length === 0) return
    cursorIndex = Math.max(0, Math.min(attention.length - 1, cursorIndex + dy))
  }

  Component.onCompleted: {
    bindService()
    settingsOpen = setting("showSettings", false) === true
  }

  onOpenedChanged: if (opened) {
    nowSec = Date.now() / 1000
    cursorIndex = -1
    if (panelFlick) panelFlick.contentY = 0
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  Timer {
    interval: 2000
    running: root.service === null
    repeat: true
    onTriggered: root.bindService()
  }

  Timer {
    interval: 15000
    running: root.opened
    repeat: true
    onTriggered: {
      root.nowSec = Date.now() / 1000
      if (root.expandedIdentity !== "") root.fetchDetail(root.expandedIdentity)
    }
  }

  Process {
    id: detailProc
    property string identity: ""
    stdout: StdioCollector {
      id: detailOut
      waitForEnd: true
    }
    onExited: function(exitCode) {
      if (exitCode !== 0) return
      try {
        var next = Object.assign({}, root.details)
        next[detailProc.identity] = JSON.parse(detailOut.text)
        root.details = next
      } catch (e) {}
    }
  }

  // ------------------------------------------------- the Arrange blocks
  Component {
    id: needsBlock
    Column {
      width: parent ? parent.width : 0
      spacing: Tokens.space["2"]

      Repeater {
        model: root.needsUpdates
        delegate: UpdateRow {
          required property var modelData
          note: modelData
          tokens: root.tokens
          fontFamily: root.fontFamily
          onOpenRequested: if (root.service) root.service.openNotifications(modelData.id)
        }
      }

      Column {
        visible: root.attention.length > 0
        width: parent.width
        spacing: Tokens.space["1"]

        PanelSectionHeader {
          color: root.dim
          text: "To look at (" + root.attention.length + ")"
          foreground: root.foreground
          fontFamily: root.fontFamily
        }

        Repeater {
          model: root.attention
          delegate: AgentRow {
            required property var modelData
            required property int index
            agent: modelData
            width: column.width
            expanded: root.expandedIdentity === modelData.identity
            hasCursor: root.cursorIndex === index
            output: root.service && root.service.outputs ? (root.service.outputs[modelData.identity] || null) : null
            detail: root.details[modelData.identity] || null
            canAck: root.view && root.view.features ? root.view.features.acknowledge : true
            herdrConnected: root.herdrConnected
            view: root.view
            nowSec: root.nowSec
            foreground: root.foreground
            accent: root.accent
            fontFamily: root.fontFamily
            urgentColor: root.urgent
            warnColor: root.warnColor
            goodColor: root.goodColor
            onToggleRequested: root.toggleExpanded(modelData)
            onAckRequested: {
              // Mark read closes the row and returns to the list.
              if (root.service) root.service.ack(modelData.event_id)
              root.expandedIdentity = ""
            }
            onOutputRequested: if (root.service) root.service.fetchOutput(modelData.identity)
            onTerminalRequested: if (root.service) root.service.openTerminal(modelData.identity)
            canOpenNotification: Model.notificationLink(root.view) !== ""
            onNotificationRequested: if (root.service) root.service.openNotifications(modelData.event_id)
          }
        }
      }
    }
  }

  Component {
    id: quotasBlock
    Column {
      width: parent ? parent.width : 0
      spacing: Tokens.space["1"]

      TokenButton {
        tokens: root.tokens
        text: Model.sectionLabel(root.sections, "quotas") + " · "
              + (root.view && root.view.quotas && root.view.quotas.header ? root.view.quotas.header.label : root.labels.quotas_open)
        tooltipText: root.labels.quotas_tip
        onClicked: if (root.service) root.service.openOverview("quotas")
      }
      Repeater {
        model: root.accounts
        delegate: AccountRow {
          required property var modelData
          account: modelData
          tokens: root.tokens
          fontFamily: root.fontFamily
          onOpenRequested: if (root.service) root.service.openOverview("quotas")
        }
      }
    }
  }

  Component {
    id: otherBlock
    Column {
      width: parent ? parent.width : 0
      spacing: Tokens.space["2"]

      PanelSectionHeader {
        color: root.dim
        text: Model.sectionLabel(root.sections, "other_updates") + " (" + root.otherUpdates.length + ")"
        foreground: root.foreground
        fontFamily: root.fontFamily
      }
      Repeater {
        model: root.otherUpdates.slice(0, 6)
        delegate: UpdateRow {
          required property var modelData
          note: modelData
          tokens: root.tokens
          fontFamily: root.fontFamily
          onOpenRequested: if (root.service) root.service.openNotifications(modelData.id)
        }
      }
    }
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    tooltipText: root.online ? Model.heroMeta(root.view, true, "") : "Herdr attention: collector offline"
    iconComponent: Component {
      Item {
        Text {
          anchors.centerIn: parent
          text: "󰚩"
          color: !root.online ? root.dim : root.urgentBadge ? root.urgent : root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.bar.iconFont
        }
        Rectangle {
          visible: root.badge > 0
          anchors.right: parent.right
          anchors.top: parent.top
          width: Math.max(height, badgeText.implicitWidth + Style.space(4))
          height: Style.space(10)
          radius: height / 2
          color: root.urgentBadge ? root.urgent : root.accent

          Text {
            id: badgeText
            anchors.centerIn: parent
            text: root.badge > 9 ? "9+" : String(root.badge)
            color: root.urgentBadge ? root.tokens.needsInk : root.tokens.accentInk
            font.family: root.fontFamily
            font.pixelSize: Style.space(7)
            font.bold: true
          }
        }
      }
    }
    onPressed: function(buttonCode) { root.toggle() }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(460))
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(680))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onMoveRequested: function(dx, dy) { root.moveCursor(dy) }
      onActivateRequested: {
        var a = root.selectedAgent()
        if (a) root.toggleExpanded(a)
      }
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(t) {
        var a = root.selectedAgent()
        if (!a || !root.service) return
        if (t === "r" || t === "R") root.service.ack(a.event_id)
        else if (t === "o" || t === "O") { root.expandedIdentity = a.identity; root.service.fetchOutput(a.identity) }
      }

      Flickable {
        id: panelFlick
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
          id: column
          width: panelFlick.width
          spacing: Tokens.space["4"]

          // Hero: icon, title and one status line (the shell's PanelHero darkens its own
          // subtitle; this one takes the theme's muted token).
          Row {
            width: parent.width
            spacing: Style.space(14)
            Text {
              id: heroIcon
              anchors.verticalCenter: parent.verticalCenter
              text: "󰚩"
              color: root.urgentBadge ? root.urgent : root.strong
              font.family: root.fontFamily
              font.pixelSize: Style.font.display
            }
            Column {
              anchors.verticalCenter: parent.verticalCenter
              width: parent.width - heroIcon.width - parent.spacing
              spacing: Style.space(2)
              Text {
                width: parent.width
                text: "Herdr agents"
                textFormat: Text.PlainText
                elide: Text.ElideRight
                color: root.strong
                font.family: root.fontFamily
                font.pixelSize: Tokens.type.title
                font.bold: true
              }
              Text {
                width: parent.width
                text: (root.online && root.herdrConnected && root.workspaces.length > 0
                       ? Model.overviewLine(root.view)
                       : Model.heroMeta(root.view, root.online, root.service ? root.service.offlineReason : "plugin service not loaded")).toUpperCase()
                textFormat: Text.PlainText
                elide: Text.ElideRight
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Tokens.type.micro
                font.bold: true
                font.letterSpacing: 1.2
              }
            }
          }

          Text {
            width: parent.width
            text: root.online ? Model.phoneText(root.view) : "Start it with: systemctl --user start herdr-attention"
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Tokens.type.small
          }

          Text {
            visible: !!root.service && root.service.actionError !== ""
            width: parent.width
            text: root.service ? root.service.actionError : ""
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            color: root.urgent
            font.family: root.fontFamily
            font.pixelSize: Tokens.type.small
          }

          // ------------------------------------------------- notifications
          TokenButton {
            tokens: root.tokens
            visible: root.online && Model.notificationLink(root.view) !== ""
            text: Model.notificationsText(root.view)
            tooltipText: "Opens the Notifications view in Herdr overview"
            onClicked: if (root.service) root.service.openNotifications()
          }

          // ------------------------------------- blocks in Alex's Arrange order
          // Needs action, Quotas and Other updates follow view.layout (Arrange in the
          // dashboard). A block is rebuilt only when that order changes.
          Repeater {
            model: root.blockKey === "" ? [] : root.blockKey.split(",")
            delegate: Loader {
              required property string modelData
              width: column.width
              visible: !!root.blockPresent[modelData]
              sourceComponent: modelData === "needs_action" ? needsBlock : modelData === "quotas" ? quotasBlock : otherBlock
            }
          }

          // One muted line names hidden blocks that have something, so nothing is lost.
          Flow {
            visible: root.hiddenText !== ""
            width: parent.width
            spacing: Tokens.space["2"]
            Text {
              width: Math.min(implicitWidth, parent.width)
              height: arrangeButton.visible ? Math.max(implicitHeight, arrangeButton.height) : implicitHeight
              verticalAlignment: Text.AlignVCenter
              text: root.hiddenText
              textFormat: Text.PlainText
              wrapMode: Text.WordWrap
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Tokens.type.small
            }
            TokenButton {
              id: arrangeButton
              tokens: root.tokens
              visible: Model.notificationLink(root.view) !== ""
              text: root.labels.arrange
              tooltipText: root.labels.arrange_tip
              onClicked: if (root.service) root.service.openNotifications()
            }
          }

          // ---------------------------------------------------- workspaces
          Column {
            visible: root.workspaces.length > 0
            width: parent.width
            spacing: Tokens.space["3"]

            PanelSectionHeader {
              color: root.dim
              text: "Workspaces (" + root.workspaces.length + ")"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            Repeater {
              model: root.activeWorkspaces
              delegate: WorkspaceCard {
                required property var modelData
                workspace: modelData
                panel: root
                width: column.width
                open: !!root.openWorkspaces[modelData.id]
                foreground: root.foreground
                accent: root.accent
                onToggleRequested: root.toggleWorkspace(modelData.id)
              }
            }
          }

          Column {
            visible: root.emptyWorkspaces.length > 0
            width: parent.width
            spacing: Tokens.space["1"]
            TokenButton {
              tokens: root.tokens
              text: root.emptyWorkspaces.length + (root.emptyWorkspaces.length === 1 ? " workspace without agents" : " workspaces without agents") + (root.emptyWorkspacesOpen ? " ▴" : " ▾")
              onClicked: root.emptyWorkspacesOpen = !root.emptyWorkspacesOpen
            }
            Repeater {
              model: root.emptyWorkspacesOpen ? root.emptyWorkspaces : []
              delegate: Text {
                required property var modelData
                width: column.width
                text: modelData.label + " · 0 live agents"
                textFormat: Text.PlainText
                wrapMode: Text.WordWrap
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Tokens.type.small
              }
            }
          }

          Text {
            visible: root.nothingWatched
            width: parent.width
            text: "No workspaces watched yet. Switch some on below; nothing is watched until you do."
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Tokens.type.body
          }

          PanelSeparator { width: parent.width; foreground: root.tokens.line; strength: 1 }

          // ------------------------------------------------------ settings
          Row {
            spacing: Tokens.space["3"]
            PanelSectionHeader {
              color: root.dim
              text: "Workspaces and settings"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }
            Text {
              text: root.settingsOpen || root.nothingWatched ? "hide" : "show"
              visible: !root.nothingWatched
              color: root.accent
              font.family: root.fontFamily
              font.pixelSize: Tokens.type.small
              MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: { root.settingsOpen = !root.settingsOpen; root.persistFold("showSettings", root.settingsOpen) }
              }
            }
          }

          Column {
            visible: (root.settingsOpen || root.nothingWatched) && !!root.view
            width: parent.width
            spacing: Tokens.space["3"]

            PanelSectionHeader {
              color: root.dim
              text: "Watch (new workspaces start off)"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }
            Repeater {
              model: root.view && root.view.chooser ? root.view.chooser : []
              delegate: Column {
                required property var modelData
                width: parent ? parent.width : 0
                spacing: Tokens.space["1"]
                Toggle {
                  width: parent.width
                  label: modelData.label
                  description: modelData.watched ? "watched" : "not watched"
                  checked: modelData.watched
                  foreground: root.foreground
                  accent: root.accent
                  fontFamily: root.fontFamily
                  onClicked: if (root.service) root.service.setSetting("watch.enabled_workspaces",
                    Model.toggled(root.settingValue("watch.enabled_workspaces", []), modelData.id, !modelData.watched))
                }
                Toggle {
                  visible: modelData.watched
                  width: parent.width
                  label: "Mute phone alerts for " + modelData.label
                  description: "Still shown here, never sent to the phone"
                  checked: modelData.muted
                  foreground: root.foreground
                  accent: root.accent
                  fontFamily: root.fontFamily
                  onClicked: if (root.service) root.service.setSetting("watch.muted_workspaces",
                    Model.toggled(root.settingValue("watch.muted_workspaces", []), modelData.id, !modelData.muted))
                }
              }
            }

            PanelSectionHeader {
              color: root.dim
              text: "What to notify"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }
            Row {
              spacing: Tokens.space["3"]
              Text {
                width: Style.space(190)
                anchors.verticalCenter: parent.verticalCenter
                text: "Preset"
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Tokens.type.body
              }
              ButtonGroup {
                options: root.presetOptions
                value: root.settingValue("notify_tiers.preset", "normal")
                foreground: root.foreground
                background: root.tokens.bg
                accent: root.accent
                fontFamily: root.fontFamily
                focusable: false
                onChanged: function(value) { if (root.service && value !== "custom") root.service.setSetting("notify_tiers.preset", value) }
              }
            }
            Text {
              width: parent.width
              visible: root.presetHint !== ""
              wrapMode: Text.WordWrap
              text: root.presetHint
              textFormat: Text.PlainText
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Tokens.type.small
            }
            Text {
              width: parent.width
              visible: root.settingValue("notify_tiers.preset", "normal") === "custom"
              wrapMode: Text.WordWrap
              text: "Custom — a switch no longer matches a preset."
              textFormat: Text.PlainText
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Tokens.type.small
            }
            Repeater {
              model: root.tierRows
              delegate: Toggle {
                required property var modelData
                width: parent ? parent.width : 0
                label: modelData.label
                description: modelData.hint
                checked: modelData.locked === true || root.settingValue(modelData.key, modelData.fallback) === true
                enabled: modelData.locked !== true
                opacity: modelData.locked === true ? 0.55 : 1
                foreground: root.foreground
                accent: root.accent
                fontFamily: root.fontFamily
                onClicked: if (root.service && modelData.locked !== true) root.service.setSetting(modelData.key, !checked)
              }
            }
            Text {
              width: parent.width
              wrapMode: Text.WordWrap
              text: root.view && root.view.notify ? (root.view.notify.status_line || "Phone alert status unavailable") : "Phone alert status unavailable"
              textFormat: Text.PlainText
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Tokens.type.small
            }

            TokenButton {
              tokens: root.tokens
              visible: Model.notificationLink(root.view) !== ""
              text: "Open full settings ↗"
              tooltipText: "Opens all alert, AI, workspace and phone settings in the dashboard"
              onClicked: if (root.service) root.service.openOverview("settings")
            }

            Repeater {
              model: [
                { key: "stall.minutes", label: "Possible stall after", unit: "min", step: 5, low: 5, high: 240, fallback: 15 },
                { key: "ui.parked_after_minutes", label: "Parked after (idle)", unit: "min", step: 15, low: 15, high: 1440, fallback: 60 },
                { key: "presence.phone_delay_minutes", label: "Phone alerts after idle", unit: "min", step: 1, low: 0, high: 120, fallback: 5 }
              ]
              delegate: Row {
                required property var modelData
                spacing: Tokens.space["3"]
                readonly property real current: root.settingValue(modelData.key, modelData.fallback)
                Text {
                  width: Style.space(190)
                  anchors.verticalCenter: parent.verticalCenter
                  text: modelData.label
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Tokens.type.body
                }
                TokenButton {
                  tokens: root.tokens
                  text: "−"
                  onClicked: if (root.service) root.service.setSetting(modelData.key, Model.clampStep(current, -modelData.step, modelData.low, modelData.high))
                }
                Text {
                  anchors.verticalCenter: parent.verticalCenter
                  text: current + " " + modelData.unit
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Tokens.type.body
                }
                TokenButton {
                  tokens: root.tokens
                  text: "+"
                  onClicked: if (root.service) root.service.setSetting(modelData.key, Model.clampStep(current, modelData.step, modelData.low, modelData.high))
                }
              }
            }

            Row {
              spacing: Tokens.space["3"]
              Text {
                width: Style.space(190)
                anchors.verticalCenter: parent.verticalCenter
                text: "Phone"
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Tokens.type.body
              }
              ButtonGroup {
                options: [
                  { value: "auto", label: "When I'm away" },
                  { value: "always", label: "Always" },
                  { value: "never", label: "Paused" }
                ]
                value: root.settingValue("presence.mode", "auto")
                foreground: root.foreground
                background: root.tokens.bg
                accent: root.accent
                fontFamily: root.fontFamily
                focusable: false
                onChanged: function(value) { if (root.service) root.service.setSetting("presence.mode", value) }
              }
            }

            Text {
              width: parent.width
              wrapMode: Text.WordWrap
              text: "Idle means no keyboard or mouse input; it is a guess at whether you are away. Only idle/active is reported, never keys."
              textFormat: Text.PlainText
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Tokens.type.small
            }
          }
        }
      }
    }
  }
}
