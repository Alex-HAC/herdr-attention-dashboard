"""Per-agent metrics with provenance: value, unit, exact or estimated, source, freshness.

Every metric is either a value with where it came from, or ``available:
False`` with the reason. Nothing is invented and nothing is priced (tokens
are not money; no price data exists here).
"""

from __future__ import annotations


def _unavailable(reason: str) -> dict:
    return {"available": False, "reason": reason}


def build(agent: dict, now: float) -> dict:
    trace = agent.get("trace") or {}
    has_trace = bool(trace.get("available"))
    reading = has_trace and not trace.get("caught_up", True)
    why = trace.get("reason") or "trace not read yet"
    return {
        "context": _context(trace, has_trace, reading, why),
        "tokens": _tokens(trace, has_trace, reading, why),
        "age": _age(agent, trace, has_trace, now),
        "active": _active(agent, now),
        "model": trace.get("model") if has_trace else None,
        # Reasoning effort of the latest main-thread request, when the trace records it.
        "effort": trace.get("effort") if has_trace else None,
        "runtime_at": trace.get("runtime_at") if has_trace else None,
        "trace": {
            "available": has_trace,
            "reason": None if has_trace else why,
            "file": trace.get("file"),
            "read_at": trace.get("read_at"),
            "last_entry_at": trace.get("last_entry_at"),
            "caught_up": trace.get("caught_up"),
            "read_percent": trace.get("read_percent"),
        },
    }


def _context(trace: dict, has_trace: bool, reading: bool, why: str) -> dict:
    if not has_trace:
        return _unavailable(why)
    context = trace.get("context")
    if not context:
        return _unavailable("still reading the trace" if reading else "no request recorded in the trace yet")
    window = context.get("window")
    if trace.get("kind") == "codex":
        source = "Codex last_token_usage (tokens of the latest request)"
        note = "Codex reports this per request."
    else:
        source = "Claude: input + cache + output tokens of the latest main-thread request"
        note = "Context window size is not recorded in Claude traces."
    out = {
        "available": True, "value": context["tokens"], "unit": "tokens", "window": window,
        "percent": round(100 * context["tokens"] / window, 1) if window else None,
        # The latest request is a measurement, but "current" is only as fresh as it.
        "exact": False, "kind": "estimate (as of the latest request)",
        "source": source, "as_of": context.get("as_of"), "note": note,
    }
    if trace.get("compactions"):
        out["compactions"] = trace["compactions"]
        out["last_compaction_at"] = trace.get("last_compaction_at")
    if reading:
        out["note"] += " Trace still being read; may be older than the latest request."
    return out


def _tokens(trace: dict, has_trace: bool, reading: bool, why: str) -> dict:
    if not has_trace:
        return _unavailable(why)
    usage = trace.get("usage")
    if not usage:
        return _unavailable("still reading the trace" if reading else "no token usage in the trace yet")
    parts = {k: usage[k] for k in ("input", "cache_write", "cache_read", "output", "reasoning") if k in usage}
    # Reasoning is shown separately but already part of output, so it is not added again.
    total = parts["input"] + parts["cache_write"] + parts["cache_read"] + parts["output"]
    if trace.get("kind") == "codex":
        source = "Codex total_token_usage (cumulative snapshot, latest wins)"
    else:
        source = f"Claude per-request usage, each request counted once ({usage.get('requests', 0)} requests)"
    return {
        "available": True, "value": total, "unit": "tokens", "parts": parts,
        "exact": not reading, "kind": "partial (still reading)" if reading else "exact sum from the trace",
        "source": source, "as_of": trace.get("last_entry_at"),
        "note": "Session total since it started; not money.",
    }


def _age(agent: dict, trace: dict, has_trace: bool, now: float) -> dict:
    started = trace.get("started_at") if has_trace else None
    if started:
        return {"available": True, "value": max(0, now - started), "unit": "seconds", "since": started,
                "exact": True, "kind": "exact", "source": "session start in the trace"}
    first = agent.get("first_seen")
    if first is None:
        return _unavailable("never seen")
    return {"available": True, "value": max(0, now - first), "unit": "seconds", "since": first,
            "exact": False, "kind": "at least", "source": "collector first saw this agent (no trace start)"}


def _active(agent: dict, now: float) -> dict:
    total = float(agent.get("active_seconds") or 0)
    started = agent.get("working_started")
    if agent.get("present") and agent.get("status") == "working" and started is not None:
        total += max(0, now - started)
    known = not agent.get("active_start_unknown")
    return {
        "available": True, "value": total, "unit": "seconds", "since": agent.get("first_seen"),
        "exact": known, "kind": "observed" if known else "at least (was already working when first seen)",
        "source": "time Herdr reported this agent as working, while the collector was running",
        "note": "Not process or laptop uptime; gaps while the collector was off are not counted.",
    }
