"""Command line: run the collector and talk to it over the local socket."""

from __future__ import annotations

from .ui_contract import API, api_path

import argparse
import getpass
import http.client
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from . import __version__
from .config import Settings, config_dir, load_file


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float = 10.0):
        super().__init__("localhost", timeout=timeout)
        self.socket_path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self.socket_path)
        self.sock = sock


class CollectorDown(RuntimeError):
    pass


class Client:
    def __init__(self, socket_path: str):
        self.socket_path = socket_path

    def request(self, method: str, path: str, body: dict | None = None, timeout: float = 10.0):
        conn = UnixConnection(self.socket_path, timeout)
        try:
            payload = json.dumps(body).encode() if body is not None else None
            headers = {"Content-Type": "application/json"} if payload is not None else {}
            conn.request(method, path, body=payload, headers=headers)
            response = conn.getresponse()
            data = json.loads(response.read() or b"{}")
        except (FileNotFoundError, ConnectionRefusedError) as exc:
            raise CollectorDown(f"collector is not running ({self.socket_path}): {exc.strerror}") from None
        finally:
            conn.close()
        if response.status >= 400:
            raise RuntimeError(data.get("error") or f"HTTP {response.status}")
        return data

    def get(self, path: str):
        return self.request("GET", path)

    def post(self, path: str, body: dict | None = None):
        return self.request("POST", path, body or {})


def load_settings(args) -> Settings:
    return Settings(load_file(Path(args.config) if args.config else None))


def _print(data) -> None:
    print(json.dumps(data, indent=2, sort_keys=True))


def _ago(ts: float | None) -> str:
    if not ts:
        return "?"
    seconds = int(time.time() - ts)
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 5400:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


# commands ---------------------------------------------------------------------

def cmd_run(args) -> int:
    from .collector import Collector
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    Collector(config_path=Path(args.config) if args.config else None).run()
    return 0


def cmd_status(args, client: Client) -> int:
    view = client.get(API['view'])
    herdr = view["collector"]["herdr"]
    presence = view.get("presence", {})
    notify = view.get("notify", {})
    print(f"Herdr: {'connected' if herdr['connected'] else 'OFFLINE'} (last contact {_ago(herdr.get('last_contact'))} ago)"
          + (f" - {herdr['error']}" if herdr.get("error") and not herdr["connected"] else ""))
    print(f"Presence: {presence.get('state')} (counts as {presence.get('counts_as')}); phone alerts: "
          f"{notify.get('backend')} - {notify.get('detail')}")
    watched = [w for w in view["workspaces"] if w["watched"]]
    print(f"Watching: {', '.join(w['label'] + ' (' + w['id'] + ')' for w in watched) or 'nothing yet - run: herdr-attention workspace list'}")
    print()
    if not view["agents"]:
        print("No agents in watched workspaces.")
    for a in view["agents"]:
        since = _ago(a["status_since"]) + ("" if a["since_known"] else "+")
        flag = " (read)" if a["acked"] else ""
        print(f"  {a['label']:<28} {(a['name'] or a['agent'] or '?'):<20} {a['workspace_label']:<22} {since:>7}{flag}")
    return 0


def cmd_watch(args, client: Client) -> int:
    """Print one JSON line per update forever. Used by the desktop plugin."""
    out = sys.stdout
    while True:
        conn = UnixConnection(client.socket_path, timeout=60)
        try:
            conn.request("GET", API['stream'])
            response = conn.getresponse()
            while True:
                line = response.readline()
                if not line:
                    break
                out.write(line.decode())
                out.flush()
        except (OSError, http.client.HTTPException) as exc:
            out.write(json.dumps({"type": "offline", "error": str(exc)}) + "\n")
            out.flush()
        finally:
            conn.close()
        if args.once:
            return 1
        time.sleep(3)


def cmd_view(args, client: Client) -> int:
    _print(client.get(API['view']))
    return 0


def cmd_history(args, client: Client) -> int:
    _print(client.get(f"{API['history']}?limit={args.limit}"))
    return 0


