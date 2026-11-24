import QtQuick
import QtQuick.Shapes
import "Contract.js" as Contract
import "Tokens.js" as Tokens

// The plugin's one account-icon renderer (v0.9.7), the same rule as accountIcon() in app.js: the
// account's mark (a path from ui_contract.json: our stroke shapes or Omarchy's filled agent marks) in its colour on a calm tile, with the
// optional badge (P, W, ...). Colours are theme tokens (--icon-<name>), so the icon follows the
// Omarchy theme. The panel reads the view over the CLI and does not fetch uploaded images, so for an
// account with its own image the mark stands in. No icon, or no mark: nothing is drawn (width 0),
// and the row shows its text as before. Never guessed: `icon` is only what the server sends.
Item {
  id: root

  property var icon: null
  property var tokens: Tokens.palette(null)
  property int size: 14

  readonly property var glyph: {
    var id = icon && icon.glyph
    var all = Contract.UI.account_icons ? Contract.UI.account_icons.glyphs : []
    for (var i = 0; id && i < all.length; i++) if (all[i].id === id) return all[i]
    return null
  }
  readonly property string colorName: icon && icon.color ? String(icon.color) : "neutral"
  readonly property color tone: {
    var key = "icon" + colorName.charAt(0).toUpperCase() + colorName.slice(1)
    return tokens[key] || tokens.iconNeutral || tokens.muted
  }
  readonly property string badge: icon && icon.badge ? String(icon.badge) : ""
  // What the badge shows and how (qml-check): always the characters as given, as plain text.
  readonly property string shownBadge: badgeText.text
  readonly property int badgeFormat: badgeText.textFormat
  readonly property real badgeTextWidth: badgeText.implicitWidth
  // v0.9.8.5: a mark has its own box (view_box, default 0 0 24 24) and is a stroke (our shapes) or a
  // fill (Omarchy's agent marks, third_party/omarchy-agents). Filled marks reach their box's edges,
  // so they get 68% of the tile (80% at 16 px and less) instead of 70%. Same rule as accountIcon() in app.js.
  readonly property bool filled: glyph !== null && glyph.paint === "fill"
  readonly property var box: {
    var parts = String(glyph && glyph.view_box ? glyph.view_box : "0 0 24 24").split(" ")
    return [Number(parts[0]), Number(parts[1]), Number(parts[2]), Number(parts[3])]
  }
  readonly property real markBox: size * (filled ? (size <= 16 ? 0.8 : 0.68) : 0.7)
  readonly property real markScale: markBox / Math.max(box[2], box[3])
  readonly property real markStroke: glyph && glyph.stroke_width
    ? Math.max(glyph.stroke_width, 2 * Math.max(box[2], box[3]) / 24) : 2.2

  visible: glyph !== null
  width: visible ? size : 0
  height: size
  implicitWidth: width
  implicitHeight: height

  Accessible.role: Accessible.Graphic
  Accessible.name: icon && icon.label ? String(icon.label) : ""

  // The calm tile: token colours only (no derived colours in the panel), layered like the web's
  // 14% tint on the surface with a 40% ring in the account's colour.
  Rectangle {
    anchors.fill: parent
    radius: root.size * 0.3
    color: root.tokens.surface || root.tokens.bg
  }
  Rectangle {
    anchors.fill: parent
    radius: root.size * 0.3
    color: root.tone
    opacity: 0.14
  }
  Rectangle {
    anchors.fill: parent
    radius: root.size * 0.3
    color: "transparent"
    border.width: 1
    border.color: root.tone
    opacity: 0.4
  }

  Item {
    width: root.markBox
    height: width
    anchors.centerIn: parent
    anchors.horizontalCenterOffset: root.badge !== "" ? -root.size * 0.05 : 0
    anchors.verticalCenterOffset: root.badge !== "" ? -root.size * 0.05 : 0

    Shape {
      // The path keeps its own coordinates; scaled into the tile and centred in it.
      x: (parent.width - root.box[2] * root.markScale) / 2 - root.box[0] * root.markScale
      y: (parent.height - root.box[3] * root.markScale) / 2 - root.box[1] * root.markScale
      width: root.box[2] * root.markScale
      height: root.box[3] * root.markScale
      preferredRendererType: Shape.CurveRenderer

      ShapePath {
        strokeColor: root.filled ? "transparent" : root.tone
        strokeWidth: root.filled ? 0 : root.markStroke * root.markScale
        fillColor: root.filled ? root.tone : "transparent"
        fillRule: root.glyph && root.glyph.fill_rule === "evenodd" ? ShapePath.OddEvenFill : ShapePath.WindingFill
        capStyle: ShapePath.RoundCap
        joinStyle: ShapePath.RoundJoin
        scale: Qt.size(root.markScale, root.markScale)
        PathSvg { path: root.glyph ? root.glyph.d : "" }
      }
    }
  }

  Rectangle {
    id: badgeBox
    visible: root.badge !== ""
    height: Math.max(10, root.size * 0.48)
    width: Math.max(height, badgeText.implicitWidth + 4)
    radius: height / 2
    x: root.size - width + root.size * 0.22
    y: root.size - height + root.size * 0.22
    color: root.tone
    border.width: 1.5
    border.color: root.tokens.surface || root.tokens.bg

    // Plain text, never rich or styled: a badge can be any 1-2 printable characters, '<' and '&' too.
    // The account label is only the accessible name above, never drawn as text.
    Text {
      id: badgeText
      anchors.centerIn: parent
      text: root.badge
      textFormat: Text.PlainText
      color: root.tokens.bg
      font.pixelSize: Math.max(8, Math.round(root.size * 0.32))
      font.bold: true
    }
  }
}
