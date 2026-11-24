import QtQuick
import qs.Commons
import "Tokens.js" as Tokens

// A real line swatch, including on the desktop's text-oriented relationship list.
Row {
  id: line
  property var connection: ({})
  property color foreground: Tokens.fallback.muted   // the row passes its muted token
  readonly property bool dashed: !!connection.dashed
  readonly property int segmentCount: dashed ? 4 : 1
  spacing: Tokens.space["2"]
  height: label.implicitHeight
  opacity: dashed ? 0.65 : 1

  Row {
    width: 32
    height: label.font.pixelSize
    spacing: line.dashed ? 3 : 0
    Repeater {
      model: line.segmentCount
      delegate: Rectangle {
        width: line.dashed ? 5 : 32
        height: 2
        y: label.font.pixelSize / 2
        color: line.foreground
      }
    }
  }
  Text {
    id: label
    width: Math.max(0, line.width - 32 - line.spacing)
    text: line.connection.text || ""
    textFormat: Text.PlainText
    wrapMode: Text.Wrap
    color: line.foreground
    font.family: Style.font.family
    font.pixelSize: Tokens.type.small
  }
}
