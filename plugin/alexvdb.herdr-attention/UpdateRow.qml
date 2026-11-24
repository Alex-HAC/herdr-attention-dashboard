import QtQuick
import qs.Commons
import "Tokens.js" as Tokens

// One unread notification: headline, body, who and when, and a button to open it.
Column {
  id: row

  property var note: ({})
  property var tokens: Tokens.palette(null)
  property string fontFamily: Style.font.family

  signal openRequested()

  width: parent ? parent.width : 0
  spacing: Tokens.space["1"]

  Text {
    width: parent.width
    text: row.note.what || ""
    textFormat: Text.PlainText
    wrapMode: Text.WordWrap      // the headline stays readable in a narrow panel
    elide: Text.ElideRight
    maximumLineCount: 2
    color: row.tokens.text
    font.family: row.fontFamily
    font.pixelSize: Tokens.type.body
  }
  Text {
    visible: text !== ""
    width: parent.width
    text: row.note.body || ""
    textFormat: Text.PlainText
    elide: Text.ElideRight
    maximumLineCount: 1
    color: row.tokens.muted
    font.family: row.fontFamily
    font.pixelSize: Tokens.type.small
  }
  Text {
    width: parent.width
    text: (row.note.agent ? row.note.agent.name : "Agent") + " · " + (row.note.at_label || "")
    textFormat: Text.PlainText
    elide: Text.ElideRight
    color: row.tokens.muted
    font.family: row.fontFamily
    font.pixelSize: Tokens.type.small
  }
  TokenButton {
    tokens: row.tokens
    text: "Open notification"
    onClicked: row.openRequested()
  }
}
