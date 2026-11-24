"""Passive, read-only metrics from Claude and Codex session traces.

The trace of an agent is found from Herdr's ``agent_session`` id:

* Claude: ``<claude dir>/<project>/<session-id>.jsonl``
* Codex:  ``<codex dir>/YYYY/MM/DD/rollout-<time>-<session-id>.jsonl``

Only files matching those patterns inside the configured folders are ever
opened. Nothing is written, nothing is sent to any agent, no model is called.
Files are read incrementally from a saved byte offset with a per-pass byte
budget, so a 70 MB trace is caught up over a few passes.

What is extracted (numbers only; no text is kept):

* session start (first entry / ``session_meta``),
* token usage: Claude per-message usage deduplicated by request/message id,
  Codex's cumulative ``total_token_usage`` snapshot (latest wins),
* context size: the last main-thread request (Claude) / ``last_token_usage``
  plus the reported window (Codex),
* compactions (Claude ``compact_boundary``),
* what the agent runs on: the model and the reasoning effort of the latest main-thread request
  (Claude: ``message.model`` and ``effort`` on assistant lines; Codex: ``turn_context`` ``model`` and
  ``effort``). Subagent (sidechain) lines and Claude's ``<synthetic>`` model are skipped.
* verified links: Codex ``parent_thread_id`` (spawned by) and ``forked_from_id``.
* what the agent is waiting on: open tool calls, background tasks, a scheduled wake-up and the
  Herdr commands it sent to other agents (``activity.py``; short descriptions only).

Conversation excerpts are read on request only, backwards from the end of the
file, bounded, user/assistant text only (no reasoning, no tool calls).
"""

from __future__ import annotations

import glob
import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from .activity import Activity
from .sanitize import redact, strip_terminal

UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
SUPPORTED = ("claude", "codex")
MAX_LINE = 8 * 1024 * 1024          # skip absurd single lines rather than buffering them
ENTRY_CHARS = 4000                  # longest conversation entry returned
MAX_SEEN_IDS = 200_000              # dedupe memory bound per trace

# Wrappers the agents inject into "user" turns; they are not what you typed.
_INJECTED = re.compile(r"<(system-reminder|environment_context|user_instructions|permissions instructions)\b.*?</\1>",
                       re.DOTALL)


def parse_time(value) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def split_identity(identity: str) -> tuple[str, str] | None:
    kind, _, session = identity.partition(":")
    session = session.split("@", 1)[0]
    if kind in SUPPORTED and UUID.match(session or ""):
        return kind, session
    return None


class Locator:
    """Finds a trace file by session id, inside the configured folders only."""

    def __init__(self, claude_dirs: list[str], codex_dirs: list[str], retry_seconds: float = 300):
        self.roots = {
            "claude": [Path(os.path.expanduser(d)).resolve() for d in claude_dirs],
            "codex": [Path(os.path.expanduser(d)).resolve() for d in codex_dirs],
        }
        self.retry_seconds = retry_seconds
        self._found: dict[tuple[str, str], Path] = {}
        self._missing: dict[tuple[str, str], float] = {}

    def find(self, kind: str, session: str) -> Path | None:
        if kind not in SUPPORTED or not UUID.match(session):
            return None
        key = (kind, session)
        cached = self._found.get(key)
        if cached and cached.is_file():
            return cached
        if time.monotonic() - self._missing.get(key, -1e9) < self.retry_seconds:
            return None
        pattern = f"*/{session}.jsonl" if kind == "claude" else f"*/*/*/rollout-*-{session}.jsonl"
        for root in self.roots[kind]:
            for match in sorted(glob.glob(str(root / pattern))):
                path = Path(match)
                real = path.resolve()
                if real.is_file() and real.suffix == ".jsonl" and real.is_relative_to(root):
                    self._found[key] = real
                    self._missing.pop(key, None)
                    return real
        self._missing[key] = time.monotonic()
        return None


def _word(value) -> str | None:
    """A model id or effort level as recorded: short printable text, else None.

    Claude writes ``<synthetic>`` as the model of messages it made up itself (errors, interrupts)."""
    if not isinstance(value, str) or not value or len(value) > 80 or value.startswith("<"):
        return None
    return value if value.isprintable() else None


