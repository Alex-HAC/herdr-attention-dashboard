"""Observed state and event history.

The engine is fed Herdr data (snapshots, live events, refreshed agent info)
and turns state changes into events. It never talks to Herdr itself; the
collector does the I/O and calls in here. All methods are thread-safe.

Rules that keep alerts honest:

* Agents are identified by their Herdr session id, not the pane id.
* Timestamps come from the collector clock at the moment it sees a change.
  A state that was already there when we first looked has an unknown start
  (``since_known = False``).
* One event per episode: entering an alert state creates an event; staying
  in it does not. The event is resolved when the state is left.
* After a restart or reconnect, Herdr's global ``state_change_seq`` tells us
  whether anything changed while we were away. Unchanged agents produce no
  new events. Changes found this way are marked ``seen_late``.
"""

from __future__ import annotations

import os
import hashlib
import threading
import time
from typing import Callable

from . import attention as tiers
from . import notify_tiers as nt
from . import classify as rules
from . import flow
from . import metrics
from .config import Settings
from .store import Store
from .content import agent_name, event_label

AGENT_FIELDS = ("pane_id", "workspace_id", "tab_id", "name", "agent", "session_identity", "session_collision",
                "workspace_order", "tab_order", "pane_order", "number")
# Stall evidence. Reset whenever it can no longer be trusted (status change,
# read error, collector restart, Herdr disconnect).
OUTPUT_FIELDS = ("output_hash", "output_checked_at", "output_changed_at", "output_first_checked_at",
                 "output_unchanged_checks", "output_error", "output_error_at")


def _summary_compact(row: dict | None, event: dict | None) -> dict | None:
    """What list views need of the latest AI summary."""
    if not row:
        return None
    summary = row.get("summary") or {}
    return {
        "event_id": row.get("event_id"),
        "id": row["id"], "status": row["status"], "mode": row["mode"],
        "at": row.get("finished_at") or row.get("requested_at"),
        "headline": summary.get("headline"), "needs_you": summary.get("needs_you"),
        "asks": summary.get("asks") or [],   # AI wording for question items (asks.py checks every quote)
        "error": row.get("error"), "model": row.get("model"), "effort": row.get("effort"),
        "resolved_model": row.get("resolved_model"),
        "source_from": row.get("source_from"), "source_to": row.get("source_to"),
        # A summary made for an earlier item is shown as older context.
        "current": bool(event) and row.get("event_id") == event["id"],
    }


def _number(item: dict) -> int:
    """Herdr's display number; items without one sort last."""
    number = item.get("number")
    return number if isinstance(number, int) else 10**6


def _home_short(path: str) -> str:
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path == home or path.startswith(home + "/") else path


def identity_of(info: dict) -> str:
    if info.get("_attention_identity"):
        return info["_attention_identity"]
    session = info.get("agent_session") or {}
    value = session.get("value")
    if value:
        return f"{session.get('agent') or info.get('agent') or 'agent'}:{value}"
    return f"term:{info.get('terminal_id') or info.get('pane_id')}"


