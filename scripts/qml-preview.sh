#!/bin/bash
# Render the panel's parts (hero, Arrange blocks, quota accounts, agent rows,
# workspaces) with sample data in the real Quickshell engine, offscreen
# (QT_QPA_PLATFORM=offscreen: no window opens, the running shell is not
# touched). Writes a PNG.
#   scripts/qml-preview.sh [out.png] [width]   (default docs/screenshots/panel.png, 480 px)
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
out=$(realpath -m "${1:-$repo/docs/screenshots/panel.png}")
width=${2:-480}
[[ $width =~ ^[0-9]+$ ]] || { echo "width must be a number" >&2; exit 2; }
shell_dir=${OMARCHY_SHELL_DIR:-/usr/share/omarchy/shell}
command -v qs >/dev/null && [[ -d $shell_dir/Commons ]] || { echo "needs quickshell (qs) and $shell_dir" >&2; exit 2; }
tmp=$(mktemp -d "${XDG_RUNTIME_DIR:-/tmp}/hat-preview.XXXXXX"); trap 'rm -rf "$tmp"' EXIT
mkdir -p "$(dirname "$out")"
ln -s "$shell_dir/Commons" "$tmp/Commons"; ln -s "$shell_dir/Ui" "$tmp/Ui"
sed -e "s#@PLUGIN@#$repo/plugin/alexvdb.herdr-attention#g" -e "s#@OUT@#$out#" -e "s#@WIDTH@#$width#" "$repo/tests/qml/preview.qml" > "$tmp/preview.qml"
cat > "$tmp/shell.qml" <<'QML'
import QtQuick
import Quickshell
ShellRoot {
  property var shown: null
  Component.onCompleted: {
    var c = Qt.createComponent("preview.qml", Component.PreferSynchronous)
    if (c.status !== Component.Ready) { console.log("PREVIEW_ERROR " + c.errorString()); Qt.quit(); return }
    shown = c.createObject(null)
  }
}
QML
rm -f "$out"
(cd "$tmp" && HERDR_ATTENTION_CLI=/bin/true QT_QPA_PLATFORM=offscreen timeout 40 qs -p "$tmp" --no-color 2>&1 \
  | grep -E 'PREVIEW_ERROR|SAVED|TypeError|ReferenceError|Unable to assign' || true)
[[ -s $out ]] && echo "wrote $out"