class ClaudeTrace:
    kind = "claude"

    def __init__(self):
        self.first_at = None
        self.last_at = None
        self.seen: set[str] = set()
        self.usage = {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0}
        self.requests = 0
        self.last_request = None   # usage of the latest main-thread request
        self.last_request_at = None
        self.model = None
        self.effort = None
        self.runtime_at = None     # when the model or effort was last seen
        self.compactions = 0
        self.last_compaction_at = None
        self.links: list[dict] = []
        self.activity = Activity()

    def feed(self, entry: dict) -> None:
        at = parse_time(entry.get("timestamp"))
        if at is not None:
            self.first_at = at if self.first_at is None else min(self.first_at, at)
            self.last_at = at if self.last_at is None else max(self.last_at, at)
        kind = entry.get("type")
        if kind == "system" and entry.get("subtype") == "compact_boundary":
            self.compactions += 1
            self.last_compaction_at = at
            return
        if not entry.get("isSidechain"):
            self._track(kind, entry, at)
        if kind != "assistant":
            return
        message = entry.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("usage"), dict):
            return
        # One reply is written as several lines (one per content block) and a
        # retried request repeats; count each request once.
        key = entry.get("requestId") or message.get("id")
        usage = message["usage"]
        tokens = {
            "input": int(usage.get("input_tokens") or 0),
            "cache_write": int(usage.get("cache_creation_input_tokens") or 0),
            "cache_read": int(usage.get("cache_read_input_tokens") or 0),
            "output": int(usage.get("output_tokens") or 0),
        }
        if key and key not in self.seen:
            if len(self.seen) < MAX_SEEN_IDS:
                self.seen.add(key)
            for name, value in tokens.items():
                self.usage[name] += value
            self.requests += 1
        if not entry.get("isSidechain"):
            self.last_request = tokens
            self.last_request_at = at
            # Newer Claude Code writes the effort of the request on each assistant line.
            effort = _word(entry.get("effort")) or _word(entry.get("perTurnEffort"))
            model = _word(message.get("model"))
            if model:
                self.model = model
            if effort:
                self.effort = effort
            if model or effort:
                self.runtime_at = at

    def _track(self, kind, entry: dict, at) -> None:
        if kind == "system":
            if entry.get("subtype") == "turn_duration":
                self.activity.turn_ended()
            elif entry.get("subtype") == "scheduled_task_fire":
                self.activity.wakeup_fired()
            return
        # Background tasks end with a <task-notification>, queued as a prompt for the next turn.
        queued = entry.get("content") if kind == "queue-operation" else (entry.get("attachment") or {}).get("prompt") \
            if kind == "attachment" and isinstance(entry.get("attachment"), dict) else None
        if isinstance(queued, str):
            if "<task-notification>" in queued:
                self.activity.notification(queued)
            return
        message = entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if kind == "assistant" and isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool, args = block.get("name") or "", block.get("input") or {}
                    self.activity.start(block.get("id"), tool, at, args)
                    if tool in ("TaskStop", "KillShell") and isinstance(args, dict):
                        self.activity.stop_task(str(args.get("task_id") or args.get("shell_id") or ""))
        elif kind == "user":
            if isinstance(content, str):
                if "<task-notification>" in content:
                    self.activity.notification(content)
                return
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_result":
                    result = block.get("content")
                    text = result if isinstance(result, str) else " ".join(
                        b.get("text", "") for b in result or [] if isinstance(b, dict))
                    self.activity.finish(block.get("tool_use_id"), text[:300])
                elif block.get("type") == "text" and "<task-notification>" in (block.get("text") or ""):
                    self.activity.notification(block["text"])

    def summary(self) -> dict:
        context = None
        if self.last_request:
            r = self.last_request
            context = {"tokens": r["input"] + r["cache_write"] + r["cache_read"] + r["output"], "window": None,
                       "as_of": self.last_request_at}
        return {
            "kind": "claude",
            "started_at": self.first_at,
            "last_entry_at": self.last_at,
            "model": self.model,
            "effort": self.effort,
            "runtime_at": self.runtime_at,
            "usage": dict(self.usage, requests=self.requests) if self.requests else None,
            "usage_cumulative": False,
            "context": context,
            "compactions": self.compactions,
            "last_compaction_at": self.last_compaction_at,
            "links": list(self.links),
            "activity": self.activity.summary(),
        }