class Engine:
    def __init__(self, settings: Settings, store: Store, clock: Callable[[], float] = time.time):
        self.settings = settings
        self.store = store
        self.clock = clock
        self._lock = threading.RLock()
        self.changed = threading.Condition(self._lock)
        self.version = 0
        self.agents: dict[str, dict] = store.load_agents()
        for agent in self.agents.values():
            agent["confirmed"] = False  # until the first snapshot sees it
            for key in OUTPUT_FIELDS:
                agent.pop(key, None)
            # Time while the collector was off is not known to be working time.
            if agent.get("working_started") is not None:
                agent["working_started"] = None
                agent["active_start_unknown"] = True
        self.panes: dict[str, str] = {}
        self.workspaces: dict[str, dict] = {}
        self.tabs: dict[str, dict] = {}
        self.started_at = clock()
        self.herdr = {"connected": False, "last_contact": None, "version": None, "error": None,
                      "last_reconcile": None, "reconcile_error": None, "reconcile_failures": 0}
        self.snapshots_taken = 0
        self.relations = None   # relations.Registry, set by the collector
        self.inferences = None
        self.summaries = None   # summarize.Summarizer, set by the collector

    # helpers ------------------------------------------------------------------

    def _bump(self) -> None:
        self.version += 1
        self.changed.notify_all()

    def _save(self, agent: dict) -> None:
        data = {k: v for k, v in agent.items() if k not in ("confirmed",)}
        self.store.save_agent(agent["identity"], data, self.clock())

    def watched(self, workspace_id: str | None) -> bool:
        return self.settings.watched(workspace_id)

    # Herdr connection status --------------------------------------------------

    def set_herdr_status(self, connected: bool, *, version: str | None = None, error: str | None = None) -> None:
        with self._lock:
            now = self.clock()
            self.herdr.update({"connected": connected, "error": error})
            if not connected:
                for agent in self.agents.values():   # a gap breaks the stall evidence
                    for key in OUTPUT_FIELDS:
                        agent.pop(key, None)
            if connected:
                self.herdr["last_contact"] = now
            if version:
                self.herdr["version"] = version
            self._bump()

    def poke(self) -> None:
        """Wake stream readers (something outside the engine changed)."""
        with self._lock:
            self._bump()

    def reconcile_status(self, error: str | None = None, failures: int = 0) -> None:
        with self._lock:
            self.herdr.update(reconcile_error=error, reconcile_failures=failures)
            if error is None:
                self.herdr["last_reconcile"] = self.clock()
                self.herdr["last_contact"] = self.clock()
            self._bump()

    def touch_herdr(self) -> None:
        with self._lock:
            self.herdr["last_contact"] = self.clock()

    # workspaces ----------------------------------------------------------------

    def set_workspaces(self, workspaces: list[dict]) -> None:
        with self._lock:
            self.workspaces = {
                w["workspace_id"]: {"id": w["workspace_id"], "label": w.get("label") or w["workspace_id"],
                                    "number": w.get("number")}
                for w in workspaces if w.get("workspace_id")
            }
            self._bump()

    def set_tabs(self, tabs: list[dict]) -> None:
        with self._lock:
            self.tabs = {
                t["tab_id"]: {"id": t["tab_id"], "label": t.get("label") or "", "number": t.get("number"),
                              "workspace_id": t.get("workspace_id")}
                for t in tabs if t.get("tab_id")
            }
            self._bump()

    def rename_workspace(self, workspace_id: str, label: str) -> None:
        with self._lock:
            if workspace_id in self.workspaces:
                self.workspaces[workspace_id]["label"] = label
                self._bump()

    def watched_pane_ids(self) -> list[str]:
        with self._lock:
            return sorted(p for p, ident in self.panes.items()
                          if self.agents.get(ident, {}).get("present") and self.watched(self.agents[ident].get("workspace_id")))

    # applying Herdr data ----------------------------------------------------------

    def _prepare_identities(self, incoming, *, full):
        """Keep a known session owner stable; qualify additional live pane claims.

        A qualified identity remains bound to its terminal across later scans,
        moves and collector restarts, even after its duplicate disappears.
        """
        def token(info):
            return info.get('terminal_id') or info.get('pane_id')
        pool = list(incoming)
        if not full:
            panes = {i.get('pane_id') for i in incoming}
            terminals = {i.get('terminal_id') for i in incoming if i.get('terminal_id')}
            for a in self.agents.values():
                if a.get('present') and a.get('pane_id') not in panes and a.get('terminal_id') not in terminals:
                    base = a.get('session_identity') or a['identity'].split('@', 1)[0]
                    kind, _, value = base.partition(':')
                    pool.append({**a, 'agent_session': {'agent': kind, 'value': value}})
        groups = {}
        for info in pool:
            if info.get('agent'):
                groups.setdefault(identity_of(info), []).append(info)
        owners = {}
        for base, claimants in groups.items():
            old = self.agents.get(base, {})
            known = next((i for i in claimants if token(i) == token(old)), None)
            owners[base] = token(known or min(claimants, key=lambda i: str(token(i))))
            for a in self.agents.values():
                if (a.get('session_identity') or a['identity'].split('@', 1)[0]) == base:
                    a['session_collision'] = len(claimants) > 1
        out = []
        for original in incoming:
            info = dict(original)
            base = identity_of(info)
            claimants = groups.get(base, [])
            previous = next((a for a in self.agents.values()
                             if (a.get('session_identity') or a['identity'].split('@', 1)[0]) == base
                             and token(a) == token(info)), None)
            identity = previous['identity'] if previous else base
            if len(claimants) > 1 and token(info) != owners[base]:
                suffix = str(token(info))
                if sum(token(i) == token(info) for i in claimants) > 1:
                    suffix += '-' + info['pane_id'].replace(':', '_')
                identity = base + '@' + suffix
            info.update(_attention_identity=identity, session_identity=base, session_collision=len(claimants) > 1)
            out.append(info)
        return out

    def apply_snapshot(self, agents: list[dict], workspaces: list[dict] | None = None) -> list[tuple[str, str]]:
        """Reconcile with a full agent list. Returns [(identity, pane_id)] needing explain."""
        with self._lock:
            now = self.clock()
            agents = self._prepare_identities(agents, full=True)
            if workspaces is not None:
                self.set_workspaces(workspaces)
            self.snapshots_taken += 1
            seen: set[str] = set()
            needs_explain = []
            # Detach sessions that moved before processing any replacement in
            # their old pane; input ordering must not retire a still-live session.
            for info in agents:
                identity = identity_of(info)
                old_pane = self.agents.get(identity, {}).get("pane_id")
                if old_pane != info.get("pane_id") and self.panes.get(old_pane) == identity:
                    self.panes.pop(old_pane, None)
            for info in agents:
                if not info.get("agent") or not self.watched(info.get("workspace_id")):
                    continue
                identity = identity_of(info)
                seen.add(identity)
                if self._apply(info, now, live=False):
                    needs_explain.append((identity, info["pane_id"]))
            for identity, agent in list(self.agents.items()):
                if identity in seen:
                    continue
                if not self.watched(agent.get("workspace_id")):
                    # Workspace switched off: forget quietly, no events.
                    del self.agents[identity]
                    continue
                if agent.get("present"):
                    self._gone(agent, "not running any more (found on resync)", now, late=True)
            self.panes = {a["pane_id"]: i for i, a in self.agents.items()
                          if i in seen and a.get("present")}
            self._bump()
            return needs_explain

    def apply_agent(self, info: dict) -> bool:
        """Apply fresh info for one agent after a live event. Returns True if explain is needed."""
        with self._lock:
            if not info.get("agent") or not self.watched(info.get("workspace_id")):
                identity = self.panes.pop(info.get("pane_id"), None)
                if identity and identity in self.agents:
                    del self.agents[identity]
                    self._bump()
                return False
            info = self._prepare_identities([info], full=False)[0]
            needs = self._apply(info, self.clock(), live=True)
            self._bump()
            return needs

    def _apply(self, info: dict, now: float, *, live: bool) -> bool:
        identity = identity_of(info)
        pane_id = info.get("pane_id")
        previous = self.panes.get(pane_id)
        if previous and previous != identity and previous in self.agents:
            old = self.agents[previous]
            if previous.startswith("term:") and old.get("terminal_id") == info.get("terminal_id"):
                # Herdr learned the session id of the same agent: keep its history.
                self.store.rename_identity(previous, identity)
                old["identity"] = identity
                self.agents[identity] = self.agents.pop(previous)
            else:
                self._gone(old, "replaced by a new session in the same pane", now, late=False, failure=False)

        agent = self.agents.get(identity)
        is_new = agent is None
        if is_new:
            agent = {"identity": identity, "first_seen": now, "status": None, "seq": None,
                     "status_since": None, "since_known": False}
            self.agents[identity] = agent
        self.panes[pane_id] = identity

        was_gone = not agent.get("present", True) if not is_new else False
        for key in AGENT_FIELDS:
            agent[key] = info.get(key)
        agent["terminal_id"] = info.get("terminal_id")
        title = info.get("terminal_title_stripped") or info.get("terminal_title") or ""
        title_changed = not is_new and title != agent.get("title")
        agent["title"] = title
        if agent.get('name'):
            agent['name'] = agent_name(agent)
        agent["cwd"] = info.get("foreground_cwd") or info.get("cwd") or ""
        agent["present"] = True
        agent["confirmed"] = True
        agent["last_seen"] = now
        for key in ("gone_at", "gone_reason", "gone_status"):
            agent.pop(key, None)

        status = info.get("agent_status") or "unknown"
        if status == 'working':
            agent.pop('dismissed_episode', None)
        seq = info.get("state_change_seq")
        changed = status != agent.get("status") or was_gone
        # A change we watched happen has a known start. One found in a
        # snapshot (first look, or after a gap) does not, and its events are
        # marked seen_late so they go into a bounded summary.
        late = changed and not live
        if status != agent.get("status"):
            agent["status_since"] = now
            agent["since_known"] = live
        elif not live and None not in (seq, agent.get("seq")) and seq != agent.get("seq"):
            # Same status, but it changed and came back while we were away.
            agent["missed_changes"] = agent.get("missed_changes", 0) + 1
        agent["seq"] = seq
        previous_status = agent.get("status")
        agent["status"] = status
        if status != previous_status or was_gone:
            self._end_working(agent, now)
        if status == "working" and agent.get("working_started") is None:
            agent["working_started"] = now
            if not (live and status != previous_status):
                agent["active_start_unknown"] = True   # it was already working when we looked

        # Timeline. Changes watched live also count as activity; ones found in a
        # snapshot happened at an unknown time and only go into the log.
        if is_new:
            self.store.add_activity(at=now, identity=identity, kind="seen", status=status, title=title, live=False)
        else:
            if status != previous_status or was_gone:
                self.store.add_activity(at=now, identity=identity, kind="status", status=status, title=title, live=live)
            if title_changed:
                self.store.add_activity(at=now, identity=identity, kind="title", status=status, title=title, live=live)
            if live and (status != previous_status or title_changed):
                agent["last_activity_at"] = now

        needs_explain = False
        if status == "blocked":
            if changed or "blocked_rule" not in agent:
                agent["blocked_rule"] = None
                agent["explain_pending"] = True
                needs_explain = True
        else:
            agent.pop("blocked_rule", None)
            agent.pop("explain_pending", None)
        if status != "working" or changed:
            for key in OUTPUT_FIELDS:
                agent.pop(key, None)

        if not is_new and (status != previous_status or was_gone):
            self._maybe_record_status_event(agent, previous_status, now, live=live)
        self._evaluate(agent, now, late=late)
        self._save(agent)
        return needs_explain

    # traces ------------------------------------------------------------------

    def trace_targets(self) -> list[str]:
        """Identities whose traces may be read: present agents in watched workspaces."""
        with self._lock:
            return [a["identity"] for a in self.agents.values()
                    if a.get("present") and self.watched(a.get("workspace_id"))]

    def set_trace(self, identity: str, summary: dict) -> None:
        with self._lock:
            agent = self.agents.get(identity)
            if not agent or not self.watched(agent.get("workspace_id")):
                return
            old = agent.get("trace") or {}
            agent["trace"] = summary
            significant = {k: v for k, v in summary.items() if k != "read_at"}
            if significant != {k: v for k, v in old.items() if k != "read_at"}:
                self._save(agent)
                self._bump()

    def messages_of(self, identity: str) -> list[dict]:
        """The agent's messages since Alex last wrote (earlier ones, then the last), for checking quotes."""
        with self._lock:
            agent = self.agents.get(identity) or {}
            last = agent.get("last_assistant") or {}
            out = list(agent.get("earlier_messages") or [])
            if agent.get("last_message"):
                out.append({"text": agent["last_message"], "at": last.get("at")})
            return out

    def agent_names(self) -> set[str]:
        with self._lock:
            return {a.get("name") for a in self.agents.values() if a.get("name")}

    def set_last_assistant(self, identity: str, text: str | None, at: float | None = None,
                           last_role: str | None = None, earlier: list[dict] | None = None,
                           alex_at: float | None = None) -> None:
        with self._lock:
            agent = self.agents.get(identity)
            if not agent or not self.watched(agent.get("workspace_id")):
                return
            previous = (agent.get('last_assistant'), agent.get('last_message'), agent.get('earlier_messages'))
            agent["last_assistant"] = {
                "text": (text or "")[:2000],
                "at": at,
                "last_role": last_role or "assistant",
            }
            # The whole message, for the question items (asks.py) and the tier rule; not in the view.
            from .asks import MAX_ORIGINAL
            agent["last_message"] = (text or "")[:MAX_ORIGINAL]
            # v0.9.8.1: earlier messages since Alex last wrote (asks.earlier_items); not in the view.
            # Only messages with an ask by the rules are kept, and they stay when they scroll out of
            # the read window, until Alex writes to the agent (alex_at: his last turn seen).
            from .asks import extract
            kept = [m for m in agent.get("earlier_messages") or []
                    if (alex_at is None or (m.get("at") or 0) > alex_at) and extract(m.get("text") or "")["state"] == "questions"]
            have = {m.get("at") for m in kept}
            for m in earlier or []:
                body = (m.get("text") or "")[:8000]
                if m.get("at") not in have and body.strip() and extract(body)["state"] == "questions":
                    kept.append({"text": body, "at": m.get("at")})
                    have.add(m.get("at"))
            agent["earlier_messages"] = sorted(kept, key=lambda m: m.get("at") or 0)[-8:]
            if previous != (agent['last_assistant'], agent['last_message'], agent['earlier_messages']):
                self._save(agent)
                self._bump()

    def set_blocked_rule(self, identity: str, rule: str | None, prompt: str = "") -> None:
        with self._lock:
            agent = self.agents.get(identity)
            if not agent or agent.get("status") != "blocked":
                return
            agent["blocked_rule"] = rule
            agent["prompt_text"] = prompt
            agent["explain_pending"] = False
            self._evaluate(agent, self.clock(), late=not agent.get("since_known", False))
            self._save(agent)
            self._bump()

    def pane_gone(self, pane_id: str, reason: str) -> None:
        with self._lock:
            identity = self.panes.pop(pane_id, None)
            agent = self.agents.get(identity) if identity else None
            if agent and agent.get("present"):
                self._gone(agent, reason, self.clock(), late=False)
                self._bump()

    @staticmethod
    def _end_working(agent: dict, now: float) -> None:
        started = agent.get("working_started")
        if started is not None:
            agent["active_seconds"] = float(agent.get("active_seconds") or 0) + max(0.0, now - started)
            agent["working_started"] = None

    def _gone(self, agent: dict, reason: str, now: float, *, late: bool, failure: bool = True) -> None:
        self._end_working(agent, now)
        agent["present"] = False
        agent["gone_at"] = now
        agent["gone_reason"] = reason
        agent["gone_status"] = agent.get("status") if failure else None
        self.store.add_activity(at=now, identity=agent["identity"], kind="gone", status=agent.get("status"),
                                title=reason, live=not late)
        if self.panes.get(agent.get("pane_id")) == agent["identity"]:
            self.panes.pop(agent.get("pane_id"), None)
        self._evaluate(agent, now, late=late)
        self._save(agent)

    # output change tracking ----------------------------------------------------------

    def output_targets(self) -> list[tuple[str, str]]:
        poll = self.settings.get("stall.output_poll_seconds", 0)
        if not poll:
            return []
        with self._lock:
            now = self.clock()
            return [(a["identity"], a["pane_id"]) for a in self.agents.values()
                    if a.get("present") and a.get("status") == "working" and self.watched(a.get("workspace_id"))
                    and now - (a.get("output_checked_at") or 0) >= poll]

    def record_report_output(self, identity: str, fingerprint: str) -> None:
        """Content-only report freshness, independent of attention classification."""
        with self._lock:
            agent = self.agents.get(identity)
            if agent and agent.get('report_output_hash') != fingerprint:
                agent['report_output_hash'] = fingerprint
                self._save(agent)
                self._bump()

    def record_output(self, identity: str, fingerprint: str) -> None:
        with self._lock:
            agent = self.agents.get(identity)
            if not agent or agent.get("status") != "working":
                return
            now = self.clock()
            agent.pop("output_error", None)
            agent.pop("output_error_at", None)
            if agent.get("output_hash") is None:
                agent["output_first_checked_at"] = now
                agent["output_unchanged_checks"] = 1
            elif agent["output_hash"] != fingerprint:
                agent["output_changed_at"] = now
                agent["output_unchanged_checks"] = 1
                agent["last_activity_at"] = now
            else:
                agent["output_unchanged_checks"] = agent.get("output_unchanged_checks", 1) + 1
            agent["output_hash"] = fingerprint
            agent["output_checked_at"] = now
            self._evaluate(agent, now, late=False)
            self._save(agent)
            self._bump()

    def record_output_error(self, identity: str, reason: str) -> None:
        """A failed output check: evidence is unavailable, the baseline starts over."""
        with self._lock:
            agent = self.agents.get(identity)
            if not agent or agent.get("status") != "working":
                return
            now = self.clock()
            for key in OUTPUT_FIELDS:
                agent.pop(key, None)
            agent["output_error"] = reason
            agent["output_error_at"] = now
            agent["output_checked_at"] = now     # do not hammer a failing read every tick
            self._evaluate(agent, now, late=False)
            self._save(agent)
            self._bump()

    def tick(self) -> None:
        """Re-check time-based rules (possible stalls) and bank active time."""
        with self._lock:
            now = self.clock()
            changed = False
            for agent in self.agents.values():
                if agent.get("present") and agent.get("status") == "working":
                    if agent.get("working_started") is not None:
                        self._end_working(agent, now)
                        agent["working_started"] = now
                        if now - agent.get("active_saved_at", 0) >= 60:   # keep banked time across restarts
                            agent["active_saved_at"] = now
                            self._save(agent)
                    before = agent.get("kind")
                    self._evaluate(agent, now, late=False)
                    changed |= agent.get("kind") != before
            if changed:
                self._bump()

    # events ------------------------------------------------------------------

    def _evaluate(self, agent: dict, now: float, *, late: bool) -> None:
        attention = rules.classify(agent, now, self.settings)
        agent["attention"] = attention.as_dict()
        agent["kind"] = attention.kind
        identity = agent["identity"]

        open_events = self.store.open_events(identity)
        for event in open_events:
            if event["type"] != attention.kind and event['type'] != 'quota_stop':
                self.store.resolve_event(event["id"], now)

        if attention.kind not in rules.ALERT_KINDS or not self.watched(agent.get("workspace_id")):
            return
        if agent.get("explain_pending"):
            return  # wait until we know whether it is an approval or a question
        if any(e["type"] == attention.kind for e in open_events):
            return  # same episode, already recorded
        # v0.9.8.10: a quota banner already raised (or acked) for this limit must not come back
        # as a new "Finished" every time Herdr flips done/working.
        if attention.kind == "completion":
            from .quota_stops import QuotaStops
            if QuotaStops(self.store, self.clock).holds_completion(identity):
                return

        workspace = agent.get("workspace_id")
        evidence = dict(attention.evidence or {})
        evidence["notify_tier"] = self._notify_tier_for(agent, now, kind=attention.kind)
        tasks = self.relations.state().labels.get((identity, 'task'), {}) if self.relations else {}
        if tasks:
            evidence['task'] = next(iter(tasks))
        if not self.settings.event_enabled(attention.kind):
            notify_state = "off"
        elif self.settings.muted(workspace):
            notify_state = "muted"
        else:
            notify_state = "pending"
        event_id = self.store.add_event(
            at=now, identity=identity, workspace_id=workspace, pane_id=agent.get("pane_id"),
            type=attention.kind, evidence=evidence, confidence=attention.confidence,
            seen_late=late, notify_state=notify_state, title=agent.get("title") or None,
        )
        if agent.get('dismissed_episode') == self._episode_key(agent, attention.kind):
            self.store.ack_event(event_id, now)
            self.store.set_notify_state([event_id], 'acked')

    @staticmethod
    def _episode_key(agent, kind):
        return [kind, agent.get('title') or '', agent.get('blocked_rule'), agent.get('prompt_text')]

    def _notify_links(self, identity: str) -> list[dict]:
        visible = {i for i, a in self.agents.items()
                   if a.get("present") and self.watched(a.get("workspace_id"))}
        visible.add(identity)
        links = self._links(visible)
        self._name_links(links, [
            {"identity": i, "name": a.get("name"), "title": a.get("title"), "pane_id": a.get("pane_id")}
            for i, a in self.agents.items()
        ])
        return links

    def _notify_tier_for(self, agent: dict, now: float, *, kind: str | None = None) -> str:
        event = {"type": kind or agent.get("kind"), "identity": agent.get("identity"),
                 "title": agent.get("title"), "evidence": (agent.get("attention") or {}).get("evidence") or {}}
        return nt.classify_event(event, agent, settings=self.settings,
                                 links=self._notify_links(agent.get("identity") or ""), now=now)

    def _maybe_record_status_event(self, agent: dict, previous: str | None, now: float, *, live: bool) -> None:
        """When the verbose tier (or a close-watch pin) is on, record a live status change."""
        if not live or previous is None:
            return
        identity = agent.get("identity") or ""
        if not nt.verbose_enabled(self.settings, identity):
            return
        if not self.watched(agent.get("workspace_id")):
            return
        workspace = agent.get("workspace_id")
        if self.settings.muted(workspace):
            notify_state = "muted"
        else:
            notify_state = "pending"
        evidence = {"notify_tier": "everything", "from": previous, "to": agent.get("status")}
        self.store.add_event(
            at=now, identity=identity, workspace_id=workspace, pane_id=agent.get("pane_id"),
            type="status", evidence=evidence, confidence="high",
            seen_late=False, notify_state=notify_state, title=agent.get("title") or None,
        )

    def ack(self, event_id: int) -> bool:
        with self._lock:
            event = self.store.event(event_id)
            if not event or not self.watched(event.get("workspace_id")):
                return False
            ok = self.store.ack_event(event_id, self.clock())
            agent = self.agents.get(event['identity'])
            if (agent and event['type'] in ('completion', 'question', 'approval') and
                    event.get('resolved_at') is None and agent.get('kind') == event['type'] and
                    agent.get('title') == event.get('title')):
                agent['dismissed_episode'] = self._episode_key(agent, event['type'])
                self._save(agent)
            if event["notify_state"] in ("pending", "held"):
                self.store.set_notify_state([event_id], "acked")
            self._bump()
            return ok or event.get('acked_at') is not None

    def settings_changed(self) -> None:
        """Re-evaluate everything after a settings change."""
        with self._lock:
            now = self.clock()
            for identity, agent in list(self.agents.items()):
                if not self.watched(agent.get("workspace_id")):
                    del self.agents[identity]
                    continue
                self._evaluate(agent, now, late=True)
            self._bump()

    def prune(self) -> None:
        with self._lock:
            cutoff = self.clock() - float(self.settings.get("history.retention_days", 7)) * 86400
            self.store.prune_events(cutoff)
            self.store.prune_activity(cutoff)
            self.store.delete_agents_before(cutoff)
            for identity, agent in list(self.agents.items()):
                if not agent.get("present") and (agent.get("gone_at") or 0) < cutoff:
                    del self.agents[identity]

    # views ------------------------------------------------------------------

    def notify_links(self) -> list[dict]:
        with self._lock:
            visible = {i for i, a in self.agents.items()
                       if a.get("present") and self.watched(a.get("workspace_id"))}
            links = self._links(visible)
            self._name_links(links, [
                {"identity": i, "name": a.get("name"), "title": a.get("title"), "pane_id": a.get("pane_id")}
                for i, a in self.agents.items()
            ])
            return links

    def ended_agents(self) -> list[dict]:
        """Agents that are no longer present, with the pane they last used (for Clean up)."""
        with self._lock:
            return [dict(a) for a in self.agents.values()
                    if not a.get("present") and self.watched(a.get("workspace_id"))]

    def agent_by_identity(self, identity: str) -> dict | None:
        with self._lock:
            agent = self.agents.get(identity)
            if agent and self.watched(agent.get("workspace_id")):
                return dict(agent)
            return None

    def view(self, *, include_all_workspaces: bool = False) -> dict:
        """Everything the dashboards show, organised by workspace.

        Only watched workspaces appear in ``workspaces`` and ``agents``.
        ``include_all_workspaces`` adds ``chooser``: id and label of every
        workspace, nothing else, so they can be switched on.
        """
        with self._lock:
            now = self.clock()
            watched_ids = set(self.settings.get("watch.enabled_workspaces", []))
            open_by_identity: dict[str, list[dict]] = {}
            for event in self.store.open_events():
                if event["workspace_id"] in watched_ids:
                    open_by_identity.setdefault(event["identity"], []).append(event)

            rel_state = self.relations.state() if self.relations else None
            agents_out = []
            for agent in self.agents.values():
                if agent.get("workspace_id") not in watched_ids:
                    continue
                events = open_by_identity.get(agent["identity"], [])
                if not agent.get("present") and not events:
                    continue
                current = next((e for e in events if e["type"] == agent.get("kind")), None)
                agents_out.append(self._agent_view(agent, current, now, rel_state))

            group = self.settings.get("ui.group_attention_first", True)
            links = self._links({a["identity"] for a in agents_out if a["present"]}, rel_state)
            self._apply_tiers(agents_out, links)
            flow.decorate(agents_out, links, self.agents, now, fresh_seconds=rules.trace_fresh_seconds(self.settings),
                          patterns=self.settings.get("output.redact", []))

            agents_out.sort(key=lambda a: ((rules.STATES.index(a["state"]) if group else 0) + (100 if a["acked"] else 0),
                                           a["workspace_label"].lower(), (a["name"] or a["pane_id"] or "")))

            attention = [a for a in agents_out if a.get("tier")]
            counts = {k: sum(1 for a in agents_out if a["kind"] == k) for k in rules.KIND_ORDER}
            counts.update({f"state_{k}": sum(1 for a in agents_out if a["state"] == k) for k in rules.STATES})
            for name in tiers.TIERS:
                counts[name] = sum(1 for a in attention if a.get("tier") == name)
            counts["needs_you"] = counts["waiting_on_you"]
            counts["attention"] = len(attention)

            workspaces = [self._workspace_view(w, [a for a in agents_out if a["workspace_id"] == w["id"]])
                          for w in self._ordered_workspaces() if w["id"] in watched_ids]
            out = {
                "version": self.version,
                "generated_at": now,
                "collector": {"started_at": self.started_at, "herdr": dict(self.herdr)},
                "workspaces": workspaces,
                "agents": agents_out,
                "links": links,
                "attention_count": counts["attention"],
                "counts": counts,
            }
            # v0.9.8.3: changes when a workspace is added, closed or renamed, so an open chooser reloads.
            out["chooser_rev"] = hashlib.sha256(repr(sorted((w["id"], w["label"]) for w in self.workspaces.values())).encode()).hexdigest()[:12]
            if include_all_workspaces:
                out["chooser"] = self.chooser()
            return out

    def _links(self, visible: set[str], rel_state=None) -> list[dict]:
        """All relationships the dashboards may show.

        * observed-spawn: spawn/fork recorded in the trace of a watched agent
        * registered / manual: declared in the registry (reports_to, spawned_by)

        An end that is not a watched, present agent is replaced by a
        placeholder: "gone: <name>" for a watched agent that has closed,
        otherwise "hidden or unknown agent" with no id or details.
        """
        from . import relations as rel
        counter = [0]
        merged: dict[tuple, dict] = {}

        def endpoint(identity: str):
            if identity in visible:
                return identity, False, None
            counter[0] += 1
            agent = self.agents.get(identity)
            if agent and self.watched(agent.get("workspace_id")):
                return f"gone-{counter[0]}", True, f"gone: {agent.get('name') or agent.get('agent') or 'agent'}"
            return f"hidden-{counter[0]}", True, "hidden or unknown agent (not in a watched workspace, or not in Herdr)"

        def add(raw_from, raw_to, kind, provenance, source, since):
            key = (raw_from, raw_to, kind)
            if key in merged:
                entry = merged[key]
                if provenance not in entry["provenance"]:
                    entry["provenance"].append(provenance)
                    entry["source"] += f"; {source}"
                return
            f, from_hidden, from_label = endpoint(raw_from)
            t, to_hidden, to_label = endpoint(raw_to)
            merged[key] = {"from": f, "to": t, "kind": kind, "provenance": [provenance], "source": source,
                           "since": since, "hidden": to_hidden, "from_hidden": from_hidden,
                           "label": to_label or from_label, "cycle": False,
                           "inferred": False, "dashed": False}

        for identity in sorted(visible):
            if self.agents.get(identity, {}).get("session_collision"):
                continue
            trace = self.agents.get(identity, {}).get("trace") or {}
            for link in trace.get("links") or []:
                if self.agents.get(link.get("target"), {}).get("session_collision"):
                    continue
                add(identity, link.get("target"), link["kind"], "observed-spawn", link["source"], None)
        state = rel_state if rel_state is not None else (self.relations.state() if self.relations else None)
        if state:
            looped = rel.cycles([(s, o) for (s, k, o) in state.links if k == "reports_to"])
            for (subject, kind, obj), info in sorted(state.links.items(), key=lambda kv: kv[1]["since"]):
                if subject not in visible and obj not in visible:
                    continue
                if info['provenance'] == 'observed-spawn' and (subject not in visible
                        or self.agents.get(subject, {}).get('session_collision')
                        or self.agents.get(obj, {}).get('session_collision')):
                    continue
                how = {"registered": "registered by the launch helper", "manual": f"declared by you ({info['actor']})"}
                add(subject, obj, kind, info["provenance"], how.get(info["provenance"], info["provenance"]), info["since"])
                if kind == "reports_to" and (subject, obj) in looped:
                    merged[(subject, obj, kind)]["cycle"] = True
        if self.inferences and state and self.settings.get('traces.enabled', True) and self.settings.get('inference.enabled', True):
            eligible = {i for i in visible if self.agents.get(i, {}).get('present')
                        and self.watched(self.agents[i].get('workspace_id'))
                        and not self.agents[i].get('session_collision')}
            for link in self.inferences.effective(state, eligible):
                add(link['from'], link['to'], 'reports_to', 'inferred', link['source'], link['since'])
                merged[(link['from'], link['to'], 'reports_to')].update(link)
        return list(merged.values())

    def _name_links(self, links: list[dict], agents: list[dict]) -> None:
        # Display names only (content.agent_name): never a state label such as "Idle".
        from .content import agent_name
        by_id = {a["identity"]: a for a in agents}
        for link in links:
            dest = by_id.get(link.get("to"))
            src = by_id.get(link.get("from"))
            if dest and not link.get("hidden"):
                link["to_name"] = agent_name(dest)
            elif link.get("label"):
                link.setdefault("to_name", link["label"])
            if src and not link.get("from_hidden"):
                link["from_name"] = agent_name(src)

    def _apply_tiers(self, agents_out: list[dict], links: list[dict]) -> None:
        from . import asks as ask_items
        self._name_links(links, agents_out)
        answers = ask_items.load_answers(self.store)
        for agent in agents_out:
            last = agent.get("last_assistant") or {}
            message = (self.agents.get(agent["identity"]) or {}).get("last_message") or last.get("text")
            needs = (agent.get("summary") or {}).get("needs_you")
            found = tiers.classify_tier(
                agent, settings=self.settings, links=links,
                last_assistant=message, last_role=last.get("last_role"),
                summary_needs=needs, now=self.clock(), notice_tier=agent.get("notice_tier"),
            )
            coord = tiers.coordinator_of(agent.get("identity") or "", links)
            agent["reports_to"] = coord
            if found:
                data = found.as_dict()
                agent["tier_evidence"] = data.pop("evidence", None)
                agent.update(data)  # do not replace classify() evidence used on the detail page
            else:
                agent["tier"] = agent["tier_label"] = agent["reason"] = agent["snippet"] = None
                agent["waiting_on"] = None
                agent["tier_evidence"] = None
            agent["awaiting_user"] = tiers.awaiting_user(found)
            agent["question"] = None
            if agent["awaiting_user"]:
                from .sanitize import fingerprint
                dialog_revision = fingerprint(agent.get('prompt_text') or '') if agent.get('status') == 'blocked' else ''
                agent["question"] = {"id": f"{agent['identity']}:{agent.get('event_at') or last.get('at') or agent.get('status_since')}:{hashlib.sha256((found.snippet + dialog_revision).encode()).hexdigest()[:12]}",
                    "identity": agent["identity"], "text": found.snippet, "reason": found.reason,
                    "options": found.evidence.get("options", []), "terminal": agent.get("status") == "blocked", "dialog_revision": dialog_revision,
                    "at": agent.get("event_at") or last.get("at") or agent.get("status_since") or 0}
            elif agent.get("state") == "needs_you":
                agent["state"] = "waiting"
                agent["state_label"] = rules.STATE_LABELS["waiting"]
            # v0.9.6: the separate questions and decisions for Alex in this message.
            agent["asks"] = ask_items.for_agent(agent, message, coord, answers,
                                                (self.agents.get(agent["identity"]) or {}).get("earlier_messages"))
            if agent["asks"] is None:
                del agent["asks"]
            from .content import presentation
            agent["presentation"] = presentation(agent, agent.get("summary"))
            if agent["awaiting_user"] and agent.get("state") != "needs_you":
                agent["state"] = "needs_you"
                agent["state_label"] = rules.STATE_LABELS["needs_you"]
                agent["state_basis"] = {"from": found.reason}

    def _ordered_workspaces(self) -> list[dict]:
        return [w for _, w in sorted(self.workspaces.items(), key=lambda kv: (_number(kv[1]), kv[0]))]

    def chooser(self) -> list[dict]:
        """Every workspace by id and label only, for switching them on or off."""
        with self._lock:
            return [{"id": w["id"], "label": w["label"], "number": w.get("number"),
                     "watched": self.watched(w["id"]), "muted": self.settings.muted(w["id"])}
                    for w in self._ordered_workspaces()]

    def _workspace_view(self, workspace: dict, agents: list[dict]) -> dict:
        wid = workspace["id"]
        counts = {state: sum(1 for a in agents if a["state"] == state) for state in rules.STATES}
        counts.update({tier: sum(1 for a in agents if a.get("tier") == tier) for tier in tiers.TIERS})
        tabs = []
        for tab in sorted((t for t in self.tabs.values() if t["workspace_id"] == wid),
                          key=lambda t: (_number(t), t["id"])):
            members = [a["identity"] for a in agents if a["tab_id"] == tab["id"]]
            if members:
                tabs.append({"id": tab["id"], "label": tab["label"] or "unnamed tab", "agents": members})
        untabbed = [a["identity"] for a in agents if a["tab_id"] not in {t["id"] for t in tabs}]
        if untabbed:
            tabs.append({"id": None, "label": "tab unknown", "agents": untabbed})
        known = [a["last_activity_at"] for a in agents if a["last_activity_at"]]
        return {
            **workspace,
            "watched": True,
            "muted": self.settings.muted(wid),
            "stall_minutes": self.settings.stall_minutes(wid),
            "agent_count": len(agents),
            "live_agent_count": sum(1 for a in agents if a["present"] and a.get("pane_id")),
            "counts": counts,
            "attention": sum(1 for a in agents if a.get("tier")),
            "stalled": sum(1 for a in agents if a["kind"] == "stall"),
            "last_activity_at": max(known) if known else None,
            "tabs": tabs,
            # Herdr has no supervisor/worker or task-assignment data. Agents
            # share a workspace or a tab; nothing more is claimed.
            "relationships": "unknown",
        }

    def _agent_view(self, agent: dict, event: dict | None, now: float, rel_state=None) -> dict:
        from .team_reports import output_revision
        workspace = self.workspaces.get(agent.get("workspace_id"), {})
        tab = self.tabs.get(agent.get("tab_id")) or {}
        attention = agent.get("attention") or {}
        state, basis = rules.state_of(agent, now, self.settings)
        done = self.store.latest_event(agent["identity"], "completion")
        return {
            "identity": agent["identity"],
            "report_output_revision": output_revision(agent),
            "name": agent.get("name"),
            "terminal_id": agent.get("terminal_id"),
            "session_identity": agent.get("session_identity") or agent["identity"].split("@", 1)[0],
            "session_collision": bool(agent.get("session_collision")),
            "agent": agent.get("agent"),
            "title": agent.get("title") or "",
            "pane_id": agent.get("pane_id"),
            "workspace_order": agent.get("workspace_order"),
            "tab_order": agent.get("tab_order"),
            "pane_order": agent.get("pane_order"),
            "workspace_number": workspace.get("number"),
            "tab_number": tab.get("number"),
            "pane_number": agent.get("number"),
            "tab_id": agent.get("tab_id"),
            "tab_label": tab.get("label") or None,
            "cwd": _home_short(agent.get("cwd") or ""),
            "workspace_id": agent.get("workspace_id"),
            "workspace_label": workspace.get("label") or agent.get("workspace_id") or "",
            "status": agent.get("status"),
            "present": bool(agent.get("present")),
            "status_since": agent.get("status_since"),
            "since_known": bool(agent.get("since_known")),
            "first_seen": agent.get("first_seen"),
            "last_seen": agent.get("last_seen"),
            "last_activity_at": agent.get("last_activity_at"),
            "missed_changes": agent.get("missed_changes", 0),
            "state": state,
            "state_label": rules.STATE_LABELS[state],
            "state_basis": basis,
            "kind": agent.get("kind") or "unknown",
            "label": attention.get("label") or rules.LABELS.get(agent.get("kind") or "unknown", ""),
            "evidence": attention.get("evidence") or {},
            "confidence": attention.get("confidence") or "low",
            "last_done": {"at": done["at"], "title": done.get("title"), "seen_late": done["seen_late"]} if done else None,
            "metrics": metrics.build(agent, now),
            "summary": None if agent.get("session_collision") else _summary_compact(self.summaries.latest(agent["identity"]) if self.summaries else None, event),
            "roles": self.relations.labels_for(agent["identity"], rel_state) if self.relations else
                     {"role": [], "team": [], "task": []},
            "event_id": event["id"] if event else None,
            "event_at": event["at"] if event else None,
            "seen_late": bool(event and event["seen_late"]),
            "acked": bool(event and event["acked_at"]),
            "notify_state": event["notify_state"] if event else None,
            "notice_tier": nt.notice_tier(event),
            "last_assistant": agent.get("last_assistant") or None,
            "prompt_text": agent.get("prompt_text") if agent.get("status") == "blocked" else None,
        }

    def agent_detail(self, identity: str, activity_limit: int = 50) -> dict | None:
        """One agent with its timeline and events. None if not watched."""
        with self._lock:
            agent = self.agents.get(identity)
            if not agent or not self.watched(agent.get("workspace_id")):
                return None
            now = self.clock()
            current = next((e for e in self.store.open_events(identity) if e["type"] == agent.get("kind")), None)
            view = self._agent_view(agent, current, now)
            events = self.store.events_for(identity, 20)
            for event in events:
                event["label"] = event_label(event)
                if event.get('notify_state') == 'expired':
                    event['state_label'] = event['label']
            peers = [a for a in self.agents.values()
                     if a is not agent and a.get("present") and a.get("tab_id") and a.get("tab_id") == agent.get("tab_id")]
            visible = {a["identity"] for a in self.agents.values()
                       if self.watched(a.get("workspace_id")) and a.get("present")} | {identity}
            connections = [link for link in self._links(visible)
                           if link["from"] == identity or link["to"] == identity]
            self._apply_tiers([view], connections)
            history = []
            if self.relations:
                watched_ids = {a["identity"] for a in self.agents.values() if self.watched(a.get("workspace_id"))}
                for row in self.relations.events(identity, 50):
                    other = row["object"] if row["subject"] == identity else row["subject"]
                    if other and other not in watched_ids:
                        row["object" if row["subject"] == identity else "subject"] = None
                        row["hidden_end"] = True
                    row["other_name"] = (self.agents.get(other) or {}).get("name") if other in watched_ids else None
                    history.append(row)
            return {
                "agent": view,
                "connections": connections,
                "relation_history": history,
                "activity": self.store.activity(identity, activity_limit),
                "events": events,
                "same_tab": [{"identity": p["identity"], "name": p.get("name"), "agent": p.get("agent")} for p in peers],
                "relationships": "unknown",
                "generated_at": now,
            }

    def history(self, limit: int = 100) -> list[dict]:
        with self._lock:
            watched_ids = self.settings.get("watch.enabled_workspaces", [])
            events = self.store.recent_events(limit, watched_ids)
            for event in events:
                agent = self.agents.get(event["identity"], {})
                event["name"] = agent.get("name")
                event["agent"] = agent.get("agent")
                event["workspace_label"] = self.workspaces.get(event["workspace_id"], {}).get("label") or event["workspace_id"]
                event["label"] = event_label(event)
                if event.get('notify_state') == 'expired':
                    event['state_label'] = event['label']
            return events

    def wait_for_change(self, since_version: int, timeout: float) -> int:
        with self._lock:
            if self.version == since_version:
                self.changed.wait(timeout)
            return self.version
