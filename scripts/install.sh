#!/bin/bash
# Install (or upgrade) the collector, its user service and the desktop plugin.
# Everything goes into your home directory; nothing needs sudo.
#
#   scripts/install.sh                 check, install, start, enable the bar widget
#   scripts/install.sh --no-plugin     collector only
#   scripts/install.sh --plugin-files-only   copy the plugin but do not enable it
#   scripts/install.sh --collector-only  collector/service only; leave desktop integrations alone
#   scripts/install.sh --skip-checks   do not run the tests first
#
# Undo with scripts/uninstall.sh (or --rollback to go back one version).
set -euo pipefail

repo=$(cd "$(dirname "$0")/.." && pwd)
share="$HOME/.local/share/herdr-attention"
app="$share/app"
bin="$HOME/.local/bin/herdr-attention"
conf_dir="${XDG_CONFIG_HOME:-$HOME/.config}/herdr-attention"
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
plugin_id="alexvdb.herdr-attention"
plugin_dst="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/plugins/$plugin_id"
shell_json="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/shell.json"
stamp=$(date +%Y%m%d-%H%M%S)

with_plugin=1
enable_plugin=1
checks=1
with_desktop=1
with_native=1
for arg in "$@"; do
  case "$arg" in
    --collector-only) with_plugin=0; enable_plugin=0; with_desktop=0; with_native=0 ;;
    --no-plugin) with_plugin=0; enable_plugin=0 ;;
    --plugin-files-only) enable_plugin=0 ;;
    --skip-checks) checks=0 ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

step() { printf '\n== %s\n' "$*"; }
log() { mkdir -p "$share/backup"; echo "$stamp $*" >> "$share/install.log"; }

version=$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import herdr_attention; print(herdr_attention.__version__)' "$repo")

if [[ -x $bin ]]; then
  installed_version=$("$bin" --version)
  python3 - "$installed_version" "$version" <<'PYVERSION'
import re, sys

def version(text):
    if not re.fullmatch(r"\d+(\.\d+){2,3}", text.strip()):   # 0.9.3 or a hotfix such as 0.9.3.1
        raise SystemExit("Cannot compare installed version; installation stopped")
    return tuple(map(int, text.strip().split('.')))
if version(sys.argv[1]) > version(sys.argv[2]):
    raise SystemExit("Installed app is newer; merge latest local main before installing")
PYVERSION
fi

if (( checks )); then
  step "Running tests"
  (cd "$repo" && python3 -m unittest discover -s tests -t . -q)
  (cd "$repo" && node tests/js/model.test.js)
  (cd "$repo" && python3 scripts/gen_ui_contract.py --check)
  if (( with_plugin )); then
    "$repo/scripts/qml-check.sh"
  fi
fi

step "Installing collector $version to $app"
mkdir -p "$share/backup"
rm -rf "$share/app.failed"   # left over from an earlier --rollback
if [[ -d $app ]]; then
  rm -rf "$share/app.previous"
  mv "$app" "$share/app.previous"
  echo "previous version kept in $share/app.previous (scripts/uninstall.sh --rollback)"
fi
mkdir -p "$app"
cp -r "$repo/herdr_attention" "$app/"
cp -r "$repo/herdr-plugin" "$app/"
mkdir -p "$app/config" "$app/docs/skills/herdr-coordinator"
if [[ -f $share/app.previous/config/routing.toml ]]; then
  cp -p "$share/app.previous/config/routing.toml" "$app/config/routing.toml"
  cp "$repo/config/routing.toml" "$app/config/routing.toml.dist"
  routing_diff="$share/backup/routing.toml.$stamp.diff"
  (umask 077; diff -u "$app/config/routing.toml" "$app/config/routing.toml.dist" > "$routing_diff") || [[ $? == 1 ]]
  chmod 600 "$routing_diff"
  echo "kept installed routing.toml; new default: $app/config/routing.toml.dist"
  echo "routing diff: $routing_diff"
else
  cp "$repo/config/routing.toml" "$app/config/routing.toml"
fi
cp "$repo/docs/AGENT-ROUTING.md" "$app/docs/"
cp "$repo/docs/skills/herdr-coordinator/SKILL.md" "$app/docs/skills/herdr-coordinator/"
find "$app" -name __pycache__ -prune -exec rm -rf {} +
echo "$version" > "$app/VERSION"
log "installed app $version"

mkdir -p "$(dirname "$bin")"
cat > "$bin" <<PY
#!/usr/bin/python3 -s
# herdr-attention launcher (written by scripts/install.sh)
import sys
sys.path.insert(0, "$app")
from herdr_attention.cli import main
sys.exit(main())
PY
chmod 755 "$bin"
echo "command: $bin"
cat > "$(dirname "$bin")/herdr-spawn" <<PY
#!/usr/bin/python3 -s
# herdr-spawn alias (written by scripts/install.sh)
import sys
sys.path.insert(0, "$app")
from herdr_attention.cli import spawn_main
sys.exit(spawn_main())
PY
chmod 755 "$(dirname "$bin")/herdr-spawn"

