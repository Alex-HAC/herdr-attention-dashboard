"""Desktop-only launch and window helpers. All addresses come from Settings."""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from .config import Settings, load_file
from .ui_contract import CONTRACT, desktop_url, hash_path, navigation_names


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=5, check=True)


def focus_window(address: str) -> None:
    # Current Omarchy uses Lua dispatch; retain support for older Hyprland.
    try:
        run(['hyprctl', 'dispatch', f'hl.dsp.focus({{ window = "address:{address}" }})'])
    except subprocess.CalledProcessError:
        run(['hyprctl', 'dispatch', 'focuswindow', f'address:{address}'])


def dispatch(lua: str, legacy: list[str]) -> None:
    """One Hyprland dispatch: the Lua form (current Omarchy), else the old dispatcher."""
    try:
        run(['hyprctl', 'dispatch', lua])
    except subprocess.CalledProcessError:
        run(['hyprctl', 'dispatch', *legacy])


def active_workspace() -> dict:
    return json.loads(run(['hyprctl', 'activeworkspace', '-j']).stdout)


def clients() -> list[dict]:
    return json.loads(run(['hyprctl', 'clients', '-j']).stdout)


def herdr_window(windows: list[dict], proc: Path = Path('/proc')) -> dict | None:
    """Find a Ghostty containing a Herdr TUI child, not the background server.

    Window titles and agent names are deliberately irrelevant: a browser or
    another terminal titled Herdr must not be raised by a pane focus.
    """
    ghostty = {int(w['pid']): w for w in windows
               if 'ghostty' in str(w.get('class', '')).lower() and w.get('pid')}
    for path in proc.iterdir():
        if not path.name.isdigit():
            continue
        try:
            if (path / 'comm').read_text().strip() != 'herdr':
                continue
            args = (path / 'cmdline').read_bytes().split(b'\0')
            # Interactive invocations: herdr, herdr --session NAME, or attach.
            tail = [arg.decode(errors='replace') for arg in args[1:] if arg]
            if tail and not (tail[0] == '--session' or tail[:2] == ['session', 'attach']):
                continue
            pid = int(path.name)
            seen = set()
            while pid > 1 and pid not in seen:
                if pid in ghostty:
                    return ghostty[pid]
                seen.add(pid)
                status = (proc / str(pid) / 'status').read_text()
                pid = int(next(line.split()[1] for line in status.splitlines() if line.startswith('PPid:')))
        except (OSError, ValueError, StopIteration):
            continue
    return None


def raise_herdr() -> str:
    window = herdr_window(clients())
    if window:
        focus_window(window['address'])
        return 'focused'
    run(['omarchy-launch-terminal-herdr'])
    return 'launched'


def overview_main(argv: list[str] | None = None) -> int:
    import argparse
    from urllib.request import Request, build_opener, ProxyHandler
    parser = argparse.ArgumentParser(description=CONTRACT['desktop']['title'])
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--notifications', action='store_true')
    group.add_argument('--notification', type=int)
    group.add_argument('--route', choices=navigation_names())
    group.add_argument('--terminal', metavar='IDENTITY', help='open an agent\'s live terminal view')
    parser.add_argument('--print-url', action='store_true')
    args = parser.parse_args(argv)
    settings = Settings(load_file(None))
    url = desktop_url(settings)
    target = (hash_path('notification', args.notification) if args.notification is not None else
              hash_path('notifications') if args.notifications else
              hash_path(args.route) if args.route else
              hash_path('terminal', args.terminal) if args.terminal else '')
    if args.print_url:
        print(url + '/' + target)
        return 0
    if not settings.get("desktop.enabled"):
        parser.error("desktop overview is disabled in config.toml")
    if target:
        from .ui_contract import API
        opener = build_opener(ProxyHandler({}))
        # Hand a notification or view route to an already-open overview too. This
        # only navigates the dashboard; it never focuses a Herdr pane.
        with opener.open(url + API['session'], timeout=3) as response:
            csrf = json.load(response)['csrf']
        request = Request(url + API['desktop_navigation'], data=json.dumps({'hash': target}).encode(),
                          headers={'Content-Type': 'application/json', 'Origin': url, 'X-CSRF-Token': csrf})
        with opener.open(request, timeout=3):
            pass
    try:
        show_overview(url + '/' + target)
    except (OSError, ValueError, subprocess.SubprocessError):
        launch_overview(url + '/' + target)
    return 0