class CodexTrace:
    kind = "codex"

    def __init__(self):
        self.started_at = None
        self.last_at = None
        self.total = None
        self.last = None
        self.window = None
        self.usage_at = None
        self.model = None
        self.effort = None
        self.runtime_at = None
        self.links: list[dict] = []
        self.activity = Activity()

    def feed(self, entry: dict) -> None:
        at = parse_time(entry.get("timestamp"))
        if at is not None:
            self.last_at = at if self.last_at is None else max(self.last_at, at)
        payload = entry.get("payload")
        if not isinstance(payload, dict):
            return
        kind = entry.get("type")
        if kind == "response_item":
            item = payload.get("type")
            if item in ("function_call", "custom_tool_call", "local_shell_call"):
                args = payload.get("arguments") if item == "function_call" else payload.get("input") or payload.get("action")
                self.activity.start(payload.get("call_id"), payload.get("name") or "local_shell", at, args)
            elif item in ("function_call_output", "custom_tool_call_output"):
                self.activity.finish(payload.get("call_id"))
        elif kind == "event_msg" and payload.get("type") in ("task_complete", "turn_aborted"):
            self.activity.turn_ended()
        if kind == "session_meta":
            self.started_at = parse_time(payload.get("timestamp")) or at
            source = payload.get("source")
            spawn = source.get("subagent", {}).get("thread_spawn") if isinstance(source, dict) else None
            parent = payload.get("parent_thread_id") or (spawn or {}).get("parent_thread_id")
            if isinstance(parent, str) and UUID.match(parent):
                self.links.append({"kind": "spawned_by", "target": f"codex:{parent}",
                                   "source": "Codex session_meta (parent_thread_id)"})
            fork = payload.get("forked_from_id")
            if isinstance(fork, str) and UUID.match(fork):
                self.links.append({"kind": "forked_from", "target": f"codex:{fork}",
                                   "source": "Codex session_meta (forked_from_id)"})
        elif kind == "turn_context":
            model, effort = _word(payload.get("model")), _word(payload.get("effort"))
            if model:
                self.model = model
            if effort:
                self.effort = effort
            if model or effort:
                self.runtime_at = at
        elif kind == "event_msg" and payload.get("type") == "token_count":
            info = payload.get("info")
            if not isinstance(info, dict):
                return
            # Cumulative snapshot: the latest one replaces earlier ones.
            if isinstance(info.get("total_token_usage"), dict):
                self.total = info["total_token_usage"]
            if isinstance(info.get("last_token_usage"), dict):
                self.last = info["last_token_usage"]
            if info.get("model_context_window"):
                self.window = int(info["model_context_window"])
            self.usage_at = at

    def summary(self) -> dict:
        usage = None
        if self.total:
            t = self.total
            cached = int(t.get("cached_input_tokens") or 0)
            usage = {
                "input": max(0, int(t.get("input_tokens") or 0) - cached),
                "cache_read": cached,
                "cache_write": int(t.get("cache_write_input_tokens") or 0),
                "output": int(t.get("output_tokens") or 0),
                "reasoning": int(t.get("reasoning_output_tokens") or 0),
            }
        context = None
        if self.last:
            context = {"tokens": int(self.last.get("total_tokens") or 0), "window": self.window, "as_of": self.usage_at}
        return {
            "kind": "codex",
            "started_at": self.started_at,
            "last_entry_at": self.last_at,
            "model": self.model,
            "effort": self.effort,
            "runtime_at": self.runtime_at,
            "usage": usage,
            "usage_cumulative": True,
            "context": context,
            "compactions": None,
            "last_compaction_at": None,
            "links": list(self.links),
            "activity": self.activity.summary(),
        }


class _State:
    def __init__(self, path: Path, kind: str):
        self.path = path
        self.inode = None
        self.offset = 0
        self.size = 0
        self.parser = ClaudeTrace() if kind == "claude" else CodexTrace()
        self.read_at = None
        self.at_eof = False      # only an unfinished last line is left unread


