"""Phone alerts: when to send, what to say, and the ntfy sender.

What a notification contains: agent name, workspace label, event type and a
dashboard link. Never output, titles, paths or prompts.

When it is sent (presence.mode):
  auto    only while the laptop counts as away (no input for the delay)
  always  right away
  never   never; the dashboard still records everything

While you count as present, waiting alerts are held (input cancels them).
When you count as away again, everything still open and unread goes out as
one message: a single alert for one item, a summary for several. Each event
is sent at most once.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Protocol

from .ui_contract import hash_path
from . import notify_tiers as nt
from . import notify_prefs as np

log = logging.getLogger("herdr_attention")

from . import classify as rules
from .config import Settings
from .presence import Presence
from .secrets import Keyring
from .store import Store

PRIORITY = {"min": 1, "low": 2, "default": 3, "high": 4, "urgent": 5}
TAGS = {"approval": "warning", "question": "question", "blocked": "warning", "failure": "x",
        "completion": "white_check_mark", "stall": "hourglass"}


class Sender(Protocol):
    name: str

    def ready(self) -> tuple[bool, str]: ...

    def send(self, *, title: str, message: str, priority: str, tags: list[str], click: str) -> None: ...


class NoSender:
    name = "none"

    def ready(self) -> tuple[bool, str]:
        return False, "phone alerts are off (notify.backend = none)"

    def send(self, **_: object) -> None:
        raise RuntimeError("no notification backend configured")


class DesktopSender:
    """Laptop notification via notify-send, when it is installed.

    With a click link and a notify-send that supports actions, the notification
    gets a default "Open" action. notify-send only reports a click while it
    waits, so that wait runs in a daemon thread with a bounded lifetime and the
    link is opened with xdg-open only when the action is really chosen. Without
    action support (or without a link) it behaves as before.
    """

    name = "desktop"
    ACTION = "default"           # mako/swaync run the default action on a click on the body
    WAIT_SECONDS = 600           # the waiting notify-send is killed after this

    def __init__(self, *, opener: Callable[[str], None] | None = None, wait_seconds: float | None = None):
        self.opener = opener or _xdg_open
        self.wait_seconds = self.WAIT_SECONDS if wait_seconds is None else wait_seconds
        self._actions: bool | None = None

    def ready(self) -> tuple[bool, str]:
        if not shutil.which("notify-send"):
            return False, "notify-send is not installed on this laptop"
        return True, "ready"

    def supports_actions(self) -> bool:
        if self._actions is None:
            try:
                out = subprocess.run(["notify-send", "--help"], capture_output=True, timeout=5, text=True)
                self._actions = "--action" in (out.stdout or "") + (out.stderr or "")
            except (OSError, subprocess.SubprocessError):
                self._actions = False
        return self._actions

    def send(self, *, title: str, message: str, priority: str, tags: list[str], click: str) -> None:
        urgency = "critical" if priority in ("high", "urgent") else "normal"
        cmd = ["notify-send", f"--urgency={urgency}", "--app-name=Herdr attention",
               "--expire-time=15000", title, message]
        if click and click.startswith(("http://", "https://")) and self.supports_actions():
            self._send_with_action(cmd, click)
            return
        try:
            subprocess.run(cmd, check=True, timeout=5, capture_output=True)
        except FileNotFoundError:
            raise RuntimeError("notify-send is not installed on this laptop") from None
        except subprocess.TimeoutExpired:
            raise RuntimeError("notify-send timed out") from None
        except subprocess.CalledProcessError as exc:
            err = (exc.stderr or b"").decode("utf-8", "replace")[:200]
            raise RuntimeError(err or f"notify-send exited {exc.returncode}") from None

    def _send_with_action(self, cmd: list[str], click: str) -> None:
        cmd = cmd[:-2] + [f"--action={self.ACTION}=Open", cmd[-2], cmd[-1]]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    stdin=subprocess.DEVNULL, text=True)
        except FileNotFoundError:
            raise RuntimeError("notify-send is not installed on this laptop") from None
        threading.Thread(target=self._await_click, args=(proc, click), daemon=True,
                         name="desktop-notify-click").start()

    def _await_click(self, proc: subprocess.Popen, click: str) -> None:
        try:
            out, _ = proc.communicate(timeout=self.wait_seconds)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            return
        if proc.returncode == 0 and (out or "").strip().splitlines()[-1:] == [self.ACTION]:
            try:
                self.opener(click)
            except Exception as exc:  # never let a click break the collector
                log.warning("could not open %s: %s", click, exc)


def _xdg_open(url: str) -> None:
    subprocess.Popen(["xdg-open", url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


class NtfySender:
    name = "ntfy"

    def __init__(self, settings: Settings, keyring: Keyring, timeout: float = 10.0):
        self.settings = settings
        self.keyring = keyring
        self.timeout = timeout
        self._token_checked = (0.0, False)

    def _has_token(self) -> bool:
        checked_at, present = self._token_checked
        if time.monotonic() - checked_at > 60:
            present = self.keyring.has("ntfy-token")
            self._token_checked = (time.monotonic(), present)
        return present

    def ready(self) -> tuple[bool, str]:
        if not self.settings.get("notify.enabled", True):
            return False, "phone alerts are switched off"
        if not self.settings.get("notify.ntfy_url") or not self.settings.get("notify.ntfy_topic"):
            return False, "ntfy is not configured yet"
        if not self._has_token():
            return False, "no ntfy token in the keyring (herdr-attention secret set ntfy-token)"
        return True, "ready"

    def send(self, *, title: str, message: str, priority: str, tags: list[str], click: str) -> None:
        token = self.keyring.get("ntfy-token")
        if not token:
            raise RuntimeError("ntfy token missing from keyring")
        body = {
            "topic": self.settings.get("notify.ntfy_topic"),
            "title": title,
            "message": message,
            "priority": PRIORITY.get(priority, 3),
            "tags": tags,
        }
        if click:
            body["click"] = click
        request = urllib.request.Request(
            self.settings.get("notify.ntfy_url").rstrip("/"),
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                if response.status >= 300:
                    raise RuntimeError(f"ntfy answered HTTP {response.status}")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"ntfy answered HTTP {exc.code}") from None
        except urllib.error.URLError as exc:
            raise RuntimeError(f"cannot reach ntfy: {exc.reason}") from None


def make_sender(settings: Settings, keyring: Keyring) -> Sender:
    if settings.get("notify.backend") == "ntfy" and settings.get("notify.enabled", True):
        return NtfySender(settings, keyring)
    return NoSender()


def ntfy_setup(settings: Settings, *, include_secrets: bool, phone_password: str | None = None) -> dict:
    """Links and status for the Settings panel. Topic and password are only
    included when include_secrets is true (an authenticated session).
    Nothing here is logged.
    """
    enabled = bool(settings.get("notify.enabled", True))
    backend = settings.get("notify.backend", "none")
    url = (settings.get("notify.ntfy_url") or "").rstrip("/")
    topic = settings.get("notify.ntfy_topic") or ""
    parsed = urllib.parse.urlparse(url) if url else None
    host = parsed.netloc if parsed else ""
    configured = bool(url and topic and host)
    payload = {
        "enabled": enabled,
        "backend": backend,
        "configured": configured,
        "server": url if url else "",
        "host": host,
        "username": "herdr-phone" if configured else "",
        "dashboard_url": settings.get("notify.dashboard_url") or "",
    }
    if include_secrets and configured:
        payload["topic"] = topic
        payload["deep_link"] = f"ntfy://{host}/{topic}"
        payload["https_link"] = f"{url}/{urllib.parse.quote(topic, safe='')}"
        if phone_password:
            payload["password"] = phone_password
    else:
        # Authenticated but not yet configured, or unauthenticated callers
        # must never receive the topic.
        payload["topic"] = ""
        payload["deep_link"] = ""
        payload["https_link"] = ""
    return payload


def describe(event: dict, names: dict) -> tuple[str, str]:
    """(who, where) for an event: agent name and workspace label only."""
    agent = names.get(event["identity"], {})
    who = agent.get("name") or f"{agent.get('agent') or 'agent'} {event.get('pane_id') or ''}".strip()
    where = agent.get("workspace_label") or event.get("workspace_id") or "?"
    return who, where


class Notifier:
    def __init__(self, settings: Settings, store: Store, presence: Presence, sender: Sender,
                 names: Callable[[], dict], clock: Callable[[], float] = time.time,
                 gate=None, desktop: Sender | None = None,
                 agent_of: Callable[[str], dict | None] | None = None,
                 links_of: Callable[[], list] | None = None):
        self.settings = settings
        self.store = store
        self.presence = presence
        self.sender = sender
        self.desktop = desktop if desktop is not None else DesktopSender()
        self.names = names  # identity -> {"name", "agent", "workspace_label"}
        self.agent_of = agent_of or (lambda _i: None)
        self.links_of = links_of or (lambda: [])
        self.clock = clock
        self._lock = threading.Lock()
        self.last_sent_at: float | None = None
        self.last_error: str | None = None
        self.retry_at = 0.0
        self.failures = 0
        self.sent_count = 0
        self._backfilled = False
        if gate is not None:
            self.gate = gate
        elif store is not None:
            from .notify_gate import NotifyGate
            self.gate = NotifyGate(store, settings, clock)
        else:
            self.gate = None
        self.prepare_gate = None  # set by the collector: event_ids -> {text, links}

    # decision ------------------------------------------------------------------

    def away(self) -> bool:
        mode = self.settings.get("presence.mode", "auto")
        if mode == "always":
            return True
        if mode == "never":
            return False
        return self.presence.state()["counts_as"] == "away"

    def _eligible(self, event: dict) -> str | None:
        """None if the event may be sent, else the state to park it in."""
        if event["resolved_at"] is not None:
            return "expired"
        if event["acked_at"] is not None:
            return "acked"
        if not self.settings.watched(event["workspace_id"]):
            return "unwatched"
        if self.settings.muted(event["workspace_id"]):
            return "muted"
        # 0.9.8.9: quota stops are off unless switched on (Alex: no quota alerts on the phone),
        # also for agents watched closely.
        if self._quota_kind(event) and not np.enabled(self.settings, "quota_stop"):
            return "off"
        if not self.settings.event_enabled(event["type"]) and event["type"] not in ("status", "quota_stop"):
            return "off"
        tier = self._tier_of(event)
        now = self.clock()
        park = nt.allowed_now(self.settings, tier, event.get("identity"), now)
        if park == "off":
            return park
        blocking = np.is_blocking(self.settings, event, tier)
        if blocking:
            return None   # blocking asks push at once: no snooze, no quiet hours
        if park or np.snoozed(self.settings, now) or np.in_quiet_hours(self.settings, now):
            return "quiet"   # parked; looked at again every round, sent once the quiet time ends
        return None

    def tick(self) -> dict | None:
        """Run one decision round. Returns what was sent (for tests/logging)."""
        with self._lock:
            now = self.clock()
            if not self._backfilled:
                self._backfilled = True
                self._backfill_final_tiers()
            outstanding = []
            for event in self.store.events_by_notify_state(["pending", "held", "off", "quiet"]):
                park = self._eligible(event)
                if park:
                    if event["notify_state"] != park:
                        self.store.set_notify_state([event["id"]], park)
                else:
                    outstanding.append(event)
            if not outstanding:
                return None

            # Dashboard-only events are recorded immediately; they never push.
            dashboard, push = [], []
            for event in outstanding:
                if nt.delivery_for(self.settings, self._tier_of(event)) == "dashboard":
                    dashboard.append(event)
                else:
                    push.append(event)
            if dashboard:
                self._store_final_tier(dashboard)
                self.store.set_notify_state([e["id"] for e in dashboard], "dashboard", at=now)
                self.store.log_notification(now, "dashboard", True)
            push, repeats = self._finish_guard(push, now)
            if repeats:
                self._store_final_tier(repeats)
                self.store.set_notify_state([e["id"] for e in repeats], "dashboard", at=now)
                dashboard += repeats
            if not push:
                return {"kind": "dashboard", "title": "", "message": "", "events": len(dashboard)} if dashboard else None

            ntfy, desktop = [], []
            for event in push:
                if nt.delivery_for(self.settings, self._tier_of(event)) == "desktop":
                    desktop.append(event)
                else:
                    ntfy.append(event)

            sent = None
            if ntfy:
                sent = self._deliver_phone(ntfy, now)
            if desktop:
                desk = self._deliver_batch(desktop, self.desktop, now, respect_presence=False)
                sent = desk or sent
            return sent

    def _quota_kind(self, event: dict) -> bool:
        """Quota cards never push the phone: real quota_stop events, and completions whose
        AI title is a limit message (the placement coordinator's 'Weekly limit reached')."""
        if event.get("type") == "quota_stop":
            return True
        from .linking import quota_signal
        title = event.get("title") or ""
        if quota_signal(title):
            return True
        text = self.store.notification_text(event["id"]) if event.get("id") else None
        if text and (quota_signal(text.get("title") or "") or quota_signal(text.get("body") or "")):
            return True
        from .quota_stops import QuotaStops
        return QuotaStops(self.store, self.clock).holds_completion(event.get("identity") or "")

    def _blocking(self, event: dict) -> bool:

        return np.is_blocking(self.settings, event, self._tier_of(event))

    def _deliver_phone(self, events: list[dict], now: float) -> dict | None:
        """v0.9.8.9: per kind "only when I'm away", then the rate limit, then one push.

        Kinds marked "only when I'm away" wait (held) while Alex uses the laptop; presence.mode
        "always" sends them anyway and "never" (Off) holds everything. Over the rate limit only
        blocking asks go; the rest wait and leave together as one digest when the window frees.
        """
        mode = self.settings.get("presence.mode", "auto")
        if mode == "never":
            return self._deliver_batch(events, self.sender, now, respect_presence=True)
        away = self.away()
        go, wait = [], []
        for event in events:
            row = np.row_of(event, self._tier_of(event))
            if away or self._blocking(event) or not np.away_only(self.settings, row):
                go.append(event)
            else:
                wait.append(event)
        held = [e["id"] for e in wait if e["notify_state"] in ("pending", "off", "quiet")]
        if held:
            self.store.set_notify_state(held, "held")
        rate = np.rate(self.settings)
        if go and rate["count"] and self.store.notifications_since(now - rate["minutes"] * 60) >= rate["count"]:
            go = [e for e in go if self._blocking(e)]
        if not go:
            return None
        return self._deliver_batch(go, self.sender, now, respect_presence=False,
                                   bypass_budget=all(self._blocking(e) for e in go))

    # v0.9.8.7: a coordinator that ends a turn every minute or two (reading worker reports) sent
    # "Finished, not reviewed" to the phone each time: 8 alerts in 12 minutes on 19 Nov. A plain
    # finish now waits a settle time (a turn that resumes by then never pushes), and after one
    # finish alert the same agent's next plain finishes within the repeat window are only listed.
    # Questions, approvals, milestones and agents Alex watches closely are never held back by this.
    # Settings notify.finish_settle_seconds (90) and notify.finish_repeat_minutes (20); 0 turns each off.

    def _finish_guard(self, events: list[dict], now: float) -> tuple[list[dict], list[dict]]:
        """(events to push now, plain finishes to list on the dashboard only). Settling ones wait."""
        push, repeats = [], []
        settle = float(self.settings.get("notify.finish_settle_seconds", 90) or 0)
        repeat = float(self.settings.get("notify.finish_repeat_minutes", 30) or 0) * 60
        for event in events:
            identity = event.get("identity") or ""
            # 0.9.8.9: agents watched closely get the guard too (they sent 97 of 137 pushes in a day).
            if event.get("type") != "completion" or self._tier_of(event) != "fyi_finished":
                push.append(event)
                continue
            if now - float(event.get("at") or now) < settle:
                continue   # stays pending; a resumed turn resolves it before it is ever sent
            earlier = repeat and self.store._exec(
                "SELECT 1 FROM events WHERE identity=? AND type='completion' AND notify_state='sent' "
                "AND notified_at>? AND id<>? LIMIT 1", (identity, now - repeat, event["id"])).fetchone()
            (repeats if earlier else push).append(event)
        return push, repeats

    def gate_status(self) -> dict:
        if not self.gate:
            return {"enabled": False, "recent": []}
        return self.gate.status()

    def _tier_of(self, event: dict) -> str:
        if (event.get("evidence") or {}).get("final_tier") in nt.NOTIFY_TIERS:
            return event["evidence"]["final_tier"]
        agent = self.agent_of(event.get("identity") or "") or None
        gate = self.gate.decisions_by_event([event['id']]).get(event['id']) if self.gate else None
        return nt.final_tier(event, gate, self.links_of(), agent=agent, settings=self.settings, now=self.clock())

    def _store_final_tier(self, events: list[dict]) -> None:
        """Decide once: keep the tier an event is sent (or listed) with on the event."""
        for event in events:
            tier = self._tier_of(event)
            if self.store.set_final_tier(event["id"], tier):
                event.setdefault("evidence", {})["final_tier"] = tier

    def _backfill_final_tiers(self) -> None:
        """Older open notices, sent before final_tier existed, get it once."""
        for event in self.store.open_events():
            if (event.get("acked_at") is None and event.get("notify_state") in nt.NOTICE_STATES
                    and not (event.get("evidence") or {}).get("final_tier")):
                self._store_final_tier([event])

    def _deliver_batch(self, events: list[dict], sender: Sender, now: float, *,
                       respect_presence: bool, bypass_budget: bool = False) -> dict | None:
        """Send one delivery group. None means wait (gate or presence or budget)."""
        if respect_presence and not self.away():
            pending = [e["id"] for e in events if e["notify_state"] in ("pending", "off", "quiet")]
            if pending:
                self.store.set_notify_state(pending, "held")
            return None
        # Desktop delivery still pauses when presence.mode is 'never'.
        if not respect_presence and self.settings.get("presence.mode", "auto") == "never":
            pending = [e["id"] for e in events if e["notify_state"] in ("pending", "off", "quiet")]
            if pending:
                self.store.set_notify_state(pending, "held")
            return None
        ready, why = sender.ready()
        if not ready:
            self.store.set_notify_state([e["id"] for e in events], "no_backend", error=why)
            return None
        if now < self.retry_at:
            return None
        budget = int(self.settings.get("notify.max_per_10min", 8)) - self.store.notifications_since(now - 600)
        if budget <= 0 and not bypass_budget:
            return None
        message = self._gated_or_plain(events)
        if message is None:
            return None
        ids = [e["id"] for e in events]
        self._store_final_tier(events)
        self.store.save_notification_text(ids, message["title"], message["message"],
                                          "ai" if message.get("kind") == "gated" or message.get('ai_text') else "template", now)
        canonical = self.store.notification_text(ids[0])
        message["title"], message["message"] = canonical["title"], canonical["body"]
        try:
            sender.send(**{k: message[k] for k in ("title", "message", "priority", "tags", "click")})
        except Exception as exc:
            self.failures += 1
            self.last_error = str(exc)
            self.retry_at = now + min(600, 30 * 2 ** (self.failures - 1))
            self.store.log_notification(now, message["kind"], False)
            hold = "pending" if (not respect_presence or self.away()) else "held"
            self.store.set_notify_state([e["id"] for e in events], hold, error=self.last_error)
            return None
        self.failures = 0
        self.last_error = None
        self.last_sent_at = now
        self.sent_count += 1
        self.store.log_notification(now, message["kind"], True)
        state = "sent" if message["kind"] in ("single", "gated") else "summarized"
        self.store.set_notify_state([e["id"] for e in events], state, at=now)
        return message

    def _gated_or_plain(self, outstanding: list[dict]) -> dict | None:
        """Run (or reuse) the AI gate when it is on. None means 'wait, job queued'
        or 'skipped'. A dict is the notification to send (AI or plain)."""
        plain = lambda ev=outstanding: self._compose(ev)
        modes = {nt.effective_gate(self.settings, self._tier_of(e)) for e in outstanding}
        # If no event needs an AI check, send plain text now.
        if "check" not in modes and "always" not in modes:
            return plain()
        if not self.gate or not self.gate.enabled:
            return plain()
        ids = [e["id"] for e in outstanding]
        existing = self.gate.decision_for(ids)
        if existing:
            return self._message_from_decision(outstanding, existing, plain)
        if self.gate.has_in_flight(ids):
            return None
        tier_names = sorted({self._tier_of(e) for e in outstanding})
        self.gate.queue(outstanding, "", tiers=tier_names)
        self.store.set_notify_state(ids, "gating")
        self.gate.wake.set()
        return None

    def _message_from_decision(self, outstanding, decision, plain) -> dict | None:
        must_send, may_skip = [], []
        for event in outstanding:
            gate = nt.effective_gate(self.settings, self._tier_of(event))
            if gate in ("always", "off"):
                must_send.append(event)
            else:
                may_skip.append(event)
        if decision.get("decision") == "skip":
            if may_skip:
                self.store.set_notify_state([e["id"] for e in may_skip], "gated_skip", at=self.clock())
            if not must_send:
                return None
            return self._compose(must_send)
        if decision.get("decision") == "send" and decision.get("title"):
            message = self._compose(outstanding)
            message["title"] = decision["title"]
            message["message"] = decision.get("body") or message["message"]
            message["kind"] = "gated" if message.get("kind") == "single" else message.get("kind")
            message['ai_text'] = True
            return message
        return self._compose(outstanding)

    def finish_gate_job(self, job: dict) -> dict | None:
        """Called by the collector worker. Delivers or parks the result."""
        decision = self.gate.run_job(job, self.prepare_gate)
        if not decision:
            return None
        ids = decision["event_ids"] if isinstance(decision["event_ids"], list) else []
        events = [e for e in self.store.events_by_notify_state(["gating", "pending", "held"]) if e["id"] in set(ids)]
        if not events:
            return decision
        # Re-apply eligibility; if no longer sendable, park.
        with self._lock:
            still = []
            for event in events:
                park = self._eligible(event)
                if park:
                    self.store.set_notify_state([event["id"]], park)
                else:
                    still.append(event)
            if not still:
                return decision
            if decision.get("decision") == "skip":
                skip, keep = [], []
                for event in still:
                    if nt.effective_gate(self.settings, self._tier_of(event)) in ("always", "off"):
                        keep.append(event)
                    else:
                        skip.append(event)
                if skip:
                    self.store.set_notify_state([e["id"] for e in skip], "gated_skip", at=self.clock())
                if keep:
                    state = "pending" if self.away() else "held"
                    self.store.set_notify_state([e["id"] for e in keep], state)
                return decision
            # Leave as pending/held so tick() sends with the stored decision.
            state = "pending" if self.away() else "held"
            self.store.set_notify_state([e["id"] for e in still], state)
            self.gate.wake.set()
        return decision

    # message ------------------------------------------------------------------

    def _compose(self, events: list[dict]) -> dict:
        names = self.names()
        click = self.settings.get("notify.dashboard_url", "") or ""
        if len(events) == 1:
            event = events[0]
            who, where = describe(event, names)
            label = rules.LABELS.get(event["type"], event["type"])
            tier = self._tier_of(event)
            tier_label = rules.LABELS['quota_stop'] if event['type'] == 'quota_stop' else nt.TIER_LABELS.get(tier, tier)
            late = " (seen late)" if event["seen_late"] else ""
            return {
                "kind": "single",
                "title": f"{tier_label}: {who}",
                "message": f"{who} · {where} · {label}{late}",
                "priority": np.ntfy_priority(self.settings, np.row_of(event, tier)),
                "tags": [TAGS.get(event["type"], "bell")],
                "click": click + hash_path("notification", event["id"]) if click else "",
                "tier": tier,
            }

        events = sorted(events, key=lambda e: (rules.sort_key(e["type"]) if e["type"] in getattr(rules, "KIND_ORDER", ()) else 99, e["at"]))
        limit = int(self.settings.get("notify.summary_max_items", 5))
        lines = []
        for event in events[:limit]:
            who, where = describe(event, names)
            late = " (seen late)" if event["seen_late"] else ""
            tier_label = nt.TIER_LABELS.get(self._tier_of(event), self._tier_of(event))
            lines.append(f"{tier_label}: {who} · {where}{late}")
        if len(events) > limit:
            lines.append(f"+{len(events) - limit} more")
        top = max(PRIORITY[np.ntfy_priority(self.settings, np.row_of(e, self._tier_of(e)))] for e in events)
        priority = next(name for name, value in PRIORITY.items() if value == top)
        return {
            "kind": "summary",
            "title": nt.batch_title(self._tier_of(e) for e in events),
            "message": "\n".join(lines),
            "priority": priority,
            "tags": ["bell"],
            # A batch opens the Notifications view on its newest event.
            "click": click + hash_path("notification", max(events, key=lambda e: (e["at"], e["id"]))["id"]) if click else "",
        }

    def status(self) -> dict:
        ready, why = self.sender.ready()
        mode = self.settings.get("presence.mode", "auto")
        delay = self.settings.get("presence.phone_delay_minutes", 5)
        q, rate = np.quiet(self.settings), np.rate(self.settings)
        now = self.clock()
        lead = "Phone alerts paused" if mode == "never" else "Phone alerts are on" if ready else "Phone alerts are not set up"
        parts = [lead]
        if mode != "never":
            view = np.public_view(self.settings, now)
            if view["snooze_label"]:
                parts.append(view["snooze_label"].lower().replace("snoozed", "snoozed (questions still come)", 1))
            if mode == "always":
                parts.append("sent even while you use the laptop")
            else:
                parts.append(f"calm ones after {delay} min away" + ("" if self.away() else " (you're at the laptop now)"))
            parts.append(f"quiet {q['start']}–{q['end']}" if q["on"] else "no quiet hours")
            if rate["count"]:
                parts.append(f"at most {rate['count']} per {rate['minutes']} min")
        status_line = " · ".join(parts)
        return {
            "status_line": status_line,
            "backend": self.sender.name,
            "ready": ready,
            "detail": why,
            "away": self.away(),
            "mode": self.settings.get("presence.mode"),
            "last_sent_at": self.last_sent_at,
            "last_error": self.last_error,
            "held": len(self.store.events_by_notify_state(["held"])),
            "gate_enabled": bool(self.gate and self.gate.enabled),
        }

    def announce_visuals(self, items: list[dict]) -> dict | None:
        """Optional phone push for new screenshots. Off by default; respects Phone alerts."""
        from . import notify_prefs as np
        from .ui_contract import hash_path
        if not items or not np.enabled(self.settings, "visuals"):
            return None
        now = self.clock()
        if self.settings.get("presence.mode", "auto") == "never":
            return None
        if np.snoozed(self.settings, now) or np.in_quiet_hours(self.settings, now):
            return None
        if np.away_only(self.settings, "visuals") and not self.away():
            return None
        budget = int(self.settings.get("notify.max_per_10min", 8)) - self.store.notifications_since(now - 600)
        if budget <= 0:
            return None
        rate = np.rate(self.settings)
        if rate["count"] and self.store.notifications_since(now - rate["minutes"] * 60) >= rate["count"]:
            return None
        ready, why = self.sender.ready()
        if not ready:
            return None
        # One id is recorded so a later ingest of the same files does not push again.
        already = {part for part in (self.store.get_meta("visuals_pushed_ids") or "").split(",") if part}
        fresh = []
        for item in items:
            if self.settings.muted(item.get("workspace_id")):
                continue
            if item["id"] in already:
                continue
            fresh.append(item)
        if not fresh:
            return None
        first = fresh[0]
        if len(fresh) == 1:
            title = f"New visual · {first.get('name') or 'image'}"
            message = " · ".join(p for p in (first.get("agent"), first.get("relative_path") or first.get("name")) if p)
        else:
            title = f"{len(fresh)} new visuals"
            message = ", ".join(i.get("name") or "image" for i in fresh[:4])
        click = (self.settings.get("notify.dashboard_url") or "") + hash_path("visuals")
        self.sender.send(title=title, message=message, priority=np.ntfy_priority(self.settings, "visuals"),
                         tags=["camera"], click=click)
        self.store.log_notification(now, "visuals", True)
        keep = list(already)[-80:] + [i["id"] for i in fresh]
        self.store.set_meta("visuals_pushed_ids", ",".join(keep[-120:]))
        return {"kind": "visuals", "title": title, "message": message, "events": len(fresh)}

    def send_test(self) -> None:
        ready, why = self.sender.ready()
        if not ready:
            raise RuntimeError(why)
        self.sender.send(
            title="Waiting on you (test)",
            message="This is a test of the new notification-tier format. No agent data.\n\nWaiting on you · Phone (ntfy)",
            priority="default", tags=["test_tube"],
            click=base + hash_path("notifications") if (base := self.settings.get("notify.dashboard_url", "") or "") else "")
