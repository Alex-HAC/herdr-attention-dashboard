# Install and rollback

## What `scripts/install.sh` does

1. Runs the unit tests and `scripts/qml-check.sh` (skip with `--skip-checks`).
2. Moves any installed version to `~/.local/share/herdr-attention/app.previous`
   and copies the package to `~/.local/share/herdr-attention/app`.
3. Writes the launcher `~/.local/bin/herdr-attention`.
4. Creates `~/.config/herdr-attention/config.toml` from the example **only if
   it does not exist** (no workspaces watched).
5. Installs `~/.config/systemd/user/herdr-attention.service`, enables and
   restarts it.
6. Backs up `~/.config/omarchy/shell.json` to
   `~/.local/share/herdr-attention/backup/`, copies the plugin to
   `~/.config/omarchy/plugins/alexvdb.herdr-attention`, and runs
   `omarchy plugin enable alexvdb.herdr-attention` (which adds the icon to the bar).

Options: `--no-plugin` (collector only), `--plugin-files-only` (copy, do not
enable), `--skip-checks`. Every step is logged to
`~/.local/share/herdr-attention/install.log`.

Nothing outside your home directory is touched. No network listener is
opened (the local API is a Unix socket only you can open).

## Restart the shell?

Changes to `Panel.qml`, `AgentRow.qml` and `Model.js` reload by themselves.
Changes to `Service.qml` need `omarchy restart shell`, because the shell keeps
a running service instance across reloads.

## Rollback and removal

| Command | Effect |
|---|---|
| `scripts/uninstall.sh --rollback` | restore the previous app and plugin files, restart the service |
| `scripts/uninstall.sh` | disable the plugin (removes the icon), stop and remove the service, the command and the app. Keeps config, history, keyring entries and backups |
| `scripts/uninstall.sh --purge` | also deletes config, history, backups and the two keyring entries |

To restore `shell.json` by hand: copy the newest file from
`~/.local/share/herdr-attention/backup/` back to `~/.config/omarchy/shell.json`
(the shell reloads it on save).

## Desktop overview installation (v0.8.4)

Do not install this branch until the parallel w8 UI work is merged and the
release is cleared for deployment. `scripts/install.sh` installs the overview
wrapper, backs up `bindings.lua`, and appends one marked Super+Shift+H binding
labelled Herdr overview. It refuses a conflicting binding and preserves an existing
search on Super+Shift+S. Hyprland reload and configerrors validation follow.
No Tailscale settings are changed. Only herdr-attention.service is restarted.

Chromium on this laptop ignores `--class` on Wayland (verified with an isolated
profile). The wrapper also finds the exact page title, Herdr overview, before
launching, so repeated hotkeys focus the existing window. Notification actions
hand their route to that window through a desktop-only, CSRF-protected API;
opening a notification never focuses a terminal pane.

Uninstall removes only the marked binding and the overview wrapper. Rollback
to a version without desktop support removes those too. Other bindings stay.

At deployment, add this tip to the Obsidian note
`Hardware/Omarchy — Hyprland cheatsheet.md`:

> **Super+Shift+H — Herdr overview:** open or focus the desktop overview.
> Use **Open in Herdr** to jump to the agent's workspace, tab and pane.
> Super+Shift+S remains Work search.