class TraceReader:
    def __init__(self, locator: Locator, max_bytes_per_pass: int = 16 * 1024 * 1024, clock=time.time):
        self.locator = locator
        self.max_bytes = max_bytes_per_pass
        self.clock = clock
        self._states: dict[str, _State] = {}
        self._lock = threading.Lock()

    def forget_except(self, identities: set[str]) -> None:
        with self._lock:
            for identity in list(self._states):
                if identity not in identities:
                    del self._states[identity]

    def update(self, identity: str) -> dict:
        """Read new bytes of this agent's trace. Returns a summary dict."""
        parts = split_identity(identity)
        if not parts:
            return {"available": False, "reason": "no trace support for this agent type"}
        kind, session = parts
        path = self.locator.find(kind, session)
        if path is None:
            return {"available": False, "reason": f"no {kind} trace found for this session"}
        with self._lock:
            state = self._states.get(identity)
            try:
                stat = path.stat()
            except OSError:
                return {"available": False, "reason": "trace not readable"}
            if state is None or state.path != path or state.inode != stat.st_ino or stat.st_size < state.offset:
                state = _State(path, kind)   # new, replaced or truncated file: start over
                state.inode = stat.st_ino
                self._states[identity] = state
            self._read(state, stat.st_size)
            summary = state.parser.summary()
            summary.update({
                "available": True,
                "file": path.name,
                "caught_up": state.offset >= state.size or state.at_eof,
                "read_percent": 100 if not state.size else int(100 * state.offset / state.size),
                "read_at": state.read_at,
            })
            return summary

    def _read(self, state: _State, size: int) -> None:
        state.size = size
        if state.offset >= size:
            state.read_at = self.clock()
            return
        with open(state.path, "rb") as fh:
            fh.seek(state.offset)
            data = fh.read(min(self.max_bytes, size - state.offset))
        reached_eof = state.offset + len(data) >= size
        end = data.rfind(b"\n")
        if end < 0:
            if len(data) >= MAX_LINE:       # one enormous line: skip it
                state.offset += len(data)
            state.at_eof = reached_eof      # an unfinished line: wait for its newline
            state.read_at = self.clock()
            return
        for line in data[: end + 1].splitlines():
            if not line.strip() or len(line) > MAX_LINE:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                state.parser.feed(entry)
        state.offset += end + 1
        state.at_eof = reached_eof
        state.read_at = self.clock()

    # conversation -------------------------------------------------------------

    def conversation(self, identity: str, limit: int, before: int | None, patterns: list[str],
                     max_scan: int = 16 * 1024 * 1024) -> dict:
        """The last ``limit`` user/assistant entries before byte ``before``."""
        parts = split_identity(identity)
        if not parts:
            return {"available": False, "reason": "no trace support for this agent type", "entries": []}
        kind, session = parts
        path = self.locator.find(kind, session)
        if path is None:
            return {"available": False, "reason": f"no {kind} trace found for this session", "entries": []}
        size = path.stat().st_size
        end = size if before is None else max(0, min(int(before), size))
        collected: list[dict] = []
        scanned = 0
        position = end
        tail = b""
        with open(path, "rb") as fh:
            while position > 0 and len(collected) < limit and scanned < max_scan:
                step = min(256 * 1024, position)
                position -= step
                fh.seek(position)
                block = fh.read(step) + tail
                scanned += step
                lines = block.split(b"\n")
                tail = lines.pop(0) if position > 0 else b""
                offset = position + len(tail) + (1 if position > 0 else 0)
                starts = []
                for line in lines:
                    starts.append(offset)
                    offset += len(line) + 1
                for line, start in reversed(list(zip(lines, starts))):
                    if not line.strip() or len(line) > MAX_LINE:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    item = _conversation_item(kind, entry)
                    if item is None:
                        continue
                    item["offset"] = start
                    if collected and item.get("message_id") and collected[-1].get("message_id") == item["message_id"]:
                        collected[-1]["text"] = item["text"] + "\n" + collected[-1]["text"]
                        collected[-1]["offset"] = start
                        continue
                    if len(collected) >= limit:
                        break
                    collected.append(item)
        entries = []
        for item in reversed(collected):
            text, redactions = redact(strip_terminal(item["text"]), patterns)
            truncated = len(text) > ENTRY_CHARS
            entries.append({"role": item["role"], "at": item["at"], "text": text[:ENTRY_CHARS],
                            "truncated": truncated, "redactions": redactions})
        oldest = collected[-1]["offset"] if collected else end
        return {
            "available": True,
            "source": f"{kind} trace ({path.name})",
            "entries": entries,
            "before": oldest if oldest > 0 and collected else None,
            "complete": position == 0 and len(collected) < limit,
            "scan_limited": scanned >= max_scan and position > 0,
        }


def _conversation_item(kind: str, entry: dict) -> dict | None:
    at = parse_time(entry.get("timestamp"))
    if kind == "claude":
        role = entry.get("type")
        if role not in ("user", "assistant") or entry.get("isMeta") or entry.get("isCompactSummary"):
            return None
        message = entry.get("message")
        if not isinstance(message, dict):
            return None
        content = message.get("content")
        if isinstance(content, str):
            texts = [content]
        elif isinstance(content, list):
            texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        else:
            texts = []
        text = _INJECTED.sub("", "\n".join(t for t in texts if t)).strip()
        if not text:
            return None
        return {"role": role, "at": at, "text": text,
                "message_id": message.get("id") if role == "assistant" else None}
    payload = entry.get("payload")
    if entry.get("type") != "response_item" or not isinstance(payload, dict) or payload.get("type") != "message":
        return None
    role = payload.get("role")
    if role not in ("user", "assistant"):
        return None
    texts = [b.get("text", "") for b in payload.get("content") or []
             if isinstance(b, dict) and b.get("type") in ("input_text", "output_text")]
    text = _INJECTED.sub("", "\n".join(t for t in texts if t)).strip()
    if not text:
        return None
    return {"role": role, "at": at, "text": text, "message_id": None}
