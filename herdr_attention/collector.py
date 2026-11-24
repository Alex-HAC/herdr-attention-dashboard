"""The long-running collector: Herdr -> engine -> API and phone alerts.

Threads:
  watcher  holds the Herdr event subscription; reconnects with back-off
  worker   (main thread) applies events, re-checks timers, polls output
           fingerprints for stall detection, runs the notifier
  api      local Unix socket server, plus the phone server when enabled
"""

from __future__ import annotations

import hmac
import re
import logging
import queue
import signal
import threading
import time
from pathlib import Path

from . import __version__
from .api import LocalServer, PhoneServer, make_handler, serve
from .ui_contract import listener_address, desktop_url, hash_path, CONTRACT
from .config import Settings, load_file, state_dir
from .engine import Engine
from . import agent_runtime, flow, herdr_theme
from .classify import trace_fresh_seconds
from . import quotas as account_quotas
from .herdr import HerdrClient, HerdrError, TOPOLOGY_EVENTS, wait_for_herdr
from .notify import Notifier, make_sender
from .presence import Presence
from .reply import MAX_TEXT as REPLY_MAX_TEXT
from .sanitize import clean_output, fingerprint, readable, redact
from .terminal import TerminalReader, digest, plain as plain_terminal, redact_ansi
from .secrets import Keyring
from .store import Store
from .relations import LABELS, LINK_KINDS, Registry, RelationError
from .summarize import Summarizer, material
from .traces import Locator, TraceReader, split_identity

log = logging.getLogger("herdr_attention")

TICK_SECONDS = 5.0
PRUNE_SECONDS = 3600.0
RECONCILE_SECONDS = 45.0
RECONCILE_MIN_SECONDS = 2.0
RECONCILE_MAX_BACKOFF = 180.0
WORKSPACE_REFRESH_MIN_SECONDS = 3.0   # v0.9.8.3: "Refresh workspaces", at most once per 3 s
WORKSPACE_PROBE_SECONDS = 5.0         # v0.9.8.3: a cheap workspace.list between the 45 s reconciles


class TooSoon(Exception):
    def __init__(self, retry_after: float):
        super().__init__(f"wait {retry_after:.1f} s")
        self.retry_after = retry_after
EVENT_ALIASES = {t.replace(".", "_"): t for t in TOPOLOGY_EVENTS}
EVENT_ALIASES["pane_agent_status_changed"] = "pane.agent_status_changed"


_AGENT_MESSAGE = re.compile(r"^\s*([A-Za-z0-9][\w.-]{0,40}):\s")
_AGENT_WORDS = re.compile(r"(?i)\s*(?:done|question|ready|frozen|blocked|status|report|update|note|fyi|laptop|interim|review)\b")


def is_agent_message(text: str, names: set[str]) -> bool:
    """A user-role entry that another agent sent ("rel-1210: question: …") or a tool/hook notice,
    not a turn Alex typed."""
    if text.lstrip().startswith("<"):
        return True
    m = _AGENT_MESSAGE.match(text)
    return bool(m and (m.group(1) in names or _AGENT_WORDS.match(text[m.end():])))