step "Config"
mkdir -p -m 700 "$conf_dir"
if [[ -f $conf_dir/config.toml ]]; then
  echo "keeping existing $conf_dir/config.toml"
else
  cp "$repo/config/config.example.toml" "$conf_dir/config.toml"
  chmod 600 "$conf_dir/config.toml"
  echo "created $conf_dir/config.toml (no workspaces watched yet)"
  log "created config"
fi

if (( with_desktop )); then
step "Desktop overview launcher and hotkey"
python3 - "$app" "$(dirname "$bin")" "${XDG_CONFIG_HOME:-$HOME/.config}/hypr/bindings.lua" "$share/backup" <<'PYTHON'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from herdr_attention.desktop_install import install
install(*(Path(arg) for arg in sys.argv[1:]))
PYTHON
if command -v hyprctl >/dev/null && [[ -n ${HYPRLAND_INSTANCE_SIGNATURE:-} ]]; then
  hyprctl reload
  errors=$(hyprctl configerrors)
  if [[ -n $errors && $errors != "ok" ]]; then
    echo "Hyprland config validation failed: $errors" >&2
    exit 1
  fi
fi
log "overview wrapper and hotkey installed"
fi

step "Backing up history"
state_db="${XDG_STATE_HOME:-$HOME/.local/state}/herdr-attention/state.db"
if [[ -f $state_db ]]; then
  # Online copy (safe while the collector runs); the database may be migrated after the restart.
  python3 -c 'import sqlite3, sys; src = sqlite3.connect(sys.argv[1]); dst = sqlite3.connect(sys.argv[2]); src.backup(dst); dst.close()' \
    "$state_db" "$share/backup/state.db.$stamp"
  chmod 600 "$share/backup/state.db.$stamp"
  echo "copied $state_db to $share/backup/state.db.$stamp"
  log "state db backed up"
fi

step "User service"
mkdir -p "$unit_dir"
if [[ -f $unit_dir/herdr-attention.service ]]; then
  cp "$unit_dir/herdr-attention.service" "$share/backup/herdr-attention.service.$stamp"
fi
cp "$repo/systemd/herdr-attention.service" "$unit_dir/herdr-attention.service"
systemctl --user daemon-reload
systemctl --user enable herdr-attention.service >/dev/null
systemctl --user restart herdr-attention.service
log "service enabled and restarted"
for _ in 1 2 3 4 5 6 7 8 9 10; do
  "$bin" status >/dev/null 2>&1 && break
  sleep 0.5
done
if "$bin" status >/dev/null 2>&1; then
  echo "collector is running"
else
  echo "collector did not answer yet; see: journalctl --user -u herdr-attention -n 50" >&2
fi

if (( with_native )); then
step "Native Herdr sidebar plugin"
herdr_conf="${XDG_CONFIG_HOME:-$HOME/.config}/herdr"
if [[ -f $herdr_conf/config.toml ]]; then
  cp "$herdr_conf/config.toml" "$share/backup/herdr-config.toml.$stamp"
fi
if [[ -d $herdr_conf/plugins ]]; then
  cp -a "$herdr_conf/plugins" "$share/backup/herdr-plugins.$stamp"
fi
herdr plugin link "$app/herdr-plugin" --enabled
log "native Herdr sidebar plugin linked (no server restart)"
fi

if (( with_plugin )); then
  step "Desktop plugin"
  if [[ -f $shell_json ]]; then
    cp "$shell_json" "$share/backup/shell.json.$stamp"
    echo "backed up shell.json to $share/backup/shell.json.$stamp"
  fi
  if [[ -d $plugin_dst ]]; then
    rm -rf "$share/backup/plugin.previous"
    cp -r "$plugin_dst" "$share/backup/plugin.previous"
  fi
  mkdir -p "$plugin_dst"
  rsync -a --delete "$repo/plugin/$plugin_id/" "$plugin_dst/"
  echo "copied plugin to $plugin_dst"
  log "plugin files copied"
  if (( enable_plugin )) && command -v omarchy >/dev/null; then
    omarchy-shell shell rescanPlugins >/dev/null 2>&1 || true
    if omarchy plugin enable "$plugin_id"; then
      log "plugin enabled"
    else
      echo "could not enable the plugin automatically; try: omarchy plugin enable $plugin_id" >&2
    fi
  fi
fi

step "Done"
cat <<EOF
Next:
  herdr-attention workspace list            see your workspaces
  herdr-attention workspace enable <id>     start watching one (or use the panel's Settings)
  herdr-attention doctor                    check the setup
If the bar icon does not appear, run: omarchy restart shell
EOF