def cmd_output(args, client: Client) -> int:
    from urllib.parse import quote
    _print(client.get(f"{API['output']}?identity={quote(args.identity, safe='')}"))
    return 0


def cmd_agent(args, client: Client) -> int:
    from urllib.parse import quote
    _print(client.get(f"{API['agent']}?identity={quote(args.identity, safe='')}"))
    return 0


def cmd_conversation(args, client: Client) -> int:
    from urllib.parse import quote
    path = f"{API['conversation']}?identity={quote(args.identity, safe='')}&limit={args.limit}"
    if args.before is not None:
        path += f"&before={args.before}"
    _print(client.get(path))
    return 0


def cmd_inference(args, client: Client) -> int:
    from urllib.parse import urlencode
    _print(client.get(API['inference_check'] + "?" + urlencode({'ignore_declared': ','.join(args.ignore_declared)})))
    return 0


def cmd_team(args, client: Client) -> int:
    """Declared roles and reporting lines (admin actions; agents are never contacted)."""
    if args.action == "list":
        _print(client.get(API['relations']))
        return 0
    if args.action == "history":
        from urllib.parse import quote
        if not args.agent:
            raise SystemExit("usage: herdr-attention team history <agent>")
        detail = client.get(f"{API['agent']}?identity={quote(args.agent, safe='')}") if ":" in args.agent else None
        _print((detail or {}).get("relation_history") or client.get(API['relations']))
        return 0
    if args.action == "start":
        return team_start(args, client)
    if not args.agent:
        raise SystemExit(f"usage: herdr-attention team {args.action} <agent> ...")
    body = {"subject": args.agent}
    if args.action == "role":
        if not args.value:
            raise SystemExit("usage: herdr-attention team role <agent> <role> [--clear]")
        body.update(action="clear" if args.clear else "set", kind="role", value=args.value)
    elif args.action in ("team", "task"):
        if args.clear:
            body.update(action="clear", kind=args.action, value=args.value)
        elif not args.value:
            raise SystemExit(f"usage: herdr-attention team {args.action} <agent> <{args.action}> [--clear]")
        else:
            body.update(action="set", kind=args.action, value=args.value)
    elif args.action in ("link", "unlink"):
        target = args.reports_to or args.spawned_by
        if not target:
            raise SystemExit(f"usage: herdr-attention team {args.action} <agent> --reports-to <agent>")
        body.update(action=args.action, kind="reports_to" if args.reports_to else "spawned_by", object=target)
    elif args.action == "assign":
        if not args.to:
            raise SystemExit("usage: herdr-attention team assign <agent> --to <coordinator>  (hands over)")
        body.update(action="handover", object=args.to)
    _print(client.post(API['relations'], body))
    return 0


