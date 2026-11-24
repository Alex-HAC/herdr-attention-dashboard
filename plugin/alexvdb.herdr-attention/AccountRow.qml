import QtQuick
import qs.Commons
import qs.Ui
import "Contract.js" as Contract
import "Model.js" as Model
import "Tokens.js" as Tokens

// One quota account: name, flag, the chosen limit in % (centre), a meter, reset and agents.
// Server data (quotas.py: name, worst_pct, level, flag, reset_label, agents_label,
// checked). The order comes from Arrange (Model.shownAccounts); a nearly full or
// full account gets its flag here and never moves. Click opens the Quotas page.
CursorSurface {
  id: row

  property var account: ({})
  property var tokens: Tokens.palette(null)
  property string fontFamily: Style.font.family
  readonly property var labels: Contract.UI.panel.labels

  signal openRequested()

  readonly property bool hasLimits: (account.rings || []).length > 0
  readonly property color levelColor: tokens[Model.quotaTone(account.level)]
  readonly property color flagColor: account.flag ? tokens[Model.flagTone(account.flag)] : tokens.muted
  // v0.9.8.8: the number follows Quotas › Settings (fuller, weekly, 5-hour or both) and names its
  // limit (wk, 5h, mo); the meter shows the same number. Older servers: the fullest window.
  readonly property var centre: account.centre || null
  readonly property string centreText: centre ? centre.text + (centre.unit ? " " + centre.unit : "") : account.worst_pct + "%"
  readonly property real centrePct: centre ? centre.pct : (account.worst_pct || 0)
  // The fullest model-only limit (Fable Weekly) from the warning level: a line, not the number.
  readonly property string scopedLine: (account.scoped || [])
      .filter(function(w) { return w.pct >= ((account.levels || {}).warning || 70) })
      .sort(function(a, b) { return b.pct - a.pct })
      .map(function(w) { return w.label })[0] || ""
  readonly property string detail: [scopedLine, account.reset_label, account.agents_label, account.stale ? account.checked : ""]
                                     .filter(function(part) { return !!part }).join(" · ")

  foreground: tokens.text
  accent: tokens.accent
  width: parent ? parent.width : 0
  implicitHeight: body.implicitHeight + Tokens.space["2"] * 2

  Accessible.role: Accessible.Button
  Accessible.name: [account.name, hasLimits ? row.centreText + (centre && centre.title ? " " + centre.title : "") : labels.no_data, account.flag ? account.flag.label : "", detail]
                     .filter(function(part) { return !!part }).join(", ")
  Accessible.description: labels.account_tip

  MouseArea {
    anchors.fill: parent
    cursorShape: Qt.PointingHandCursor
    onClicked: row.openRequested()
  }

  Column {
    id: body
    x: Style.spacing.rowPaddingX
    y: Tokens.space["2"]
    width: parent.width - Style.spacing.rowPaddingX * 2
    spacing: Tokens.space["1"]

    Item {
      width: parent.width
      height: Math.max(name.implicitHeight, pct.implicitHeight)

      Text {
        id: name
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        width: Math.max(0, parent.width - pct.width - (flag.visible ? flag.width + Tokens.space["2"] : 0) - Tokens.space["2"])
        text: row.account.name || row.account.label || row.account.id || ""
        textFormat: Text.PlainText
        elide: Text.ElideRight
        color: row.foreground
        font.family: row.fontFamily
        font.pixelSize: Tokens.type.body
      }

      Text {
        id: flag
        visible: !!row.account.flag
        anchors.right: pct.left
        anchors.rightMargin: Tokens.space["2"]
        anchors.verticalCenter: parent.verticalCenter
        text: row.account.flag ? row.account.flag.label : ""
        textFormat: Text.PlainText
        color: row.flagColor
        font.family: row.fontFamily
        font.pixelSize: Tokens.type.small
        font.bold: true
      }

      Text {
        id: pct
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        text: row.hasLimits ? row.centreText : row.labels.no_data
        textFormat: Text.PlainText
        color: row.hasLimits ? row.levelColor : row.tokens.muted
        font.family: row.fontFamily
        font.pixelSize: Tokens.type.body
        font.bold: true
      }
    }

    Rectangle {
      id: meter
      visible: row.hasLimits
      width: parent.width
      height: Style.space(4)
      radius: height / 2
      color: row.tokens.pressed
      opacity: row.account.stale ? 0.5 : 1

      Rectangle {
        width: parent.width * Math.max(0, Math.min(100, row.centrePct)) / 100
        height: parent.height
        radius: parent.radius
        color: row.levelColor
      }
    }

    Text {
      visible: text !== ""
      width: parent.width
      text: row.detail
      textFormat: Text.PlainText
      elide: Text.ElideRight
      color: row.tokens.muted
      font.family: row.fontFamily
      font.pixelSize: Tokens.type.small
    }
  }
}