class Collector:
    def __init__(self, config_path: Path | None = None, db_path: Path | None = None,
                 client: HerdrClient | None = None, settings: Settings | None = None,
                 clock=time.time):
        self.clock = clock
        self.store = Store(db_path or state_dir() / "state.db")
        self.settings = settings or Settings(load_file(config_path), self.store.load_settings())
        self.engine = Engine(self.settings, self.store, clock)
        from .tailnet_access import TailnetAccess
        self.tailnet = TailnetAccess(self.store)   # v0.9.7.4 allow list for the phone listener
        self.relations = Registry(self.store)
        self.engine.relations = self.relations
        from .linking import Observations
        self.observations = Observations(self.store, self.relations, clock)
        from .quota_stops import QuotaStops
        self.quota_stops = QuotaStops(self.store, clock)
        self._quota_cleanup_0962()
        self.summarizer = Summarizer(self.store, self.settings, clock)
        self.engine.summaries = self.summarizer
        self._summary_requested: set[int] = set()
        self._deep_read: set[str] = set()   # identities read 120 entries deep once (v0.9.8.1)
        self._reply_submit_lock = threading.RLock()
        self._ask_lock = threading.Lock()   # one question-item answer at a time (answer_ask)
        self._quota_screens = {}
        self._quota_checked = {}
        self._quota_refresh_lock = threading.Lock()
        self._snapshot_lock = threading.Lock()        # handle_snapshot: worker loop and Refresh workspaces
        self._ws_refresh_lock = threading.Lock()
        self._ws_refresh_at = -1e9
        self._ws_probe_at = 0.0
        self._theme: tuple[float, dict] = (0.0, {})
        self.presence = Presence(self.settings, clock)
        self.keyring = Keyring(self.settings.get("keyring.service"))
        self.notifier = Notifier(self.settings, self.store, self.presence,
                                 make_sender(self.settings, self.keyring), self._names, clock,
                                 agent_of=self.engine.agent_by_identity, links_of=self.engine.notify_links)
        self.notifier.prepare_gate = self.gate_input
        from .reply import ReplyService
        self.replies = ReplyService(self.store, self.settings, clock,
                                    socket_path=self.settings.get("herdr.socket") or None)
        leftover = self.store.events_by_notify_state(["gating"])
        if leftover:
            self.store.set_notify_state([e["id"] for e in leftover], "pending")
        self.client = client or HerdrClient(self.settings.get("herdr.socket") or None)
        self.terminals = TerminalReader(self.client)   # v0.9.8 live terminal view (throttled, shared)
        from .cleanup import Cleanup, CleanupReader, PaneCloser
        from .notifications import LISTED_STATES
        sock = self.settings.get("herdr.socket") or None
        self.cleanup = Cleanup(
            settings=self.settings, store=self.store, view=lambda: self.view(local=False), clock=clock,
            ended_agents=self.engine.ended_agents, reader=client or CleanupReader(sock),
            # Only the real Herdr gets the one write; an injected client (tests, replays) never closes.
            closer=PaneCloser(sock) if client is None else None,
            unread=lambda: self.store.notification_events(
                LISTED_STATES, self.settings.get("watch.enabled_workspaces", []) or [], limit=500, unread_only=True))
        self.traces = TraceReader(
            Locator(self.settings.get("traces.claude_dirs", []), self.settings.get("traces.codex_dirs", [])),
            int(self.settings.get("traces.max_bytes_per_pass", 16 * 1024 * 1024)), clock)
        from .visuals import Visuals
        self.visuals = Visuals(self.settings,self.traces.locator,state_dir()/"thumbnails",clock)
        self.visuals.attach_store(self.store)
        from .question_images import QuestionImages
        self.question_images = QuestionImages(self.settings, state_dir()/"question-thumbnails")
        from .account_icons import ImageStore
        self.account_images = ImageStore(self.settings.account_image_dir())
        # v0.9.7.1: accounts proven from the session's own config folder (account_proof.py).
        from .account_proof import AccountProofs
        from . import routing
        self.account_proofs = AccountProofs(lambda: routing.load().get("accounts") or {}, self.traces.locator.find,
                                            lambda pane_id: self.cleanup.reader.process_info(pane_id))
        from .inference import EvidenceReader, Inferences
        self.inferences = Inferences(self.store)
        self.engine.inferences = self.inferences
        self.inference_reader = EvidenceReader(self.traces.locator)
        self.stop = threading.Event()
        self.trace_wake = threading.Event()   # set when the watched agents change
        self.queue: queue.Queue = queue.Queue()
        self._sub_stop = threading.Event()
        self._subscribed: set[str] = set()
        self._reconcile_requested = threading.Event()
        self._next_reconcile = 0.0
        self._reconcile_not_before = 0.0
        self._reconcile_failures = 0
        self._incomplete_log_at = float("-inf")
        from .layout_evidence import CompletionEvidence
        from .routing import load as load_routing
        self.completion_evidence = CompletionEvidence(clock)
        self._layout_lock = threading.RLock()
        self._layout_snapshot = {'agents': [], 'tabs': [], 'panes': []}
        self.layout_policy = load_routing().get('layout', {})
        self._settings_lock = threading.Lock()
        from .sidebar import Sidebar
        self.sidebar = Sidebar(self.client.socket_path) if isinstance(self.client, HerdrClient) else None
        self.servers: list = []

    # lifecycle ------------------------------------------------------------------

    def start_api(self) -> None:
        local = LocalServer(self.settings.api_socket, make_handler(self, local=True))
        self.servers.append(local)
        serve(local)
        log.info("local API on %s", self.settings.api_socket)
        if self.settings.get("phone.enabled"):
            address = listener_address(self.settings, "phone")
            phone = PhoneServer(address, make_handler(self, local=False))
            self.servers.append(phone)
            serve(phone)
            log.info("phone dashboard on %s:%s", *address)

        if self.settings.get("desktop.enabled"):
            address = listener_address(self.settings, "desktop")
            desktop = PhoneServer(address, make_handler(self, local=True, desktop=True))
            self.servers.append(desktop)
            serve(desktop)
            log.info("desktop overview on %s:%s", *address)

    def focus_agent(self, identity: str) -> dict:
        from .focus import FocusService
        return FocusService(self.engine, self.client.socket_path).focus(identity)

    def run(self) -> None:
        signal.signal(signal.SIGTERM, lambda *_: self.stop.set())
        signal.signal(signal.SIGINT, lambda *_: self.stop.set())
        self.start_api()
        watcher = threading.Thread(target=self._watch_loop, name="watcher", daemon=True)
        watcher.start()
        if self.settings.get("traces.enabled", True):
            threading.Thread(target=self._trace_loop, name="traces", daemon=True).start()
        threading.Thread(target=self._summary_loop, name="summaries", daemon=True).start()
        threading.Thread(target=self._notify_gate_loop, name="notify-gate", daemon=True).start()
        threading.Thread(target=self._layout_loop, name="layout-evidence", daemon=True).start()
        threading.Thread(target=self._visuals_loop, name="visuals", daemon=True).start()
        try:
            self.work_loop()
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        if self.sidebar:
            try:
                self.sidebar.clear()
            except (HerdrError, OSError):
                pass
        self.stop.set()
        self.trace_wake.set()
        self.completion_evidence.wake.set()
        self.summarizer.wake.set()
        self._sub_stop.set()
        for server in self.servers:
            server.stopping.set()
            server.shutdown()
            server.server_close()
        with self.engine.changed:
            self.engine.changed.notify_all()
        self.store.close()

    def request_resync(self) -> None:
        """Re-subscribe and take a fresh snapshot (watched set changed)."""
        self._reconcile_requested.set()
        self._sub_stop.set()

    # Herdr side ------------------------------------------------------------------

    def _watch_loop(self) -> None:
        while not self.stop.is_set():
            if not wait_for_herdr(self.client, self.stop):
                return
            self._sub_stop = threading.Event()
            try:
                pong = self.client.ping()
                self.engine.set_herdr_status(True, version=pong.get("version"))
                panes = [a["pane_id"] for a in self.client.agents() if self.settings.watched(a.get("workspace_id"))]
                self._subscribed = set(panes)
                # Subscribe first, snapshot second: anything that changes in
                # between arrives as an event after the snapshot.
                self.client.subscribe(panes, self._sub_stop, self._on_herdr_event,
                                      on_started=self._reconcile_requested.set)
                if not self._sub_stop.is_set():
                    self.engine.set_herdr_status(False, error="Herdr closed the event stream")
            except (HerdrError, OSError) as exc:
                self.engine.set_herdr_status(False, error=str(exc))
                log.warning("Herdr connection problem: %s", exc)
                self.stop.wait(2.0)

    def _on_herdr_event(self, envelope: dict) -> None:
        self.engine.touch_herdr()
        kind = EVENT_ALIASES.get(envelope.get("event"), envelope.get("event"))
        if kind in TOPOLOGY_EVENTS:
            # Coalesce at ingress too: topology storms occupy one flag,
            # rather than growing the worker queue with redundant snapshots.
            self.handle_event(envelope)
        else:
            self.queue.put(("event", envelope))

    def handle_snapshot(self) -> None:
        agents = self.client.agents()
        workspaces = self.client.workspaces()
        tabs = self.client.tabs()
        panes = self.client.panes() if hasattr(self.client, 'panes') else agents
        from .routing import load as load_routing, RoutingError
        try:
            policy = load_routing().get('layout', {})
        except RoutingError as exc:
            log.warning('layout policy: %s', exc)
            policy = self.layout_policy
        with self._layout_lock:
            self.layout_policy = policy
            self._layout_snapshot = {key: [item for item in values if self.settings.watched(item.get('workspace_id'))]
                                     for key, values in (('agents', agents), ('tabs', tabs), ('panes', panes))}
        self.engine.set_tabs(tabs)
        for identity, pane_id in self.engine.apply_snapshot(agents, workspaces):
            self._explain(identity, pane_id)
        if set(self.engine.watched_pane_ids()) != self._subscribed:
            self._sub_stop.set()
        self.observe_tags(agents)
        self.trace_wake.set()

    def observe_tags(self, agents):
        from .linking import validated_parent
        from .launcher import _identity
        for info in agents:
            if not self.settings.watched(info.get('workspace_id')):
                continue
            parent = validated_parent(info, agents)
            identity = _identity(info)
            if parent:
                # Pane tokens are inputs only; durable facts live in the registry.
                from .relations import cycles
                edges = [(s, o) for s, k, o in self.relations.state().links if k == 'spawned_by']
                if (identity, parent) not in cycles(edges + [(identity, parent)]):
                    self.relations.link(identity, 'spawned_by', parent, at=self.clock(),
                                        provenance='observed-spawn', actor=identity, note='validated session-bound pane tags')

    def reconcile_due(self, now: float | None = None) -> bool:
        """Worker-owned, coalesced metadata scan; retries cannot bypass backoff."""
        now = time.monotonic() if now is None else now
        if now < self._reconcile_not_before:
            return False
        if now < self._next_reconcile and not self._reconcile_requested.is_set():
            return False
        self._reconcile_requested.clear()
        try:
            with self._snapshot_lock:
                self.handle_snapshot()
        except (HerdrError, OSError, ValueError) as exc:
            self._reconcile_failures += 1
            delay = min(RECONCILE_MAX_BACKOFF, 5.0 * 2 ** min(self._reconcile_failures - 1, 6))
            self._next_reconcile = self._reconcile_not_before = now + delay
            self.engine.reconcile_status(error=str(exc), failures=self._reconcile_failures)
            log.warning("metadata reconciliation failed; retry in %ss: %s", delay, exc)
            return False
        self._reconcile_failures = 0
        self._next_reconcile = now + RECONCILE_SECONDS
        self._reconcile_not_before = now + RECONCILE_MIN_SECONDS
        self.engine.reconcile_status()
        return True

    def refresh_workspaces(self) -> dict:
        """v0.9.8.3: "Refresh workspaces": the reconcile now (agent.list, workspace.list, tab.list:
        reads only), not in up to 45 s. At most once per 3 s (TooSoon). New workspaces stay off."""
        now = time.monotonic()
        with self._ws_refresh_lock:
            wait = self._ws_refresh_at + WORKSPACE_REFRESH_MIN_SECONDS - now
            if wait > 0:
                raise TooSoon(wait)
            self._ws_refresh_at = now
        before = {w["id"] for w in self.engine.chooser()}
        try:
            with self._snapshot_lock:
                self.handle_snapshot()
        except (HerdrError, OSError, ValueError) as exc:
            return {"ok": False, "detail": f"Herdr did not answer: {exc}"[:200], "workspaces": self.engine.chooser()}
        self._next_reconcile = now + RECONCILE_SECONDS
        self.engine.reconcile_status()
        after = self.engine.chooser()
        new = [w for w in after if w["id"] not in before]
        return {"ok": True, "workspaces": after, "count": len(after),
                "new": [{"id": w["id"], "label": w["label"]} for w in new], "at": self.clock()}

    def probe_workspaces(self, now: float | None = None) -> bool:
        """v0.9.8.3: one workspace.list every 5 s; a new, closed or renamed workspace asks for the
        reconcile now. Herdr's workspace events do this too; this covers a missed event."""
        now = time.monotonic() if now is None else now
        if now < self._ws_probe_at:
            return False
        self._ws_probe_at = now + WORKSPACE_PROBE_SECONDS
        try:
            found = {w["workspace_id"]: w.get("label") or w["workspace_id"] for w in self.client.workspaces() if w.get("workspace_id")}
        except (HerdrError, OSError, ValueError):
            return False
        known = {w["id"]: w["label"] for w in self.engine.chooser()}
        if found != known:
            self._reconcile_requested.set()
            return True
        return False

    def handle_event(self, envelope: dict) -> None:
        kind = envelope.get("event")
        # Generic lifecycle envelopes use snake_case; per-pane status
        # subscription envelopes use dotted names in Herdr 0.9.1.
        kind = EVENT_ALIASES.get(kind, kind)
        data = envelope.get("data") or {}
        pane_id = data.get("pane_id") or (data.get("pane") or {}).get("pane_id")
        if kind == "pane.agent_status_changed" and pane_id:
            self._refresh_pane(pane_id)
        elif kind in TOPOLOGY_EVENTS or kind == "pane.agent_status_changed":
            # Topology payloads differ (created/updated/moved nest PaneInfo).
            # Never apply delayed lifecycle payloads as current truth: a pane
            # may already have been reused. agent.list is authoritative.
            self._reconcile_requested.set()
            if kind.startswith("pane.") and not pane_id:
                now = time.monotonic()
                if now - self._incomplete_log_at >= 60:
                    log.warning("incomplete %s event; scheduling metadata reconciliation", kind)
                    self._incomplete_log_at = now

    def _refresh_pane(self, pane_id: str) -> None:
        info = self.client.agent(pane_id)
        if info is None:
            self.engine.pane_gone(pane_id, "agent no longer detected")
            return
        if info.get("agent_status") == "working":
            from .engine import identity_of
            self.quota_stops.clear(identity_of(info))
            self.observations.clear_walls_of(identity_of(info))
        if self.engine.apply_agent(info):
            self._explain(None, pane_id, info=info)

    def _explain(self, identity: str | None, pane_id: str, info: dict | None = None) -> None:
        from .engine import identity_of
        identity = identity or self.engine.panes.get(pane_id) or identity_of(info or {})
        rule = None
        try:
            result = self.client.explain(pane_id)
            matched = result.get("matched_rule") or {}
            if matched.get("state") == "blocked":
                rule = matched.get("id")
        except HerdrError as exc:
            log.info("explain failed for %s: %s", pane_id, exc)
        prompt = ""
        if rule:
            try:
                prompt, _ = redact(readable(self.client.read_output(pane_id, lines=40, source="visible")), self.settings.get("output.redact", []))
            except HerdrError:
                pass
        self.engine.set_blocked_rule(identity, rule, prompt)

    # traces (own thread: a big first read must not delay Herdr events) ----------

    def poll_traces(self) -> None:
        targets = self.engine.trace_targets()
        self.traces.forget_except(set(targets))
        for identity in targets:
            try:
                summary = self.traces.update(identity)
            except OSError as exc:
                summary = {"available": False, "reason": f"trace not readable: {exc.strerror}"}
            for link in summary.get('links') or []:
                current = self.engine.agent_by_identity(identity) or {}
                if not current.get('session_collision') and link.get('kind') == 'spawned_by' and link.get('target') != identity:
                    self.relations.link(identity, 'spawned_by', link['target'], at=self.clock(),
                                        provenance='observed-spawn', actor=identity, note=link.get('source'))
            self.engine.set_trace(identity, summary)
            self._refresh_last_assistant(identity)
        try:   # read once per session here, on the trace thread; the view only looks the result up
            agents = [dict(a, launch=self.observations.card(a["identity"]))
                      for a in (self.engine.agent_by_identity(i) for i in targets) if a]
            self.account_proofs.observe(agents)
        except Exception:
            log.exception("account proofs: unexpected error")
        self.poll_inference()

    def inference_check(self, ignore_declared=()) -> dict:
        """Dry read-only evaluation; declarations and suppressions are never changed."""
        from .inference import select
        from .relations import State
        if not self.settings.get('traces.enabled', True) or not self.settings.get('inference.enabled', True):
            return {'enabled': False, 'links': [], 'ignored_declarations': []}
        ignored = {self.resolve_agent(ref, present_only=True) for ref in ignore_declared}
        with self.engine._lock:
            agents = [dict(a) for a in self.engine.agents.values()
                      if a.get('present') and self.settings.watched(a.get('workspace_id'))]
        state = self.relations.state()
        state = State(links={key: info for key, info in state.links.items()
                             if not (key[0] in ignored and key[1] == 'reports_to')}, labels=state.labels)
        candidates = self.inference_reader.candidates(agents, state)
        visible = {a['identity'] for a in agents if not a.get('session_collision')}
        return {'enabled': True, 'ignored_declarations': sorted(ignored),
                'links': select(candidates, state, visible, self.inferences.suppressed(), ignored)}

    def poll_inference(self) -> None:
        # Background trace thread only: inference never delays topology updates.
        if not self.settings.get('traces.enabled', True) or not self.settings.get('inference.enabled', True):
            return
        with self.engine._lock:
            agents = [dict(a) for a in self.engine.agents.values()
                      if a.get('present') and self.settings.watched(a.get('workspace_id'))]
        from .inference import select
        state = self.relations.state()
        candidates = self.inference_reader.candidates(agents, state)
        safe = select(candidates, state, {a['identity'] for a in agents if not a.get('session_collision')},
                      self.inferences.suppressed())
        self.inferences.replace(safe, self.clock())
        self.engine.poke()

    def _trace_loop(self) -> None:
        while not self.stop.is_set():
            try:
                self.poll_traces()
            except Exception:
                log.exception("traces: unexpected error")
            # Catch up faster while a big trace is still being read.
            behind = any(not (a.get("trace") or {}).get("caught_up", True) for a in list(self.engine.agents.values()))
            self.trace_wake.wait(2.0 if behind else float(self.settings.get("traces.poll_seconds", 30)))
            self.trace_wake.clear()

    # summaries (the only model calls) -------------------------------------------

    def queue_summaries(self) -> None:
        """Queue watched reports and optional attention summaries on collector ticks."""
        from .team_reports import queue_watched
        queue_watched(self.engine.view(),self.summarizer,self.settings)
        if not self.summarizer.enabled or not self.summarizer.auto:
            return
        for event in self.store.open_events():
            if event["id"] in self._summary_requested or event["acked_at"] or not self.settings.watched(event["workspace_id"]):
                continue
            if (self.engine.agent_by_identity(event["identity"]) or {}).get("session_collision"):
                continue
            self._summary_requested.add(event["id"])
            self.summarizer.request(event["identity"], event["id"], "auto")

    def _merge_report_asks(self, view: dict) -> None:
        """v0.9.8.1: asks found by a coordinator report's model, checked against the agent's own messages."""
        from . import asks as ask_items
        answers = None
        for agent in view["agents"]:
            if not agent.get("present", True):
                continue
            report = self.summarizer.latest_complete(agent["identity"], "report")
            found = ((report or {}).get("summary") or {}).get("asks_for_owner") if report else None
            if not found:
                continue
            if answers is None:
                answers = ask_items.load_answers(self.store)
            ask_items.add_report_asks(agent, found, self.engine.messages_of(agent["identity"]), answers)

    def summary_input(self, identity: str, mode: str) -> dict | None:
        """Redacted, bounded material for one agent, or None if it may not be summarised."""
        agent = self.engine.agent_by_identity(identity)
        if agent is None or agent.get("session_collision"):
            return None
        patterns = self.settings.get("output.redact", [])
        if mode == "report":
            from .team_reports import prepare
            return prepare(self.view(local=False),identity,lambda target:self.summary_input(target,"refresh"),
                lambda pane:self.client.read_output(pane,lines=40,source="visible"),patterns,self.clock())
        count = int(self.settings.get("summaries.longer_entries" if mode == "longer" else "summaries.entries", 20))
        entries = []
        if self.settings.get("traces.enabled", True):
            entries = self.traces.conversation(identity, count, None, patterns).get("entries", [])
        screen = ""
        if agent.get("present") and agent.get("kind") in ("approval", "question", "blocked"):
            try:
                text = self.client.read_output(agent["pane_id"], lines=40, source="visible")
                screen, _ = redact(readable(text), patterns)
            except HerdrError:
                screen = ""
        if not entries and not screen:
            return None
        workspace = self.engine.workspaces.get(agent.get("workspace_id"), {})
        meta = {"name": agent.get("name"), "agent": agent.get("agent"), "workspace": workspace.get("label"),
                "status": agent.get("status"), "label": (agent.get("attention") or {}).get("label"),
                "title": agent.get("title")}
        limit = int(self.settings.get("summaries.max_input_chars", 14000)) * (2 if mode == "longer" else 1)
        text, info = material(meta, entries, screen, limit)
        return {"text": text, "info": info, "cwd": agent.get("cwd")}

    def _summary_loop(self) -> None:
        while not self.stop.is_set():
            job = self.summarizer.next_job() if self.summarizer.enabled else None
            if job is None:
                self.summarizer.wake.wait(5.0)
                self.summarizer.wake.clear()
                continue
            try:
                self.summarizer.run_job(job, self.summary_input)
            except Exception:
                log.exception("summaries: unexpected error")
            self.engine.poke()

    def gate_input(self, event_ids: list[int]) -> dict:
        """Short redacted material and dashboard links for the notification gate."""
        import re
        from urllib.parse import quote
        from .notify_gate import dashboard_links
        ids = set(event_ids or [])
        events = [e for e in self.store.recent_events(80) if e["id"] in ids]
        names = self.notifier.names()
        lines = []
        prs = []
        patterns = self.settings.get("output.redact", [])
        from .sanitize import redact
        for event in events:
            info = names.get(event["identity"], {})
            who = info.get("name") or info.get("agent") or "agent"
            where = info.get("workspace_label") or event.get("workspace_id") or "?"
            lines.append(f"Event: {event['type']} · {who} · {where}"
                         + (" (seen late)" if event.get("seen_late") else ""))
            if event.get("title"):
                lines.append(f"Task title: {event['title']}")
            prepared = self.summary_input(event["identity"], "auto") if self.settings.watched(event.get("workspace_id")) else None
            if prepared and prepared.get("text"):
                snippet = prepared["text"]
                if len(snippet) > int(self.settings.get("notify_gate.max_input_chars", 4000)):
                    snippet = snippet[-int(self.settings.get("notify_gate.max_input_chars", 4000)):]
                lines.append(snippet)
                for match in re.findall(r"https://(?:github\.com|gitlab\.com)/[^\s)]+/merge_requests/\d+|https://github\.com/[^\s)]+/pull/\d+", prepared["text"]):
                    if match not in prs:
                        prs.append(match)
        text, _ = redact("\n".join(lines), patterns) if lines else ("", 0)
        # redact() may return a tuple; accept a string too
        if isinstance(text, tuple):
            text = text[0]
        return {"text": text, "links": dashboard_links(events, self.settings, prs)}

    def _notify_gate_loop(self) -> None:
        gate = getattr(self.notifier, "gate", None)
        if gate is None:
            return
        while not self.stop.is_set():
            job = gate.next_job() if gate.enabled else None
            if job is None:
                gate.wake.wait(5.0)
                gate.wake.clear()
                continue
            try:
                self.notifier.finish_gate_job(job)
            except Exception:
                log.exception("notify-gate: unexpected error")
            self.engine.poke()

    def summary_job(self, job_id: int) -> dict | None:
        return self.summarizer.get(job_id)

    def request_summary(self, identity: str, mode: str, question: str | None = None,
                        context: str | None = None) -> dict:
        if mode not in ("refresh", "longer", "ask", "report"):
            raise ValueError("mode must be refresh, longer, ask or report")
        agent = self.engine.agent_by_identity(identity)
        if agent is None:
            raise ValueError("no such agent in a watched workspace")
        if agent.get("session_collision"):
            raise ValueError("shared native session: use pane output; transcript attribution is ambiguous")
        if mode == "ask":
            result = self.summarizer.request_ask(identity, question or "", context or "")
            self.engine.poke()
            return result
        current = next((e for e in self.store.open_events(identity) if e["type"] == agent.get("kind")), None)
        result = self.summarizer.request(identity, current["id"] if current else None, mode)
        self.engine.poke()
        return result

    def summary_audit(self) -> dict:
        names = {a["identity"]: a.get("name") or a.get("agent") for a in self.engine.agents.values()
                 if self.settings.watched(a.get("workspace_id"))}
        return self.summarizer.audit(names)

    def _quota_cleanup_0962(self) -> None:
        """Once: drop the quota stops and walls that 0.9.6.1 and older raised from agents' own text.

        A real limit banner still on a screen is found again at the next screen check."""
        if self.store.get_meta("quota_cleanup_0962"):
            return
        stops = self.quota_stops.active()
        for identity in stops:
            self.quota_stops.clear(identity, forget_screen=True)
        walls = self.store._exec("SELECT provider, identity FROM quota_walls").fetchall()
        self.store._exec("DELETE FROM quota_walls")
        self.store.set_meta("quota_cleanup_0962", str(self.clock()))
        log.warning("0.9.6.2 cleanup: ended %d quota stop(s) %s and removed %d quota wall(s) %s",
                    len(stops), sorted(stops), len(walls), sorted(f"{p}:{i}" for p, i in walls))

    def poll_output(self) -> None:
        """Fingerprint the visible screen of working agents (stall evidence).

        ``visible`` works while an agent is busy; a failed read is recorded
        as "evidence unavailable", which never counts towards a stall.
        """
        # Quota errors often settle as blocked/idle; inspect all watched live agents.
        poll = float(self.settings.get('stall.output_poll_seconds', 60)) or 60
        now = self.clock()
        working_due = set(self.engine.output_targets())
        from .notify_tiers import is_watched_closely
        watch_poll = CONTRACT['watch_closely']['output_poll_seconds']
        with self.engine._lock:
            present = {a['identity'] for a in self.engine.agents.values() if a.get('present')}
            working = {a['identity'] for a in self.engine.agents.values() if a.get('present') and a.get('status') == 'working'}
        # An agent that is gone or working is not out of quota: end its stop and its walls.
        for identity in self.quota_stops.prune(present):
            log.info("quota stop ended: %s is gone", identity)
        for identity in working.intersection(self.quota_stops.active()):
            # v0.9.8.10: a brief working flicker must not forget the banner; observe() decides.
            self.quota_stops.end(identity, banner_gone=False)
            self.observations.clear_walls_of(identity)
        with self.engine._lock:
            targets = [(a['identity'], a['pane_id']) for a in self.engine.agents.values() if a.get('present')
                       and self.settings.watched(a.get('workspace_id'))
                       and (now - self._quota_checked.get(a['identity'], 0) >= (watch_poll if is_watched_closely(self.settings,a['identity']) else poll)
                            or (a['identity'], a['pane_id']) in working_due)]
        for identity, pane_id in targets:
            self._quota_checked[identity] = now
            try:
                text = self.client.read_output(pane_id, lines=int(self.settings.get("output.lines", 40)), source="visible")
            except HerdrError as exc:
                self.engine.record_output_error(identity, exc.code or "read failed")
                continue
            self.engine.record_output(identity, fingerprint(text))
            self.engine.record_report_output(identity, fingerprint(text))
            current = self.engine.agent_by_identity(identity)
            if current and current.get('status') in ('idle', 'done'):
                self.completion_evidence.consider(identity, text, current.get('cwd'))
            from .linking import quota_banner
            if current:
                current["launch"] = self.observations.card(identity)
                self.quota_stops.observe(current, text, self.account_quotas().get("accounts", []))
            screen_hash = fingerprint(text)
            # Only the provider's own banner at the bottom of the screen of an agent that is not
            # working raises a wall; a working agent or a screen without it clears its own walls.
            banner = quota_banner(text) and not (current and current.get('status') == 'working')
            if banner and self._quota_screens.get(identity) != screen_hash:
                self._quota_screens[identity] = screen_hash
                provider = identity.split(':', 1)[0]
                self.observations.wall(provider, identity, premium_only=provider == 'cursor' and 'premium' in text.lower(),
                                       timeout=float(self.settings.get('routing.quota_wall_seconds', 3600)))
            elif not banner:
                self._quota_screens.pop(identity, None)
                self.observations.clear_walls_of(identity)

    def _layout_loop(self):
        while not self.stop.is_set():
            try:
                with self.engine._lock:
                    allowed = {a['identity'] for a in self.engine.agents.values()
                               if a.get('present') and a.get('status') in ('done', 'idle')
                               and self.settings.watched(a.get('workspace_id'))}
                before = self.completion_evidence.facts()
                self.completion_evidence.resolve(allowed)
                if before != self.completion_evidence.facts():
                    self.engine.poke()
            except Exception:
                log.exception('layout evidence check failed')
            self.completion_evidence.wake.wait(5)
            self.completion_evidence.wake.clear()

    def decorate_layout(self, view):
        from .layout import findings
        with self._layout_lock:
            snapshot = {k: [a for a in rows if self.settings.watched(a.get('workspace_id'))]
                        for k, rows in self._layout_snapshot.items()}
            policy = dict(self.layout_policy)
        hints = findings(**snapshot, policy=policy, cards=view['agents'], completions=self.completion_evidence.facts())
        view['layout'] = {'findings': hints, 'max_panes_per_tab': policy.get('max_panes_per_tab', 4)}
        for workspace in view['workspaces']:
            workspace['layout_hints'] = [h for h in hints if h['workspace_id'] == workspace['id']]
            known = {t['id'] for t in workspace.get('tabs', [])}
            # Shell panes still count toward the cap; empty-agent tabs can have layout findings.
            for tab in snapshot['tabs']:
                if tab.get('workspace_id') == workspace['id'] and tab['tab_id'] not in known:
                    workspace['tabs'].append({'id': tab['tab_id'], 'label': tab.get('label') or 'unnamed tab', 'agents': []})
            for tab in workspace.get('tabs', []):
                tab['layout_hints'] = [h for h in workspace['layout_hints'] if h['tab_id'] == tab['id']]

    # worker ------------------------------------------------------------------

    def work_loop(self) -> None:
        next_tick = 0.0
        next_prune = 0.0
        while not self.stop.is_set():
            try:
                item = self.queue.get(timeout=1.0)
            except queue.Empty:
                item = None
            try:
                # A bad individual event must not starve periodic discovery.
                self.probe_workspaces()
                self.reconcile_due()
                if item and item[0] == "snapshot":
                    self._reconcile_requested.set()
                elif item and item[0] == "event":
                    self.handle_event(item[1])
                now = time.monotonic()
                if now >= next_tick:
                    next_tick = now + TICK_SECONDS
                    self.poll_output()
                    self.engine.tick()
                    self.notifier.tick()
                    self.flush_replies()
                    self.queue_summaries()
                    if self.sidebar:
                        try:
                            # Built only when the sidebar will draw it (not when off or passive), and shared.
                            self.sidebar.update(lambda: self.view(local=True, cached=True), enabled=self.settings.get('sidebar.enabled', True))
                        except HerdrError as exc:
                            log.warning('Herdr sidebar: %s', exc)
                if now >= next_prune:
                    next_prune = now + PRUNE_SECONDS
                    self.engine.prune()
                    self.store.prune_sessions(self.clock())
            except (HerdrError, OSError) as exc:
                log.warning("worker: %s", exc)
            except Exception:  # keep the collector alive; the journal gets the trace
                log.exception("worker: unexpected error")

    # App interface for the API ---------------------------------------------------

    def _names(self) -> dict:
        view = self.engine.view()
        return {a["identity"]: {"name": a["name"], "agent": a["agent"], "workspace_label": a["workspace_label"]}
                for a in view["agents"]}

    def dashboard_url(self) -> str:
        """Address for deep links from the desktop: the configured dashboard
        URL, else the local web server when it is on, else ""."""
        url = (self.settings.get("notify.dashboard_url") or "").rstrip("/")
        if url:
            return url
        if self.settings.get("phone.enabled", False):
            bind = self.settings.get("phone.bind")
            host = CONTRACT["listeners"]["phone"]["bind"] if bind in ("0.0.0.0", "::", "") else bind
            return f"http://{host}:{self.settings.get('phone.port')}"
        return ""

    # v0.9.8.7 (load speed): with ~700 subagent cards one view took ~3 s on a busy laptop, and every
    # browser poll, agent page refresh and the sidebar tick built its own. Readers that only read share
    # one recent build: reused while the engine version is unchanged (at most VIEW_TTL old), or for
    # VIEW_FRESH seconds regardless; any POST/DELETE from the dashboard drops it.
    VIEW_FRESH = 2.0
    VIEW_TTL = 10.0
    VIEW_SLOW_LOG = 1.5

    def invalidate_view(self) -> None:
        self.__dict__.setdefault("_view_cache", {}).clear()

    def view(self, *, local: bool, cached: bool = False) -> dict:
        """The dashboard view. ``cached`` readers must not change what they get (it is shared)."""
        if not cached:
            return self._build_view(local=local)
        cache = self.__dict__.setdefault("_view_cache", {})
        lock = self.__dict__.setdefault("_view_lock", threading.Lock())
        with lock:   # one build at a time: a second reader waits and takes the same result
            hit = cache.get(local)
            now = time.monotonic()
            if hit and (now - hit[0] < self.VIEW_FRESH or (now - hit[0] < self.VIEW_TTL and hit[1] == self.engine.version)):
                return hit[2]
            version = self.engine.version
            view = self._build_view(local=local)
            took = time.monotonic() - now
            cache[local] = (now, version, view)
        if took > self.VIEW_SLOW_LOG and now - self.__dict__.get("_view_slow_at", 0) > 300:
            self._view_slow_at = now
            log.info("view build took %.1f s (%d agents)", took, len(view.get("agents", [])))
        return view

    def _build_view(self, *, local: bool) -> dict:
        view = self.engine.view(include_all_workspaces=local)
        view["presence"] = self.presence.state()
        view["notify"] = self.notifier.status()
        view["summaries"] = self.summarizer.public_config()
        from .writing import public_config as writing_config
        view["writing"] = writing_config(self.settings)
        from .writing import SECTION_NAMES
        view["content_labels"] = SECTION_NAMES
        view["notify_gate"] = self.notifier.gate_status() if hasattr(self.notifier, "gate_status") else {"enabled": False, "recent": []}
        from . import notify_tiers as nt
        sample = self.store.recent_events(1000)
        view["notify_tiers"] = nt.public_view(
            self.settings, sample, now=self.clock(),
            links=self.engine.notify_links(), capped=len(sample) >= 1000)
        from . import notify_prefs as np
        view["phone_prefs"] = np.public_view(self.settings, self.clock(),
                                             sent_24h=self.store.notifications_since(self.clock() - 86400))
        view["settings"] = self.settings.editable_snapshot(from_phone=not local)
        view["features"] = {
            "acknowledge": bool(self.settings.get("ui.acknowledge", True)),
            "explain": self.settings.get("explain.backend", "none") != "none",
            "steering": False,
            "reply": True,
            "reply_max_text": REPLY_MAX_TEXT,
        }
        if local:   # the desktop plugin opens deep links in the browser
            view["features"]["dashboard_url"] = self.dashboard_url()
            view["desktop"] = {"url": desktop_url(self.settings) if self.settings.get("desktop.enabled") else "",
                               "notifications_hash": hash_path("notifications"),
                               "notification_hash": CONTRACT["hashes"]["notification"]}
        view["collector"]["version"] = __version__
        view["status_colors"] = self.status_colors()
        view["theme"] = herdr_theme.current()   # the dashboard colours, read live from the Omarchy theme
        view["questions"] = sorted([a["question"] for a in view["agents"] if a.get("question")], key=lambda q: q["at"])

        # One engine view for both (was three: about half of a view build with 700 subagent cards).
        notify_agents = self._notification_agents()
        view["notifications"] = {"unread": self.notifications_unread(notify_agents),
                                 "items": self._notification_items(self._notification_rows(30), notify_agents)}
        self.decorate_links(view)
        from .questions import build_questions
        answered={row[0] for row in self.store._exec("SELECT q.question_id FROM question_answers q LEFT JOIN replies r ON r.id=q.reply_id WHERE q.reply_id IS NULL OR r.status IN ('sent','queued')").fetchall()}
        view["questions"]=build_questions(view,self.settings,self.clock(),answered)
        self.question_images.decorate(view)
        from .reply_routing import decorate as decorate_replies
        decorate_replies(view, self.replies)
        self._merge_report_asks(view)
        from .asks import decorate as decorate_asks
        decorate_asks(view)
        self.decorate_layout(view)
        # Arrange: Alex's order for accounts and main blocks, read by every surface.
        view["layout"].update(self.settings.arrangement())
        self.quota_stops.decorate(view, self.settings.get("notify_tiers.quota_stops", True))
        from .classify import STATE_LABELS
        view["state_definitions"] = [{"id": s, "label": label, "short": {"needs_you":"need you","done":"done"}.get(s,label.lower())} for s,label in STATE_LABELS.items()]
        self.account_proofs.decorate(view["agents"])   # before quotas: proven agents count on their account
        view["quotas"] = self.account_quotas(view["agents"])
        from .team_reports import decorate as decorate_reports
        decorate_reports(view,self.summarizer,self.settings)
        from .content import decorate_subagents
        decorate_subagents(view)
        # Account icons last: quota accounts, agents with a recorded account, their questions.
        from .account_icons import decorate as decorate_icons
        decorate_icons(view, self.settings.account_icons(), self.account_images)
        # Cheap: unseen count and a few new items for the badge and in-app toasts. Never starts a scan.
        view["visuals"] = self.visuals.snapshot(view)
        return view

    def account_quotas(self, agents=None) -> dict:
        """Mirror of the Omarchy agent-usage records, read-only."""
        try:
            snapshot = account_quotas.read_quotas(
                usage_dir=self.settings.get("quotas.usage_dir") or None,
                order_path=self.settings.get("quotas.order_path") or None,
                tz_name=self.settings.get("quotas.timezone") or account_quotas.DEFAULT_TIMEZONE,
                now=self.clock(),
            )
            return account_quotas.enrich(snapshot, agents or [], self.settings.get("quotas.header_accounts", []),
                                         self.settings.arrangement(), self.settings.quota_view())
        except Exception:
            # A bad record must never take the rest of the dashboard down.
            return {
                "source": "omarchy-agent-usage",
                "dir": "",
                "timezone": self.settings.get("quotas.timezone") or account_quotas.DEFAULT_TIMEZONE,
                "generated_at": None,
                "accounts": [],
                "error": "could not read usage records",
            }

    def refresh_quotas(self) -> dict:
        if not self._quota_refresh_lock.acquire(blocking=False):
            return {"ok": False, "detail": "Quota refresh already running"}
        try:
            return account_quotas.refresh_records(self.settings.get("quotas.usage_dir") or None)
        finally:
            self._quota_refresh_lock.release()
            self.engine.poke()

    def decorate_links(self, view):
        by_id = {a['identity']: a for a in view['agents']}
        virtual = self.store._exec('SELECT identity, parent, status, at FROM hook_agents ORDER BY at').fetchall()
        # A closed, retained native parent still provides a place to open its fold.
        for parent in {row[1] for row in virtual} - set(by_id):
            detail = self.engine.agent_detail(parent)
            if detail:
                card = dict(detail['agent'], tier=None, question=None, awaiting_user=False)
                by_id[parent] = card
                view['agents'].append(card)
        pending = list(virtual)
        relations_state = self.relations.state() if virtual else None   # once per view, not per subagent
        # Resolve nested hook-only subagents without depending on event ordering.
        while pending:
            progressed = False
            for row in list(pending):
                identity, parent, status, at = row
                if identity in by_id:
                    pending.remove(row)
                    continue
                if parent not in by_id:
                    continue
                base = by_id[parent]
                card = {**base, 'identity': identity, 'name': 'subagent ' + identity.rsplit('/', 1)[-1][:12],
                        'pane_id': None, 'terminal_id': None, 'session_identity': identity, 'acked': False,
                        'in_process_parent': parent, 'quota_stop': None, 'quota_wall': None,
                        'title': 'In-process subagent', 'status': status, 'state': status,
                        'state_label': status.capitalize(), 'kind': status, 'event_id': None, 'event_at': None,
                        'summary': None, 'question': None, 'tier': None, 'awaiting_user': False, 'prompt_text': None, 'evidence': {}, 'confidence': 'high', 'metrics': {},
                        'asks': None, 'label': 'In-process subagent ' + status, 'last_activity_at': at, 'roles': self.relations.labels_for(identity, relations_state), 'in_process': True, 'waiting_for': None}
                by_id[identity] = card
                view['agents'].append(card)
                pending.remove(row)
                progressed = True
            if not progressed:
                break
        if virtual:
            with self.engine._lock:
                view['links'] = self.engine._links(set(by_id))
                # The links are new: give them their last action again (flow.py).
                flow.decorate(view['agents'], view['links'], self.engine.agents, self.engine.clock(),
                              fresh_seconds=trace_fresh_seconds(self.settings), patterns=self.settings.get("output.redact", []))
        walls = self.observations.walls()
        for card in view['agents']:
            card['launch'] = self.observations.card(card['identity'])
            card['quota_wall'] = next((w for w in walls if w['identity'] == card['identity']), None)
        agent_runtime.decorate(view['agents'])   # service, model, effort: recorded values only
        for workspace in view['workspaces']:
            members = [a for a in view['agents'] if a['workspace_id'] == workspace['id']]
            workspace['agent_count'] = len(members)
            workspace['live_agent_count'] = sum(1 for a in members if a.get('present') and a.get('pane_id'))
            for tab in workspace.get('tabs', []):
                tab['agents'] = [a['identity'] for a in members if a.get('tab_id') == tab['id']]

    def launch_action(self, body, *, bind=False):
        if bind:
            self.observations.bind(body.get('run_id'), body.get('session'))
            result = {'ok': True}
        else:
            result = self.observations.launch(body)
        self.engine.poke()
        return result

    def hook_event(self, body):
        result = self.observations.hook(body)
        self.engine.poke()
        return result

    def status_colors(self) -> dict:
        """Herdr's status colours for the current theme (re-read every minute)."""
        checked, theme = self._theme
        if time.monotonic() - checked > 60 or not theme:
            try:
                theme = herdr_theme.resolve()
            except Exception:  # never let theme reading break the dashboard
                theme = {"colors": {}, "source": "could not read Herdr's theme"}
            self._theme = (time.monotonic(), theme)
        return theme

    def history(self, limit: int) -> list[dict]:
        return self.engine.history(limit)

    def _refresh_last_assistant(self, identity: str) -> None:
        if not self.settings.get("traces.enabled", True):
            return
        try:
            # v0.9.8.1: a deeper first read after a start, so an ask from before it is found.
            deep = identity not in self._deep_read
            page = self.traces.conversation(identity, 120 if deep else 30, None, self.settings.get("output.redact", []))
            self._deep_read.add(identity)
        except (OSError, ValueError):
            return
        entries = page.get("entries") or []
        last_a = next((e for e in reversed(entries) if e.get("role") == "assistant"), None)
        last = entries[-1] if entries else None
        if last_a:
            # v0.9.8.1: the assistant messages since Alex's own last turn (workers' messages to a
            # coordinator, "name: …", do not count as Alex's), for asks buried in an earlier report.
            from .asks import is_item_answer
            names = self.engine.agent_names()
            since, alex_at = 0, None
            for i, e in enumerate(entries):
                # v0.9.8.6: a card answer ('Answer to "…": yes') resolves that one item only; the
                # agent's other open asks stay until each is answered (or Alex writes freely).
                if e.get("role") == "user" and not is_agent_message(e.get("text") or "", names) \
                        and not is_item_answer(e.get("text") or ""):
                    since, alex_at = i + 1, e.get("at") or 0
            earlier = [e for e in entries[since:] if e.get("role") == "assistant" and e is not last_a]
            if last is not last_a and any(e is last_a for e in entries[since:]):
                earlier.append(last_a)   # a worker wrote last: the last message's asks stay too
            self.engine.set_last_assistant(
                identity, last_a.get("text") or "", last_a.get("at"),
                (last or {}).get("role") or "assistant", earlier=earlier, alex_at=alex_at,
            )

    def flush_replies(self) -> None:
        view = {a["identity"]: a for a in self.engine.view()["agents"]}
        def live_recipient(identity):
            card = view.get(identity)
            if not card:
                return None
            try:
                live = self.client.agent(card['pane_id']) or {}
            except HerdrError:
                return dict(card, status='unknown')
            from .engine import identity_of
            if identity_of(live) != identity:
                return None
            return dict(card, status=live.get('agent_status') or 'unknown')
        if self.replies.tick(live_recipient):
            self.engine.poke()

    def preview_reply(self, identity: str, text: str, quote: str | None = None) -> dict:
        agent = self._reply_agent(identity)
        from .reply_routing import target_info
        return dict(self.replies.preview(agent, text, quote), recipient=target_info(agent))

    def submit_reply(self, identity: str, *, text: str, quote: str | None, when: str,
                     interrupt: bool, confirm: bool, event_id: int | None = None, question_id: str | None = None, recipient_identity: str | None = None, option: str | None = None, terminal_text: bool = False,
                     acknowledge: bool = True) -> dict:
        with self._reply_submit_lock:
            question = None
            source = self.engine.agent_by_identity(identity)
            if question_id:
                question=next((q for q in self.view(local=False)["questions"] if q["id"]==question_id and q["identity"]==identity),None)
                if not question:
                    raise ValueError("That question is no longer open. Refresh before replying.")
            agent = self._reply_agent(identity)
            if when == 'auto':
                live = self.client.agent(agent['pane_id']) or {}
                from .engine import identity_of
                if identity_of(live) != agent['identity']:
                    raise ValueError('The recipient changed. Reopen Reply before sending.')
                agent = dict(agent, status=live.get('agent_status') or 'unknown')
                when = 'now' if agent['status'] in ('idle', 'done') else 'when_waiting'
                interrupt = False
            if recipient_identity and recipient_identity != agent['identity']:
                raise ValueError('The recipient changed. Reopen Reply before sending.')
            if option is not None or terminal_text:
                result = self.answer_choice(agent, question, option if option is not None else text, confirm, literal=terminal_text)
            else:
                result = self.replies.submit(agent, text=text, quote=quote, when=when,
                                             interrupt=interrupt, confirm=confirm, about=identity)
            from .reply_routing import target_info
            result['recipient'] = target_info(agent)
            # A confirmed reply answers the notification: mark it read (the given one,
            # else the agent's current attention event), only after a successful submit.
            if result.get("status") in ("sent", "queued") and question_id:
                self.store._exec("INSERT OR REPLACE INTO question_answers VALUES (?,?,?)",(question_id,self.clock(),result.get("id")))
            if acknowledge and result.get("status") in ("sent", "queued") and self.settings.get("ui.acknowledge", True):
                target = event_id if event_id is not None else (source or agent).get("event_id")
                if target:
                    result["acked"] = bool(self.ack(int(target)))
            self.engine.poke()
            return result

    def answer_ask(self, item_id: str, text: str) -> dict:
        """Answer one question item (v0.9.6). It goes through the reply routing to the
        item's answer_to and names the question; a busy recipient gets it queued, never
        interrupted. The other items stay open: the notification is marked read only with
        the last open item."""
        from . import asks
        text = (text or "").strip()
        if not text:
            raise ValueError("The answer is empty. Nothing was sent.")
        with self._ask_lock:
            agent, item = asks.find(self.view(local=False), item_id)
            if not item:
                raise ValueError("That question is no longer open. Refresh before answering.")
            if not asks.is_open(item):
                raise ValueError("That question is already answered.")
            target = item.get("answer_to") or {}
            if not target.get("identity"):
                raise ValueError(f"{target.get('error') or 'The recipient is unavailable.'} Nothing was sent.")
            last = not any(asks.is_open(other) for other in agent["asks"]["items"] if other["id"] != item_id)
            question = agent.get("question") or {}
            if item["kind"] == "permission" and target["identity"] == agent["identity"] and question.get("terminal"):
                options = question.get("options") or []
                option = next((o for o in options if o == text), None)
                if options and option is None:
                    raise ValueError("Choose one of the dialog's own options.")
                result = self.submit_reply(agent["identity"], text=text, quote=None, when="auto", interrupt=False,
                                           confirm=True, question_id=question.get("id"), recipient_identity=target["identity"],
                                           option=option, terminal_text=not options, acknowledge=last,
                                           event_id=agent.get("event_id"))
            else:
                result = self.submit_reply(agent["identity"], text=asks.answer_text(item, text), quote=None, when="auto",
                                           interrupt=False, confirm=True, recipient_identity=target["identity"],
                                           acknowledge=last, event_id=agent.get("event_id"))
            if result.get("status") in ("sent", "queued"):
                asks.save_answer(self.store, item_id, agent["identity"], text, self.clock(), result.get("id"))
            result["item"] = item_id
            result["answered"] = asks.load_answers(self.store).get(item_id)
            self.engine.poke()
            return result

    def answer_choice(self, agent, question, option, confirm, literal=False):
        from .choice_reply import menu_keys, numbered_rows
        if not confirm:
            raise ValueError('An explicit Send action is required.')
        if not question or question['identity'] != agent['identity']:
            raise ValueError('Choices can only answer the recipient’s current dialog. Reply to the coordinator instead.')
        live = self.client.agent(agent['pane_id']) or {}
        if live.get('agent_status') != 'blocked':
            raise ValueError('The agent is no longer waiting at that dialog.')
        # Verify native session identity as well as pane/status before key delivery.
        from .engine import identity_of
        if identity_of(live) != agent['identity']:
            raise ValueError('The agent in that pane changed. Refresh before answering.')
        screen = self.client.read_output(agent['pane_id'], lines=40, source='visible')
        cleaned, _ = redact(readable(screen), self.settings.get('output.redact', []))
        if question.get('dialog_revision') != fingerprint(cleaned):
            self.engine.set_blocked_rule(agent['identity'], (self.engine.agent_by_identity(agent['identity']) or {}).get('blocked_rule'), cleaned)
            raise ValueError('The choice dialog changed. Refresh before answering.')
        provider = agent['identity'].split(':', 1)[0]
        # Queued Codex questions open with Shift+Left; only an explicit answer
        # invokes it. Re-read the dialog before selecting anything.
        import re
        queued = provider == 'codex' and re.search(r'(?i)shift\s*\+\s*(?:left|←)', readable(screen)) and not numbered_rows(screen)
        if queued:
            from .reply import HerdrPrompt
            opener = getattr(self.replies, '_key_sender', None) or HerdrPrompt(self.settings.get('herdr.socket') or None).send_keys
            opener(agent['pane_id'], ['shift+left'])
            for _ in range(5):
                screen = self.client.read_output(agent['pane_id'], lines=40, source='visible')
                if question['text'].casefold() in readable(screen).casefold():
                    break
                time.sleep(0.1)
        current = self.client.agent(agent['pane_id']) or {}
        if identity_of(current) != agent['identity'] or current.get('agent_status') != 'blocked':
            raise ValueError('The recipient left the question. Nothing was answered.')
        if literal:
            from .reply import MAX_TEXT
            if not question.get('terminal') or question.get('options') or numbered_rows(screen) or not option.strip() or len(option) > MAX_TEXT or question['text'].casefold() not in readable(screen).casefold():
                raise ValueError('This is not the current free-text question. Choose a dialog option instead.')
            return self.replies.answer_choice(agent, option, ['enter'], confirm, literal=option)
        keys = menu_keys(question, option, screen, provider)
        return self.replies.answer_choice(agent, option, keys, confirm)

    def cancel_reply(self, reply_id: int, identity: str) -> dict | None:
        result = self.replies.cancel(reply_id, identity)
        if result:
            self.engine.poke()
        return result

    def replies_for(self, identity: str) -> dict | None:
        if self.engine.agent_by_identity(identity) is None:
            return None
        return {"replies": self.replies.for_agent(identity)}

    def _reply_agent(self, identity: str) -> dict:
        from .reply_routing import recipient
        return recipient(self.engine.view(), identity)

    def latest_visuals(self, identity=None, workspace=None, team=False, refresh=False,
                       before=None, limit=None, hub=False):
        view=self.view(local=False, cached=True)
        if team and identity:
            from .team_reports import member_ids
            ids=member_ids(view,identity)
            if not ids:raise ValueError("no such watched team")
            # Keep all workspace metadata for authorization; restrict discovery to the team.
            view["agents"]=[a for a in view["agents"] if a["identity"] in ids]
            identity=None
        if hub or (before is not None or limit is not None) or (
                not identity and not workspace and not team and self.visuals.catalog and not refresh):
            if refresh or not self.visuals.catalog:
                self.visuals.ingest(view)
            return self.visuals.feed(view, identity=identity, workspace=workspace,
                                     before=before, limit=limit or 40)
        # Bounded and cached; "partial" tells the page the scan stopped at its budget or a cap.
        result=self.visuals.scan(view,identity,workspace,refresh)
        self.visuals.merge_catalog(result['items'])
        return self.visuals.decorate_result(view, result)

    def visual_file(self, media_id, thumbnail=False):
        return self.visuals.read(self.view(local=False, cached=True),media_id,thumbnail)

    def visual_thumbnail_etag(self, media_id):
        return self.visuals.thumbnail_etag(media_id)

    def visual_filename(self, media_id):
        return self.visuals.filename(media_id)

    def mark_visuals_seen(self, media_ids=None, all=False):
        now=self.clock()
        if all:
            marked=self.store.mark_visuals_seen_all(now, list(self.visuals.catalog))
        else:
            if not isinstance(media_ids, list) or len(media_ids) > 200:
                raise ValueError("ids must be a short list")
            marked=self.store.mark_visuals_seen(media_ids, now)
        self.invalidate_view()
        summary=self.visuals.snapshot(self.engine.view())
        return {"ok": True, "marked": marked, "unseen": summary["unseen"]}

    # Account icons (v0.9.7): Alex's own image per account; account_icons.py has the rules.
    def _known_account(self, account: str) -> str:
        from .quotas import _account_id
        account = _account_id(account)
        if account not in {a["id"] for a in self.account_quotas().get("accounts", [])}:
            raise LookupError(account)
        return account

    def _save_icon_entry(self, account: str, entry: dict | None) -> None:
        icons = self.settings.account_icons()
        if entry:
            icons[account] = entry
        else:
            icons.pop(account, None)
        self.update_settings({"accounts.icons": icons}, local=False)

    def account_icon_file(self, account: str):
        from .quotas import _account_id
        account = _account_id(account)
        if not (self.settings.account_icons().get(account) or {}).get("image"):
            raise LookupError(account)
        return self.account_images.open(account)

    def account_icon_upload(self, account: str, data: bytes) -> dict:
        account = self._known_account(account)
        version = self.account_images.save(account, data)
        self._save_icon_entry(account, {**self.settings.account_icons().get(account, {}), "image": True})
        return {"ok": True, "account": account, "version": version}

    def account_icon_delete(self, account: str) -> dict:
        from .quotas import _account_id
        account = _account_id(account)
        removed = self.account_images.delete(account)
        entry = {k: v for k, v in self.settings.account_icons().get(account, {}).items() if k != "image"}
        self._save_icon_entry(account, entry)
        return {"ok": True, "account": account, "removed": removed}

    def question_image(self, token, thumbnail=False):
        return self.question_images.read(self.view(local=False), token, thumbnail)

    def agent_detail(self, identity: str) -> dict | None:
        detail = self.engine.agent_detail(identity)
        if detail is None:
            virtual = next((a for a in self.view(local=False, cached=True)['agents']
                            if a['identity'] == identity and a.get('in_process')), None)
            if not virtual:
                return None
            return {'agent': dict(virtual), 'events': [], 'activity': [], 'relations': [],
                    'summary': None, 'fallback': {'text': 'In-process subagent observed by a lifecycle hook.'}}
        decorated = next((a for a in self.view(local=False, cached=True)['agents'] if a['identity']==identity), None)
        if decorated:
            detail['agent'].update(decorated)
        detail['agent']['launch'] = self.observations.card(identity)
        detail['agent']['runtime'] = agent_runtime.describe(detail['agent'])
        detail['agent']['quota_wall'] = next((w for w in self.observations.walls() if w['identity'] == identity), None)
        detail["summary"] = None if detail["agent"].get("session_collision") else self.summarizer.latest(identity)
        from .content import presentation
        detail["presentation"] = presentation(detail["agent"], detail["summary"])
        detail["fallback"] = self.fallback_summary(identity)
        detail["status_report"] = decorated.get("status_report") if decorated else None
        detail["replies"] = self.replies.for_agent(identity)
        return detail

    def fallback_summary(self, identity: str) -> dict:
        """Not AI: the agent's own last message, shortened, for when no summary exists."""
        agent = self.engine.agent_by_identity(identity) or {}
        if agent.get("session_collision"):
            return {"available": False, "reason": "shared session trace cannot be attributed to this pane"}
        if not self.settings.get("traces.enabled", True):
            return {"available": False}
        page = self.traces.conversation(identity, 4, None, self.settings.get("output.redact", []))
        last = next((e for e in reversed(page.get("entries", [])) if e["role"] == "assistant"), None)
        if not last:
            return {"available": False}
        text = " ".join(last["text"].split())
        return {"available": True, "text": text[:400] + ("…" if len(text) > 400 else ""), "at": last["at"],
                "source": page.get("source")}

    def chooser(self) -> list[dict]:
        return self.engine.chooser()

    def read_output(self, identity: str, raw: bool = False) -> dict | None:
        agent = self.engine.agent_by_identity(identity)
        if agent is None:
            return None
        if not agent.get("present"):
            return {"text": "", "note": "This agent is gone; there is no output to show.", "redactions": 0}
        lines = int(self.settings.get("output.lines", 40))
        try:
            text, source = self.client.read_screen(agent["pane_id"], lines)
        except HerdrError as exc:
            return {"text": "", "note": f"Could not read the terminal ({exc.code or 'error'}).", "redactions": 0,
                    "source": None, "read_at": self.clock()}
        result = clean_output(text if raw else readable(text), self.settings.get("output.redact", []), lines)
        result["source"] = ("visible screen (agent busy)" if source == "visible" else f"last {lines} lines") + \
                           ("" if raw else ", cleaned for reading")
        result["raw"] = raw
        result["read_at"] = self.clock()
        return result

    def read_file(self, identity: str, path: str, against: str | None = None) -> dict:
        """v0.9.8.8 file viewer: one text file a watched agent mentioned (files.py has the rules).
        Relative paths start from that agent's folder and repo. The text is returned to the browser
        only: it is not stored and never reaches the summary agent."""
        from . import files
        agents = list(self.engine.agents.values())
        agent = self.engine.agent_by_identity(identity) if identity else None
        cwd = (agent or {}).get("cwd") or None
        result = files.read(path, cwd, [a.get("cwd") for a in agents])
        if against:
            original = files.read(against, cwd, [a.get("cwd") for a in agents])
            result["against"] = {k: original[k] for k in ("requested", "path", "shown", "name", "mtime")}
            result["diff"] = files.diff(original["text"], result["text"])
        return result

    def terminal(self, identity: str, since: str | None = None, history: bool = False) -> dict | None:
        """v0.9.8: the agent's live screen with colours (read-only). None = not a watched agent."""
        agent = self.engine.agent_by_identity(identity)
        if agent is None:
            return None
        base = {"identity": identity, "read_at": self.clock(), "poll_ms": 1000}
        if not agent.get("present") or not agent.get("pane_id"):
            return {**base, "screen": "", "hash": None, "note": "This agent is gone; there is no terminal to show."}
        try:
            screen = self.terminals.screen(agent["pane_id"])
        except (HerdrError, OSError) as exc:
            code = getattr(exc, "code", None)
            return {**base, "screen": None, "hash": None, "unchanged": True,
                    "note": f"Could not read the terminal ({code or 'error'}); trying again."}
        screen, redactions = redact_ansi(screen, self.settings.get("output.redact", []))
        result = {**base, "hash": digest(screen), "redactions": redactions, "status": agent.get("status")}
        if since and since == result["hash"]:
            result["unchanged"] = True
        else:
            result["screen"] = screen
        if history:
            text = ""
            if agent.get("status") not in ("working",):   # never ask Herdr for history of a busy agent
                try:
                    text = self.terminals.history(agent["pane_id"], plain_terminal(screen).rstrip("\n").count("\n") + 1)
                except (HerdrError, OSError):
                    text = ""
            result["history"], n = redact(text, self.settings.get("output.redact", [])) if text else ("", 0)
            result["redactions"] += n
        return result

    def conversation(self, identity: str, limit: int, before: int | None) -> dict | None:
        """Last ``limit`` user/assistant entries from the agent's trace, on request."""
        agent = self.engine.agent_by_identity(identity)
        if agent is None:
            return None
        if not self.settings.get("traces.enabled", True):
            return {"available": False, "reason": "trace reading is switched off", "entries": []}
        limit = max(1, min(int(limit), int(self.settings.get("traces.conversation_max", 50))))
        result = self.traces.conversation(identity, limit, before, self.settings.get("output.redact", []))
        if agent.get("session_collision"):
            result["warning"] = "Several live panes report this session. This transcript is shared and cannot identify which pane performed an action."
        result["limit"] = limit
        result["read_at"] = self.clock()
        return result

    # declared relationships ------------------------------------------------------

    def resolve_agent(self, ref: str, *, present_only: bool = False) -> str:
        """Identity of a watched agent named by identity, Herdr name or pane id."""
        ref = (ref or "").strip()
        with self.engine._lock:
            candidates = [a for a in self.engine.agents.values()
                          if self.settings.watched(a.get("workspace_id")) and (a.get("present") or not present_only)]
            for match in (lambda a: a["identity"] == ref, lambda a: a.get("present") and a.get("pane_id") == ref,
                          lambda a: a.get("present") and a.get("name") == ref):
                found = [a["identity"] for a in candidates if match(a)]
                if len(found) == 1:
                    return found[0]
                if len(found) > 1:
                    raise RelationError(f"{ref!r} matches several agents; use the identity")
        raise RelationError(f"no agent {ref!r} in a watched workspace")

    def relation_action(self, body: dict, *, local: bool) -> dict:
        """Declare or remove a relationship or label (an admin action, not agent control)."""
        from .linking import valid_identity
        action = body.get("action")
        registered = local and body.get("registered") is True
        provenance = "registered" if registered else "manual"
        actor = str(body.get("actor") or "helper") if registered else ("cli" if local else "ui")
        now = self.clock()

        def ref(name: str) -> str:
            raw = str(body.get(name) or "")
            # The helper may name its caller even if that agent is not watched.
            if registered and valid_identity(raw):
                return raw
            return self.resolve_agent(raw)

        subject = ref("subject")
        kind = body.get("kind")
        if action in ("link", "unlink", "handover"):
            kind = "reports_to" if action == "handover" else kind
            if kind not in LINK_KINDS:
                raise RelationError(f"kind must be one of {', '.join(LINK_KINDS)}")
            obj = ref("object")
            if action == "link":
                self.relations.link(subject, kind, obj, at=now, provenance=provenance, actor=actor, note=body.get("note"))
            elif action == "unlink":
                declared = (subject, kind, obj) in self.relations.state().links
                inferred = kind == 'reports_to' and any(l['from'] == subject and l['to'] == obj
                                                       for l in self.engine.view()['links'] if l.get('inferred'))
                if not declared and not inferred:
                    raise RelationError('no such link')
                # Write suppression first so concurrent readers never see a fallback
                # reappear between removing the declaration and recording the veto.
                if kind == 'reports_to':
                    self.inferences.suppress(subject, now, actor)
                if declared:
                    self.relations.unlink(subject, kind, obj, at=now, provenance=provenance, actor=actor, note=body.get("note"))
            else:
                self.relations.handover(subject, obj, at=now, provenance=provenance, actor=actor)
        elif action in ("set", "clear"):
            if kind not in LABELS:
                raise RelationError(f"kind must be one of {', '.join(LABELS)}")
            if action == "set":
                self.relations.set_label(subject, kind, str(body.get("value") or ""), at=now, provenance=provenance, actor=actor)
            else:
                self.relations.clear_label(subject, kind, body.get("value") or None, at=now, provenance=provenance, actor=actor)
        else:
            raise RelationError("action must be link, unlink, handover, set or clear")
        self.engine.poke()
        return {"ok": True, "subject": subject, "roles": self.relations.labels_for(subject)}

    def relations_overview(self) -> dict:
        view = self.engine.view()
        return {"links": [l for l in view["links"] if "observed-spawn" not in l["provenance"] or len(l["provenance"]) > 1],
                "agents": {a["identity"]: a["roles"] for a in view["agents"] if any(a["roles"].values())}}

    def ack(self, event_id: int) -> bool:
        event = self.store.event(event_id)
        if not self.engine.ack(event_id):
            return False
        if event["type"] == "quota_stop":
            self.quota_stops.ack(event["identity"], event_id=event_id)
        return True

    def ack_quota_stops(self, identities):
        for identity in identities:
            if self.engine.agent_by_identity(identity) is not None:
                self.quota_stops.ack(identity)
        self.engine.poke()
        return {"ok": True}

    # Notifications view (v0.8.2) --------------------------------------------

    def _notification_rows(self, limit: int, unread_only: bool = False) -> list[dict]:
        from .notifications import LISTED_STATES
        watched = list(self.settings.get("watch.enabled_workspaces", []) or [])
        return self.store.notification_events(LISTED_STATES, watched, limit=limit, unread_only=unread_only)

    def _notification_agents(self) -> dict:
        current = self.engine.view()
        self.quota_stops.decorate(current)
        return {a["identity"]: a for a in current["agents"]}

    def _notification_items(self, events: list[dict], agents: dict | None = None) -> list[dict]:
        from .notifications import build_items
        gate = getattr(self.notifier, "gate", None)
        gates = gate.decisions_by_event([e["id"] for e in events]) if gate and events else {}
        # v0.9.8.7: callers that need items twice pass the agents in; each engine view is costly.
        agents = agents if agents is not None else self._notification_agents()
        texts = {}
        for event in events:
            text = self.store.notification_text(event["id"])
            gate_text = gates.get(event["id"]) or {}
            if not text and gate_text.get("decision") == "send" and gate_text.get("title"):
                self.store.save_notification_text([event["id"]], gate_text["title"], gate_text.get("body") or "", "ai", event["at"])
                text = self.store.notification_text(event["id"])
            if text:
                texts[event["id"]] = text
        return build_items(events, agents=agents, links=self.engine.notify_links(), gates=gates, texts=texts,
                           settings=self.settings, now=self.clock())

    def notifications(self, limit: int = 50) -> dict:
        from .notifications import row_counts
        limit = max(1, min(200, int(limit)))
        agents = self._notification_agents()
        items = self._notification_items(self._notification_rows(limit), agents)
        return {"items": items, "counts": row_counts(items), "unread": self.notifications_unread(agents)}

    def notification(self, event_id: int) -> dict | None:
        event = self.store.event(event_id)
        from .notifications import LISTED_STATES
        if not event or event.get("notify_state") not in LISTED_STATES or not self.engine.watched(event.get("workspace_id")):
            return None
        items = self._notification_items([event])
        return items[0] if items else None

    def notifications_unread(self, agents: dict | None = None) -> int:
        """Unread among the rows the Notifications screen shows (was: any unread in the last 1000,
        so old items outside the list kept the badge on with nothing to see)."""
        from .notifications import LIST_LIMIT, row_counts
        return row_counts(self._notification_items(self._notification_rows(LIST_LIMIT), agents))['unread']

    def notifications_read_all(self) -> int:
        count = 0
        for event in self._notification_rows(1000, unread_only=True):
            if self.ack(event["id"]):
                count += 1
        return count

    def notify_setup(self) -> dict:
        """Authenticated ntfy onboarding payload. The topic is included here
        and must never be logged."""
        from .notify import ntfy_setup
        password = self.keyring.get("ntfy-phone-password")
        payload = ntfy_setup(self.settings, include_secrets=True, phone_password=password)
        ready, why = self.notifier.sender.ready()
        # Re-check enabled/config even if the in-memory sender predates a toggle.
        if not self.settings.get("notify.enabled", True):
            ready, why = False, "phone alerts are switched off"
        elif self.settings.get("notify.backend") != "ntfy":
            ready, why = False, "phone alerts are off (notify.backend = none)"
        payload["ready"] = ready
        payload["detail"] = why
        return payload

    def notify_test(self) -> dict:
        """Send a test alert through the configured ntfy sender."""
        from .notify import make_sender
        sender = make_sender(self.settings, self.keyring)
        ready, why = sender.ready()
        if not ready:
            return {"ok": False, "detail": why}
        try:
            sender.send(
                title="Waiting on you (test)",
                message="This is a test of the new notification-tier format. No agent data.\n\nWaiting on you · Phone (ntfy)",
                priority="default", tags=["test_tube"],
                click=self.settings.get("notify.dashboard_url", "") or "")
        except Exception as exc:
            return {"ok": False, "detail": str(exc)[:200]}
        return {"ok": True, "detail": "the ntfy server accepted the test"}

    def settings_view(self, *, local: bool) -> dict:
        from .config import EDITABLE
        return {"settings": self.settings.editable_snapshot(from_phone=not local),
                "editable": [k for k, (_, phone_ok) in EDITABLE.items() if local or phone_ok]}

    def update_settings(self, changes: dict, *, local: bool) -> dict:
        with self._settings_lock:
            if "notify.snooze" in changes:
                # Phone alerts > Snooze: 1h | morning | off, saved as notify.snooze_until.
                from . import notify_prefs as np
                changes = dict(changes)
                changes["notify.snooze_until"] = np.snooze_value(changes.pop("notify.snooze"), self.settings, self.clock())
            if any(str(k).startswith("notify_tiers.") for k in changes):
                from . import notify_tiers as nt
                changes = nt.expand_changes(self.settings, changes)
            validated = {key: self.settings.validate(key, value, from_phone=not local) for key, value in changes.items()}
            resync = any(k == "watch.enabled_workspaces" and v != self.settings.get(k) for k, v in validated.items())
            for key, value in validated.items():
                self.settings.set_override(key, value)
                self.store.save_setting(key, value)
        self.engine.settings_changed()
        if resync:
            self.request_resync()
        return self.settings_view(local=local)

    def reset_settings(self) -> dict:
        with self._settings_lock:
            self.store.clear_settings()
            self.settings.clear_overrides()
        self.engine.settings_changed()
        self.request_resync()
        return self.settings_view(local=True)

    def report_presence(self, state: str, idle_seconds: float) -> None:
        self.presence.report(state, idle_seconds)
        self.engine.poke()

    def explain(self, identity: str) -> dict:
        return {"available": False,
                "message": "Explain is not set up yet: the backend is still to be chosen. Nothing was sent anywhere."}

    def wait_for_change(self, since: int, timeout: float) -> int:
        return self.engine.wait_for_change(since, timeout)

    def check_passphrase(self, candidate: str) -> bool | None:
        stored = self.keyring.get("dashboard-passphrase")
        if not stored:
            return None
        return bool(candidate) and hmac.compare_digest(candidate.encode(), stored.encode())

    def _visuals_loop(self):
        """Background discovery for the global hub and in-app toasts. Never blocks /api/view."""
        # A short first wait so startup (Herdr, traces) can settle before a 4s scan.
        while not self.stop.wait(2.0):
            try:
                interval = float(self.settings.get("visuals.poll_seconds", 30) or 30)
            except (TypeError, ValueError):
                interval = 30.0
            interval = max(8.0, min(180.0, interval))
            try:
                view = self.engine.view()
                before = set(self.visuals.catalog)
                self.visuals.ingest(view)
                new = [item for item in self.visuals.catalog.values() if item["id"] not in before]
                if new:
                    self.invalidate_view()
                    self._maybe_push_visuals(new)
                # Warm a few previews so the hub does not stall on first open.
                try:
                    recent = sorted(self.visuals.catalog.values(), key=lambda i: i.get("at") or 0, reverse=True)[:24]
                    self.visuals.prepare_thumbnails(view, recent, limit=8)
                except Exception:
                    log.exception("visuals thumbnails")
            except Exception:
                log.exception("visuals scan")
            if self.stop.wait(interval):
                return

    def _maybe_push_visuals(self, items):
        """Phone push for new visuals, off by default. Respects Phone alerts settings."""
        from . import notify_prefs as np
        if not items or not np.enabled(self.settings, "visuals"):
            return
        try:
            self.notifier.announce_visuals(items)
        except Exception:
            log.exception("visuals push")
