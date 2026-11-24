import QtQuick
import qs.Ui
import "Tokens.js" as Tokens

// The shell's Button in the live theme colours: pass the panel's `tokens`
// (Tokens.palette(view), from view.theme.tokens). Without them: the fallback set.
Button {
  property var tokens: Tokens.palette(null)

  foreground: tokens.text
  accent: tokens.accent
  tooltipBackground: tokens.overlay
  tooltipForeground: tokens.textStrong
  tooltipBorder: tokens.lineStrong
}
