#!/bin/bash
# Remove or roll back the Herdr attention dashboard.
#
#   scripts/uninstall.sh              remove plugin, service and command; keep config, history and secrets
#   scripts/uninstall.sh --purge      also delete config, history and the keyring entries
#   scripts/uninstall.sh --rollback   go back to the previously installed version
set -euo pipefail

share="$HOME/.local/share/herdr-attention"
bin="$HOME/.local/bin/herdr-attention"
conf_dir="${XDG_CONFIG_HOME:-$HOME/.config}/herdr-attention"
state_dir="${XDG_STATE_HOME:-$HOME/.local/state}/herdr-attention"
unit="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/herdr-attention.service"
plugin_id="alexvdb.herdr-attention"
plugin_dst="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/plugins/$plugin_id"
keyring_service=herdr-attention-dashboard

mode=remove
case "${1:-}" in
  "") ;;
  --purge) mode=purge ;;
  --rollback) mode=rollback ;;
  -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
  *) echo "unknown option: $1" >&2; exit 2 ;;
esac

remove_overview() {
  if [[ -f $share/app/herdr_attention/desktop_install.py ]]; then
    python3 - "$share/app" "${XDG_CONFIG_HOME:-$HOME/.config}/hypr/bindings.lua" "$share/backup" <<'PYTHON'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from herdr_attention.desktop_install import remove
remove(Path(sys.argv[2]), Path(sys.argv[3]))
PYTHON
    if command -v hyprctl >/dev/null && [[ -n ${HYPRLAND_INSTANCE_SIGNATURE:-} ]]; then
      hyprctl reload
      errors=$(hyprctl configerrors)
      [[ -z $errors || $errors == "ok" ]] || { echo "Hyprland config validation failed: $errors" >&2; exit 1; }
    fi
  fi
  rm -f "$(dirname "$bin")/herdr-attention-overview"
}

if [[ $mode == rollback ]]; then
  [[ -d $share/app.previous ]] || { echo "no previous version to roll back to" >&2; exit 1; }
  if [[ ! -f $share/app.previous/herdr_attention/desktop.py ]]; then remove_overview; fi
  rm -rf "$share/app.failed"
  if [[ ! -d $share/app.previous/herdr-plugin ]]; then
    herdr plugin unlink herdr-attention >/dev/null 2>&1 || true
  fi
  mv "$share/app" "$share/app.failed"
  mv "$share/app.previous" "$share/app"
  if [[ -d $share/backup/plugin.previous ]]; then
    rsync -a --delete "$share/backup/plugin.previous/" "$plugin_dst/"
    echo "restored previous plugin files"
  fi
  if ! rg -q 'def spawn_main' "$share/app/herdr_attention/cli.py"; then
    rm -f "$(dirname "$bin")/herdr-spawn"
  fi
  systemctl --user restart herdr-attention.service
  echo "rolled back to $(cat "$share/app/VERSION" 2>/dev/null || echo 'previous version'); the replaced one is in $share/app.failed"
  exit 0
fi

if command -v omarchy >/dev/null && [[ -d $plugin_dst ]]; then
  omarchy plugin disable "$plugin_id" || true
fi
herdr plugin unlink herdr-attention >/dev/null 2>&1 || true
rm -rf "$plugin_dst"
echo "removed plugin"

systemctl --user disable --now herdr-attention.service 2>/dev/null || true
rm -f "$unit"
systemctl --user daemon-reload
echo "removed user service"

remove_overview
rm -f "$bin" "$(dirname "$bin")/herdr-spawn"
rm -rf "$share/app" "$share/app.previous" "$share/app.failed"
echo "removed command and app"

if [[ $mode == purge ]]; then
  rm -rf "$conf_dir" "$state_dir" "$share"
  for key in ntfy-token dashboard-passphrase; do
    secret-tool clear service "$keyring_service" key "$key" 2>/dev/null || true
  done
  echo "deleted config, history and keyring entries"
else
  echo "kept $conf_dir, $state_dir and keyring entries (use --purge to delete them)"
  echo "shell.json backups are in $share/backup"
fi