# ------------------------------------------------------------- overview window (v0.9.8.4)
#
# The dashboard window is a Chromium --app window. Chromium only honours --class when it
# starts a new browser process; handed to a running browser (Omarchy's handoff) it is
# ignored and the window gets Chromium's own app id ("chrome-127.0.0.1__-Default"). When
# the overview was the first Chromium window, the whole browser got the class instead, so
# every later browser window was "herdr-overview" too, and the shortcut focused one of
# those (another site, another workspace). The window is therefore found by its page
# title, which the dashboard sets, or by the app id of its address, never by class alone.

LAUNCH_STAMP = Path.home() / '.cache' / 'herdr-attention' / 'overview-launch'
LAUNCH_GRACE = 8.0       # seconds: a second press while the window is still opening does not open another


def _app_ids(url: str) -> set[str]:
    from urllib.parse import urlsplit
    parts = urlsplit(url)
    path = (parts.path or '/').rstrip('/').replace('/', '_')
    return {f'{browser}-{parts.hostname}_{path}_-' for browser in ('chrome', 'brave', 'msedge', 'vivaldi', 'opera', 'helium')}


def find_overview(windows: list[dict], url: str) -> dict | None:
    """The dashboard's own window: exact page title first, then its app id with a
    dashboard title. A window that only carries the class (a normal browser window
    of a process started with --class) is never picked."""
    title = CONTRACT['desktop']['title']
    exact = [w for w in windows if w.get('title') == title]
    if exact:
        return min(exact, key=lambda w: w.get('focusHistoryID', 1 << 30))
    ids = _app_ids(url)
    for w in sorted(windows, key=lambda w: w.get('focusHistoryID', 1 << 30)):
        klass = str(w.get('class') or w.get('initialClass') or '')
        if any(klass.startswith(i) for i in ids) and str(w.get('title', '')).startswith('Herdr'):
            return w
    return None


def plan(windows: list[dict], workspace: dict, url: str, launched_at: float | None, now: float) -> tuple[str, dict | None]:
    """What the shortcut does: ('focus', w) where it is; ('bring', w) from a special
    (hidden) workspace to the current one; ('wait', None) while a launch is still opening;
    ('launch', None) otherwise."""
    window = find_overview(windows, url)
    if window:
        name = str((window.get('workspace') or {}).get('name', ''))
        here = str(workspace.get('name', workspace.get('id', '')))
        if name.startswith('special:') and name != here:
            return 'bring', window
        return 'focus', window
    if launched_at is not None and 0 <= now - launched_at < LAUNCH_GRACE:
        return 'wait', None
    return 'launch', None


def launch_overview(url: str) -> None:
    # No --class: a running Chromium ignores it, a starting one applies it to every window.
    LAUNCH_STAMP.parent.mkdir(parents=True, exist_ok=True)
    LAUNCH_STAMP.write_text(str(time.time()))
    subprocess.Popen(['omarchy-launch-webapp', url], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def show_overview(url: str) -> str:
    try:
        launched_at = float(LAUNCH_STAMP.read_text())
    except (OSError, ValueError):
        launched_at = None
    workspace = active_workspace()
    action, window = plan(clients(), workspace, url, launched_at, time.time())
    if action == 'launch':
        launch_overview(url)
    elif action == 'bring':
        target = str(workspace.get('id'))
        address = window['address']
        dispatch(f'hl.dsp.window.move({{ workspace = "{target}", window = "address:{address}" }})',
                 ['movetoworkspacesilent', f'{target},address:{address}'])
        focus_window(address)
    elif action == 'focus':
        focus_window(window['address'])
    return action