def team_start(args, client: Client) -> int:
    from .herdr import HerdrClient
    from .launcher import LaunchError, plan, preview, run
    from .routing import load, pick, RoutingError, coordinator_skill
    herdr = HerdrClient()
    try:
        data = load()
        walls = client.get(API['routing_walls'])['walls']
        selection = pick(data, role=args.role or 'backend', cwd=args.cwd or os.getcwd(), walls=walls,
                         agent=args.routing_agent, kind=args.kind, model=args.model, effort=args.effort)
        caller = herdr.agent(os.environ.get('HERDR_PANE_ID', ''))
        caller_pane = os.environ.get('HERDR_PANE_ID')
        caller_identity = next((a['identity'] for a in client.get(API['view'])['agents']
                                if a.get('present') and a.get('pane_id') == caller_pane), None)
        occupied = {a.get('name') for a in herdr.agents()}
        from .layout import generated_name, unique_name
        proposed = args.name_override or args.agent
        if not proposed:
            proposed = generated_name(data.get('layout', {}), role=args.role or 'worker',
                                      provider=selection['kind'], task=args.task or 'task', suffix='task')
        args.agent = unique_name(proposed, occupied)
        reports_to = args.reports_to
        if reports_to and reports_to != 'me':
            view = client.get(API['view'])
            matches = [a['identity'] for a in view['agents'] if reports_to in (a['identity'], a['name'], a['pane_id'])]
            if len(matches) != 1:
                raise LaunchError('--reports-to must name one watched agent')
            reports_to = matches[0]
        steps = plan(name=args.agent or '', kind=selection['kind'], env=dict(os.environ), config_dir=args.config_dir,
                     direction=args.direction, cwd=args.cwd or os.getcwd(), agent_args=args.agent_args or [],
                     selection=selection, permissions=data['permissions']['yolo'][selection['kind']],
                     sandbox=args.sandbox, role=args.role, brief=args.brief, team=args.team_label, task=args.task, tab=args.tab,
                     api_socket=client.socket_path, caller=caller, parent_identity=caller_identity, layout_policy=data.get('layout'),
                     existing_tabs=herdr.tabs(), existing_panes=herdr.call('pane.list').get('panes', []),
                     explicit_split=args.split, focus=args.focus)
        if args.role == 'coordinator':
            rules = Path(__file__).resolve().parent.parent / 'docs/AGENT-ROUTING.md'
            path = Path(steps['hook_dir']) / 'coordinator/SKILL.md'
            steps['args'][-1] = steps['args'][-1].replace(str(rules.parent / 'skills/herdr-coordinator/SKILL.md'), str(path))
            steps['coordinator_skill'] = coordinator_skill(data, rules)
        print(f"Routing: {selection['agent']} ({selection['account']}): {selection['why']}", file=sys.stderr)
        if args.dry_run:
            _print({'dry_run': True, **preview(steps)})
            return 0
        result = run(steps, name=args.agent, register=lambda body: client.post(API['relations'], body),
                     register_launch=lambda body: client.post(API['launches'], body),
                     bind_launch=lambda body: client.post(API['launches_bind'], body),
                     agent_info=herdr.agent, role=args.role, team=args.team_label, task=args.task,
                     reports_to=reports_to)
    except (LaunchError, RoutingError) as exc:
        raise SystemExit(f'herdr-attention: {exc}')
    _print(result)
    return 0


def cmd_routing(args, client: Client) -> int:
    from .routing import load, pick, RoutingError
    if args.action == 'clear':
        if not args.provider:
            raise SystemExit('routing clear needs --provider')
        _print(client.post(API['routing_clear'], {'provider': args.provider}))
        return 0
    try:
        data = load()
        walls = client.get(API['routing_walls'])['walls']
        if args.role:
            result = pick(data, role=args.role, cwd=args.cwd or os.getcwd(), walls=walls)
            result.pop('env', None)
            _print(result)
        else:
            _print({'roles': data['roles'], 'agents': data['agents'], 'walls': walls,
                    'permissions': data['permissions'], 'projects': data['project'], 'layout': data.get('layout', {})})
    except RoutingError as exc:
        raise SystemExit(f'herdr-attention: {exc}')
    return 0


def cmd_layout(args, client):
    from urllib.parse import urlencode
    data = client.get(API['layout'] + ('?' + urlencode({'workspace': args.workspace}) if args.workspace else ''))
    if args.json:
        _print(data)
        return 0
    hints = data['findings']
    print(f"Layout: {len(hints)} quiet suggestion(s); no changes made.")
    for hint in hints:
        print(f"  {hint['workspace_id']}/{hint['tab_id']}: {hint['message']}")
        for command in hint['commands']:
            print('    ' + command)
    return 0


def cmd_cleanup(args, client: Client) -> int:
    """Preview only. Closing panes needs Alex's confirm in the dashboard."""
    if not args.preview:
        raise SystemExit("herdr-attention cleanup: only --preview is supported here. "
                         "Confirm in the dashboard (List > Clean up Herdr).")
    body = {"workspace": args.workspace, "caller_pane": os.environ.get("HERDR_PANE_ID") or None}
    job = client.post(API['cleanup_preview'], {k: v for k, v in body.items() if v})
    deadline = time.monotonic() + 120
    while job.get("status") == "running" and time.monotonic() < deadline:
        time.sleep(0.5)
        job = client.get(f"{API['cleanup']}?id={job['id']}")
    if args.json:
        _print(job)
        return 0 if job.get("status") == "ready" else 1
    if job.get("status") != "ready":
        print(f"Clean up preview {job.get('status')}: {job.get('error') or 'no answer in time'}")
        return 1
    print(f"Clean up preview (nothing closed): {len(job['candidates'])} candidate pane(s).")
    for row in job["candidates"]:
        print(f"  {row['pane_id']:<8} {row['name'][:40]:<40} {row['reason']}")
    if job["excluded"]:
        print(f"Not included: {len(job['excluded'])}")
        for row in job["excluded"]:
            print(f"  {row['pane_id']:<8} {row['name'][:40]:<40} {row['reason']}")
    for ws in job["workspaces_off"]:
        print(f"  {ws['workspace_id']:<8} {ws['label'][:40]:<40} {ws['reason']} ({ws['panes']} pane(s))")
    return 0


