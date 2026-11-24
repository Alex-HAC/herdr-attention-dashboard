"""Write small but realistic Claude and Codex trace files for tests.

Shapes copied from real traces (field names only, no real content):
Claude writes one line per content block of a reply (same message id and
requestId), retries repeat a requestId, ``compact_boundary`` marks a
compaction. Codex writes ``session_meta`` first and cumulative
``token_count`` snapshots.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

NS = uuid.UUID("2f0a6f0e-4f43-4b7e-9d0a-3e1d1f6f2a10")


def sid(name: str) -> str:
    return str(uuid.uuid5(NS, name))


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


def claude_trace(root: Path, session: str, start: float, turns: list[dict], project: str = "-tmp-project",
                 model: str = "claude-test-1", effort: str | None = None) -> Path:
    """turns: [{"user": text, "reply": [texts], "usage": {...}, "retry": bool, "compact": bool, "sidechain": bool,
    "model": str, "effort": str}]. Claude Code writes ``effort`` on each assistant line (newer versions only)."""
    path = root / "claude" / project / f"{session}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    t = start
    for i, turn in enumerate(turns):
        if turn.get("compact"):
            lines.append({"type": "system", "subtype": "compact_boundary", "timestamp": iso(t), "sessionId": session,
                          "compactMetadata": {"trigger": "auto", "preTokens": 150000}})
            t += 5
        if turn.get("user"):
            lines.append({"type": "user", "timestamp": iso(t), "sessionId": session,
                          "message": {"role": "user", "content": turn["user"]}})
            lines.append({"type": "user", "timestamp": iso(t), "sessionId": session, "isMeta": True,
                          "message": {"role": "user", "content": "<local-command-caveat>meta</local-command-caveat>"}})
            t += 10
        usage = turn.get("usage") or {"input_tokens": 10, "cache_creation_input_tokens": 100,
                                      "cache_read_input_tokens": 1000, "output_tokens": 50}
        message_id = f"msg_{session[:8]}_{i}"
        request_id = f"req_{session[:8]}_{i}"
        blocks = [{"type": "thinking", "thinking": "HIDDEN REASONING"}] + [{"type": "text", "text": x} for x in turn.get("reply", [])]
        blocks.append({"type": "tool_use", "name": "Bash", "input": {"command": "TOOL INPUT"}})
        turn_model, turn_effort = turn.get("model", model), turn.get("effort", effort)
        extra = {"effort": turn_effort} if turn_effort else {}
        for block in blocks:
            lines.append({"type": "assistant", "timestamp": iso(t), "sessionId": session, "requestId": request_id,
                          "isSidechain": bool(turn.get("sidechain")), **extra,
                          "message": {"id": message_id, "role": "assistant", "model": turn_model, "content": [block], "usage": usage}})
        if turn.get("retry"):
            lines.append({"type": "assistant", "timestamp": iso(t), "sessionId": session, "requestId": request_id, **extra,
                          "message": {"id": message_id, "role": "assistant", "model": turn_model,
                                      "content": [{"type": "text", "text": "(retry duplicate)"}], "usage": usage}})
        t += 30
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    return path


def codex_trace(root: Path, session: str, start: float, snapshots: list[dict], parent: str | None = None,
                fork: str | None = None, messages: list[tuple[str, str]] | None = None,
                model: str = "gpt-test", effort: str | None = None) -> Path:
    when = datetime.fromtimestamp(start, timezone.utc).strftime("%Y/%m/%d")
    stamp = datetime.fromtimestamp(start, timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    path = root / "codex" / when / f"rollout-{stamp}-{session}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    source = {"subagent": {"thread_spawn": {"parent_thread_id": parent, "depth": 1, "agent_nickname": "Test"}}} if parent else "cli"
    meta = {"id": session, "session_id": session, "timestamp": iso(start), "source": source,
            "thread_source": "subagent" if parent else "user", "cwd": "/tmp/project", "originator": "codex-tui"}
    if fork:
        meta["forked_from_id"] = fork
    lines = [{"timestamp": iso(start), "type": "session_meta", "payload": meta},
             {"timestamp": iso(start), "type": "turn_context",
              "payload": {"model": model, **({"effort": effort} if effort else {})}}]
    t = start
    for role, text in messages or []:
        t += 20
        lines.append({"timestamp": iso(t), "type": "response_item",
                      "payload": {"type": "message", "role": role,
                                  "content": [{"type": "input_text" if role == "user" else "output_text", "text": text}]}})
        lines.append({"timestamp": iso(t), "type": "response_item",
                      "payload": {"type": "reasoning", "summary": [{"text": "HIDDEN REASONING"}]}})
    for snap in snapshots:
        t += 60
        lines.append({"timestamp": iso(t), "type": "event_msg", "payload": {"type": "token_count", "info": {
            "total_token_usage": snap["total"], "last_token_usage": snap["last"], "model_context_window": snap.get("window", 400000)}}})
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    return path


def codex_usage(input_tokens: int, cached: int, output: int, reasoning: int = 0) -> dict:
    return {"input_tokens": input_tokens, "cached_input_tokens": cached, "cache_write_input_tokens": 0,
            "output_tokens": output, "reasoning_output_tokens": reasoning, "total_tokens": input_tokens + output}