def cmd_ack(args, client: Client) -> int:
    client.post(api_path("event_ack", args.event_id))
    return 0


def cmd_presence(args, client: Client) -> int:
    client.post(API['presence'], {"state": args.state, "idle_seconds": args.idle_seconds})
    return 0


def _coerce(raw: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def cmd_settings(args, client: Client) -> int:
    if args.action == "get":
        _print(client.get(API['settings']))
    elif args.action == "set":
        if not args.key or args.value is None:
            raise SystemExit("usage: herdr-attention settings set <key> <value>")
        _print(client.post(API['settings'], {args.key: _coerce(args.value)}))
    elif args.action == "reset":
        _print(client.post(API['settings_reset']))
    return 0


def cmd_workspace(args, client: Client) -> int:
    view = client.get(API['view'])
    if args.action == "list":
        for w in view["workspaces"]:
            flags = ("watched" if w["watched"] else "off") + (", muted" if w["muted"] else "")
            print(f"  {w['id']:<6} {w['label']:<30} {flags}")
        return 0
    if not args.id:
        raise SystemExit(f"usage: herdr-attention workspace {args.action} <id>")
    settings = view["settings"]
    key = "watch.enabled_workspaces" if args.action in ("enable", "disable") else "watch.muted_workspaces"
    current = set(settings[key])
    if args.action in ("enable", "mute"):
        current.add(args.id)
    else:
        current.discard(args.id)
    client.post(API['settings'], {key: sorted(current)})
    print(f"{args.id}: {args.action}d")
    return 0


def cmd_secret(args) -> int:
    from .secrets import KNOWN_KEYS, Keyring
    settings = load_settings(args)
    keyring = Keyring(settings.get("keyring.service"))
    if args.key not in KNOWN_KEYS:
        raise SystemExit(f"unknown secret {args.key!r}; known: {', '.join(KNOWN_KEYS)}")
    if args.action == "set":
        value = getpass.getpass(f"{KNOWN_KEYS[args.key]}: ")
        if args.key == "dashboard-passphrase":
            if len(value) < 12:
                raise SystemExit("use at least 12 characters")
            if getpass.getpass("again: ") != value:
                raise SystemExit("did not match")
        keyring.set(args.key, value)
        print(f"stored {args.key} in keyring service {keyring.service!r}")
    elif args.action == "clear":
        keyring.delete(args.key)
        print(f"cleared {args.key}")
    else:
        print(f"{args.key}: {'set' if keyring.has(args.key) else 'not set'} (keyring service {keyring.service!r})")
    return 0


def cmd_quotas(args, client):
    if args.refresh:
        client.request('POST', API['quotas_refresh'], {}, timeout=480)
    data = client.get(API['view']).get('quotas') or {}
    if args.json:
        _print(data)
    else:
        for account in data.get('accounts') or []:
            print(f"{account['provider_name']} · {account['header_label']} · {account['reset_text']} · {'stale · ' if account['stale'] else ''}{account['freshness']}")
    return 0

def cmd_notify(args, client: Client) -> int:
    """Show or change notification-tier settings. Never prints the ntfy topic."""
    if args.action == "preset":
        name = args.name or (args.extra[0] if getattr(args, "extra", None) else None)
        if not name:
            raise SystemExit("usage: herdr-attention notify preset <quiet|normal|close-tabs|everything>  (Only me / Normal / More / All)")
        client.post(API['settings'], {"notify_tiers.preset": name})
    view = client.get(API['view'])
    cfg = view.get("notify_tiers") or {}
    if getattr(args, "json", False):
        _print(cfg)
        return 0
    print(f"Notification preset: {cfg.get('preset_label') or cfg.get('preset') or 'Normal'}")
    if cfg.get("preset_hint"):
        print(f"  {cfg['preset_hint']}")
    estimate = cfg.get("estimate") or {}
    if estimate.get("hint"):
        print(f"  {estimate['hint']}")
    for tier in cfg.get("tiers") or []:
        state = "on" if tier.get("enabled") else "off"
        if tier.get("locked"):
            state = "always on"
        print(f"  {tier['label']}: {state} · {tier.get('delivery_label')} · AI gate: {tier.get('gate_label')}")
    if cfg.get("quiet_hours"):
        print(f"  Quiet hours: {cfg.get('quiet_start')}–{cfg.get('quiet_end')} (Waiting on you still comes through)")
    else:
        print("  Quiet hours: off")
    pins = cfg.get("watch_closely") or []
    if pins:
        print(f"  Watching closely: {len(pins)} agent{'s' if len(pins) != 1 else ''}")
    return 0


def cmd_notify_test(args) -> int:
    from .notify import Notifier, make_sender
    from .presence import Presence
    from .secrets import Keyring
    settings = load_settings(args)
    sender = make_sender(settings, Keyring(settings.get("keyring.service")))
    notifier = Notifier(settings, None, Presence(settings), sender, dict)  # type: ignore[arg-type]
    notifier.send_test()
    print("test alert sent")
    return 0


def cmd_ntfy(args) -> int:
    """Local helpers for phone notification setup. The topic is never printed."""
    if args.action == "test":
        return cmd_notify_test(args)
    from .notify import ntfy_setup
    settings = load_settings(args)
    setup = ntfy_setup(settings, include_secrets=True)
    link = setup.get("deep_link") or ""
    if not link:
        print("herdr-attention: ntfy is not configured yet", file=sys.stderr)
        return 1
    if not shutil.which("qrencode"):
        print("herdr-attention: qrencode is not installed", file=sys.stderr)
        return 1
    png = getattr(args, "png", None)
    if png:
        result = subprocess.run(["qrencode", "-o", png, "-t", "PNG", "-s", "8", "-m", "2", link],
                                capture_output=True, text=True)
        if result.returncode != 0 or not Path(png).is_file():
            print("herdr-attention: could not write the QR image", file=sys.stderr)
            return 1
        print(f"QR saved to {png}")
        return 0
    if not sys.stdout.isatty():
        print("herdr-attention: refusing to draw a QR on a non-terminal; use --png FILE", file=sys.stderr)
        return 1
    result = subprocess.run(["qrencode", "-t", "ANSIUTF8", link])
    if result.returncode != 0:
        print("herdr-attention: could not draw the QR code", file=sys.stderr)
        return 1
    print("Scan this QR with the ntfy app to subscribe. The topic is not printed here.")
    return 0


def cmd_doctor(args) -> int:
    from .herdr import HerdrClient, HerdrError
    from .secrets import Keyring
    settings = load_settings(args)
    ok = True

    def check(label: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        ok &= passed
        print(f"  [{'ok' if passed else '!!'}] {label}" + (f" - {detail}" if detail else ""))

    config_file = Path(args.config) if args.config else config_dir() / "config.toml"
    check("config file", config_file.exists(), str(config_file) if config_file.exists() else f"missing {config_file} (defaults used)")
    try:
        pong = HerdrClient(settings.get("herdr.socket") or None).ping()
        check("Herdr socket", True, f"herdr {pong.get('version')}, protocol {pong.get('protocol')}")
        if pong.get("protocol") != 22:
            print("       note: built and tested against protocol 22")
    except HerdrError as exc:
        check("Herdr socket", False, str(exc))
    try:
        view = Client(settings.api_socket).get(API['view'])
        watched = [w["id"] for w in view["workspaces"] if w["watched"]]
        check("collector", True, f"watching {', '.join(watched) or 'no workspaces yet'}")
        hints = view.get('layout', {}).get('findings', [])
        print(f"  [--] layout - {len(hints)} quiet suggestion(s); herdr-attention layout check")
        for hint in hints:
            print(f"       {hint['workspace_id']}/{hint['tab_id']}: {hint['message']}")
        herdr = view["collector"]["herdr"]
        last = herdr.get("last_reconcile")
        fresh = last is not None and time.time() - last <= 90 and not herdr.get("reconcile_error")
        check("discovery freshness", fresh, f"last successful reconcile {_ago(last)} ago"
              + (f" - {herdr['reconcile_error']}" if herdr.get("reconcile_error") else ""))
        check("presence reports", view["presence"]["state"] != "unknown",
              view["presence"]["state"] + ("" if view["presence"]["state"] != "unknown" else " - is the desktop plugin enabled?"))
    except (CollectorDown, OSError) as exc:
        check("collector", False, str(exc))
    keyring = Keyring(settings.get("keyring.service"))
    check("secret-tool", keyring.available())
    if settings.get("notify.backend") == "ntfy":
        check("ntfy config", bool(settings.get("notify.ntfy_url") and settings.get("notify.ntfy_topic")))
        check("ntfy token in keyring", keyring.has("ntfy-token"))
    else:
        print("  [--] phone alerts off (notify.backend = none)")
    if settings.get("phone.enabled"):
        check("dashboard passphrase in keyring", keyring.has("dashboard-passphrase"))
    else:
        print("  [--] phone dashboard off (phone.enabled = false)")
    return 0 if ok else 1


def cmd_record(args) -> int:
    """Record live Herdr events (read-only) as a JSONL fixture."""
    import threading
    from .herdr import HerdrClient
    client = HerdrClient()
    agents = client.agents()
    panes = [a["pane_id"] for a in agents if not args.workspace or a["workspace_id"] in args.workspace]
    stop = threading.Event()
    t0 = time.time()
    with open(args.out, "w") as fh:
        fh.write(json.dumps({"kind": "snapshot", "at": t0, "agents": agents, "workspaces": client.workspaces()}) + "\n")

        def on_event(envelope: dict) -> None:
            fh.write(json.dumps({"kind": "event", "at": round(time.time(), 3), "envelope": envelope}) + "\n")
            fh.flush()

        timer = threading.Timer(args.seconds, stop.set)
        timer.start()
        try:
            client.subscribe(panes, stop, on_event)
        finally:
            timer.cancel()
    print(f"recorded to {args.out}")
    return 0


# parser ---------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="herdr-attention", description="Which Herdr agents need you.")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config", help="config file (default ~/.config/herdr-attention/config.toml)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="run the collector (what the systemd service starts)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub.add_parser("status", help="short summary")
    sub.add_parser("view", help="full dashboard data as JSON")
    p = sub.add_parser("watch", help="stream dashboard updates as JSON lines")
    p.add_argument("--once", action="store_true", help="exit when the stream ends")
    p = sub.add_parser("history", help="recent events")
    p.add_argument("--limit", type=int, default=50)
    p = sub.add_parser("output", help="recent output of one agent (cleaned)")
    p.add_argument("identity")
    p = sub.add_parser("agent", help="one agent with its timeline and events")
    p.add_argument("identity")
    p = sub.add_parser("conversation", help="last N user/assistant entries from an agent's trace")
    p.add_argument("identity")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--before", type=int, help="byte offset from a previous page ('before')")
    p = sub.add_parser('inference', help='read-only reporting-link evidence check')
    p.add_argument('action', choices=['check'])
    p.add_argument('--ignore-declared', action='append', default=[], metavar='AGENT',
                   help='dry evaluation without this worker\'s declarations (repeatable; never deletes them)')
    p = sub.add_parser("team", help="declare roles, teams, tasks and who reports to whom")
    p.add_argument("action", choices=["list", "history", "role", "team", "task", "link", "unlink", "assign", "start"])
    p.add_argument("agent", nargs="?", help="agent identity, Herdr name or pane id (for start: the new agent's name)")
    p.add_argument("value", nargs="?", help="role / team / task text")
    p.add_argument("--clear", action="store_true", help="remove the role/team/task")
    p.add_argument("--reports-to", help="agent it reports to ('me' with start)")
    p.add_argument("--spawned-by", help="agent that started it (declared)")
    p.add_argument("--to", help="new coordinator (assign hands over)")
    p.add_argument("--kind", help="start: provider or routing agent")
    p.add_argument("--agent", dest="routing_agent", help="start: routing agent override")
    p.add_argument("--model", help="start: model override")
    p.add_argument("--effort", help="start: effort override")
    p.add_argument("--sandbox", help="start: opt out of full permissions, with a reason")
    p.add_argument("--brief", help="start: worker brief file")
    topology = p.add_mutually_exclusive_group()
    topology.add_argument("--tab", action="store_true", help="start in a new visible tab")
    topology.add_argument("--split", action="store_true", help="start in a sibling pane (default)")
    p.add_argument("--role", help="start: role to declare")
    p.add_argument("--name", dest="name_override", help="start: name override, slugged and de-duplicated")
    p.add_argument("--focus", action="store_true", help="start: explicitly focus the new pane/tab")
    p.add_argument("--team", dest="team_label", help="start: team to declare")
    p.add_argument("--task", help="start: task to declare")
    p.add_argument("--config-dir", help="start: Claude account folder (else $CLAUDE_CONFIG_DIR)")
    p.add_argument("--direction", default="right", help="start: split right or down")
    p.add_argument("--cwd", help="start: working folder")
    p.add_argument("--dry-run", action="store_true", help="start: show what would happen")
    p.add_argument("agent_args", nargs="*", help="start: arguments for the agent, after --")
    p = sub.add_parser("routing", help="inspect routing policy or clear a provider quota wall")
    p.add_argument("action", choices=["show", "clear"])
    p.add_argument("--role")
    p.add_argument("--cwd")
    p.add_argument("--provider", choices=["claude", "codex", "cursor", "agy"])
    p = sub.add_parser('layout', help='read-only layout findings and suggested commands')
    p.add_argument('action', choices=['check'])
    p.add_argument('--workspace', help='watched workspace id')
    p.add_argument('--json', action='store_true')
    p = sub.add_parser("cleanup", help="preview which Herdr panes can be closed (nothing is closed)")
    p.add_argument("--preview", action="store_true", help="list candidate panes and why; closes nothing")
    p.add_argument("--workspace", help="only this workspace id")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("ack", help="mark an event as read")
    p.add_argument("event_id", type=int)
    p = sub.add_parser("presence", help="report idle/active (the desktop plugin does this)")
    p.add_argument("state", choices=["idle", "active"])
    p.add_argument("--idle-seconds", type=float, default=0.0)
    p = sub.add_parser("summary", help="show or change AI summary settings")
    p.add_argument("action", choices=["config"], nargs="?", default="config")
    p.add_argument("--mode", choices=["ask", "on-demand", "auto", "off"],
                   help="ask/on-demand = only when you press Summarize (default); auto = also when an agent finishes")
    p.add_argument("--account", choices=["codex-personal", "codex-work", "claude-personal", "claude-main"])
    p.add_argument("--model", help="model id, e.g. gpt-6.2-sol or sonnet")
    p.add_argument("--effort", choices=["low", "medium", "high"])
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("quotas", help="show account quotas or explicitly refresh usage collectors")
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("settings", help="show or change settings")
    p.add_argument("action", choices=["get", "set", "reset"])
    p.add_argument("key", nargs="?")
    p.add_argument("value", nargs="?")
    p = sub.add_parser("workspace", help="choose which workspaces are watched")
    p.add_argument("action", choices=["list", "enable", "disable", "mute", "unmute"])
    p.add_argument("id", nargs="?")
    p = sub.add_parser("secret", help="store secrets in the GNOME keyring (never printed)")
    p.add_argument("action", choices=["set", "clear", "status"])
    p.add_argument("key")
    p = sub.add_parser("notify", help="show or change notification tiers and presets")
    p.add_argument("action", choices=["config", "preset"], nargs="?", default="config",
                   help="config: show; preset: apply quiet, normal, close-tabs or everything")
    p.add_argument("name", nargs="?", help="preset name, for 'notify preset'")
    p.add_argument("--json", action="store_true")
    sub.add_parser("notify-test", help="send a test phone alert")
    p = sub.add_parser("ntfy", help="phone notification setup (QR on this laptop, or a test alert)")
    p.add_argument("action", choices=["qr", "test"], help="qr: show a subscribe QR (topic is not printed); test: send a test alert")
    p.add_argument("--png", metavar="FILE", help="write a QR PNG instead of drawing it in the terminal")
    sub.add_parser("doctor", help="check the setup")
    p = sub.add_parser("record", help="record live Herdr events as a test fixture")
    p.add_argument("seconds", type=float)
    p.add_argument("out")
    p.add_argument("--workspace", action="append", help="only panes in this workspace (repeatable)")
    return parser


LOCAL_COMMANDS = {"run": cmd_run, "secret": cmd_secret, "notify-test": cmd_notify_test, "ntfy": cmd_ntfy,
                  "doctor": cmd_doctor, "record": cmd_record}

def cmd_summary(args, client: Client) -> int:
    """Show or change AI summary settings. Default is on-demand only."""
    patch = {}
    mode = getattr(args, "mode", None)
    if mode in ("on-demand", "ask"):
        patch["summaries.enabled"] = True
        patch["summaries.auto"] = False
    elif mode == "auto":
        patch["summaries.enabled"] = True
        patch["summaries.auto"] = True
    elif mode == "off":
        patch["summaries.enabled"] = False
        patch["summaries.auto"] = False
    if getattr(args, "account", None):
        patch["summaries.account"] = args.account
    if getattr(args, "model", None):
        patch["summaries.model"] = args.model
    if getattr(args, "effort", None):
        patch["summaries.effort"] = args.effort
    if patch:
        client.post(API['settings'], patch)
    view = client.get(API['view'])
    cfg = view.get("summaries") or {}
    if getattr(args, "json", False):
        _print(cfg)
        return 0
    mode_label = {"ask": "only when you ask (default)", "auto": "also automatically when an agent finishes",
                  "off": "off — never call a model"}.get(cfg.get("mode"), cfg.get("mode"))
    print(f"AI summaries: {mode_label}")
    print(f"  account: {cfg.get('account_label') or cfg.get('account')}")
    print(f"  model:   {cfg.get('model')}")
    print(f"  effort:  {cfg.get('effort')}")
    return 0


CLIENT_COMMANDS = {"status": cmd_status, "view": cmd_view, "watch": cmd_watch, "history": cmd_history,
                   "output": cmd_output, "agent": cmd_agent, "conversation": cmd_conversation, "team": cmd_team, "ack": cmd_ack, "presence": cmd_presence,
                   "quotas": cmd_quotas, "settings": cmd_settings, "workspace": cmd_workspace, "routing": cmd_routing, "layout": cmd_layout, "cleanup": cmd_cleanup,
                   "inference": cmd_inference, "summary": cmd_summary, "notify": cmd_notify}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    prompt = None
    if '--' in argv:
        separator = argv.index('--')
        prompt, argv = argv[separator + 1:], argv[:separator]
    args = build_parser().parse_args(argv)
    if prompt is not None:
        if args.command != 'team' or args.action != 'start':
            raise SystemExit('-- prompt is only supported by team start')
        args.agent_args = prompt
    try:
        if args.command in LOCAL_COMMANDS:
            return LOCAL_COMMANDS[args.command](args)
        client = Client(load_settings(args).api_socket)
        return CLIENT_COMMANDS[args.command](args, client)
    except CollectorDown as exc:
        print(f"herdr-attention: {exc}", file=sys.stderr)
        return 3
    except (RuntimeError, OSError) as exc:
        print(f"herdr-attention: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


def spawn_main(argv=None):
    return main(['team', 'start', *(sys.argv[1:] if argv is None else argv)])


if __name__ == "__main__":
    sys.exit(main())
