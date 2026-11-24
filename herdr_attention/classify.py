"""Plain rules that decide what an agent's state means for you.

No model calls. Every result carries the evidence it was based on and a
confidence word, because Herdr's status is itself a screen-pattern guess.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import Settings
from .flow import quiet_reason

# Kinds in display order (needs you first). Only the ones in ALERT_KINDS
# create events.
KIND_ORDER = ("approval", "question", "blocked", "failure", "completion", "stall",
              "working", "unknown", "idle", "gone")
ALERT_KINDS = ("approval", "question", "blocked", "failure", "completion", "stall")
NEEDS_YOU = ("approval", "question", "blocked", "failure")

LABELS = {
    "approval": "Approval needed",
    "question": "Question waiting",
    "blocked": "Waiting for input",
    "failure": "Agent stopped unexpectedly",
    "completion": "Finished, not reviewed",
    "stall": "Possibly stalled",
    "working": "Working",
    "unknown": "Status unclear",
    "idle": "Idle",
    "gone": "Gone",
    "status": "Status change",
    "quota_stop": "Out of quota",
}


@dataclass
class Attention:
    kind: str
    evidence: dict = field(default_factory=dict)
    confidence: str = "medium"

    @property
    def label(self) -> str:
        return LABELS[self.kind]

    def as_dict(self) -> dict:
        return {"kind": self.kind, "label": self.label, "evidence": self.evidence, "confidence": self.confidence}


def blocked_kind(rule_id: str | None, settings: Settings) -> str:
    rule = (rule_id or "").lower()
    if rule:
        if any(s in rule for s in settings.get("classify.approval_rules", [])):
            return "approval"
        if any(s in rule for s in settings.get("classify.question_rules", [])):
            return "question"
    return "blocked"


def _minutes(seconds: float) -> int:
    return int(seconds // 60)


def classify(agent: dict, now: float, settings: Settings) -> Attention:
    status = agent.get("status") or "unknown"

    if not agent.get("present", True):
        gone_status = agent.get("gone_status")
        evidence = {"reason": agent.get("gone_reason") or "gone", "last_status": gone_status}
        if gone_status in settings.get("failure.when_gone_while", []):
            evidence["note"] = "The agent disappeared while it was busy. It may also have been closed on purpose."
            return Attention("failure", evidence, "low")
        return Attention("gone", evidence, "high")

    if status == "blocked":
        rule = agent.get("blocked_rule")
        kind = blocked_kind(rule, settings)
        evidence = {"herdr_status": "blocked", "rule": rule or "not checked"}
        if kind == "blocked":
            evidence["note"] = "Herdr sees an approval or question screen but the type is unclear."
        return Attention(kind, evidence, "medium")

    if status == "done":
        return Attention("completion", {"herdr_status": "done",
                                        "note": "Herdr marks a finished turn as done until the pane is viewed."}, "medium")

    if status == "working":
        stall = _stall(agent, now, settings)
        if stall:
            return stall
        evidence = {"herdr_status": "working"}
        quiet = quiet_reason(agent, now, trace_fresh_seconds(settings))
        if quiet:
            evidence["waiting_on"] = quiet
        if agent.get("output_error"):
            evidence["output_check"] = f"unavailable ({agent['output_error']}); no stall is inferred"
        return Attention("working", evidence, "medium")

    if status == "idle":
        # Idle never means blocked or "needs you".
        return Attention("idle", {"herdr_status": "idle"}, "medium")

    return Attention("unknown", {"herdr_status": status,
                                 "note": "Herdr sees an agent but cannot classify its screen."}, "low")


def trace_fresh_seconds(settings: Settings) -> float:
    """How old a trace read may be and still describe what the agent is doing now."""
    return max(180.0, 3 * float(settings.get("traces.poll_seconds", 30) or 30))


def _stall(agent: dict, now: float, settings: Settings) -> Attention | None:
    """A possible stall needs real evidence over time, never status alone.

    Required: output checks enabled, the latest check succeeded and is recent,
    and the same normalised screen content (timers, spinners and counters
    removed) was seen in at least 3 checks spanning the whole threshold.
    """
    threshold = settings.stall_minutes(agent.get("workspace_id")) * 60
    poll = float(settings.get("stall.output_poll_seconds", 0) or 0)
    status_since = agent.get("status_since") or agent.get("first_seen") or now
    working_for = now - status_since
    checked = agent.get("output_checked_at")
    if not poll or agent.get("output_error") or checked is None or working_for < threshold:
        return None
    if quiet_reason(agent, now, trace_fresh_seconds(settings)):
        return None                              # a long command, a sleep or a wait: quiet on purpose
    if now - checked > max(3 * poll, 180):
        return None                              # evidence is stale
    quiet_since = agent.get("output_changed_at") or agent.get("output_first_checked_at")
    if quiet_since is None:
        return None
    observed = checked - quiet_since
    checks = int(agent.get("output_unchanged_checks") or 0)
    if observed < threshold or checks < 3:
        return None
    evidence = {
        "herdr_status": "working",
        "working_minutes": _minutes(working_for),
        "working_since_known": bool(agent.get("since_known")),
        "threshold_minutes": int(threshold // 60),
        "output_unchanged_minutes": _minutes(observed),
        "output_checks": checks,
        "output_checked_every_seconds": int(poll),
        "last_check_at": checked,
        "output_source": "visible screen, timers/spinners/counters ignored",
        "note": "Same screen content in every check over this time. Long builds or tests can look like this too.",
    }
    return Attention("stall", evidence, "medium")


def sort_key(kind: str) -> int:
    return KIND_ORDER.index(kind) if kind in KIND_ORDER else len(KIND_ORDER)


# Workspace view states -------------------------------------------------------
#
# A coarser grouping for the overview, derived from the kind above:
#   needs_you  approval / question / blocked / failure
#   working    Herdr says working (a possible stall is flagged, still working)
#   done       finished a turn, nobody has viewed it yet (Herdr "done")
#   waiting    idle, with activity seen recently (ready for the next prompt)
#   parked     idle, nothing seen for ui.parked_after_minutes
#   unknown    Herdr cannot classify the screen
# Waiting vs parked is a time rule on the collector's own observations, not
# something Herdr reports.

STATES = ("needs_you", "working", "done", "waiting", "parked", "unknown")
STATE_LABELS = {
    "needs_you": "Needs you",
    "working": "Working",
    "done": "Done, not reviewed",
    "waiting": "Waiting",
    "parked": "Parked",
    "unknown": "Unclear",
    "out_of_quota": "Out of quota",
}


def state_of(agent: dict, now: float, settings: Settings) -> tuple[str, dict]:
    """(state, basis). ``basis`` says what the state rests on."""
    kind = agent.get("kind") or "unknown"
    if kind in NEEDS_YOU:
        return "needs_you", {"from": LABELS[kind]}
    if kind in ("working", "stall"):
        return "working", {"from": "Herdr status: working", "possible_stall": kind == "stall"}
    if kind == "completion":
        return "done", {"from": "Herdr status: done (not viewed yet)"}
    if kind == "idle":
        parked_after = float(settings.get("ui.parked_after_minutes", 60)) * 60
        known = agent.get("last_activity_at")
        first_seen = agent.get("first_seen")
        reference = known if known is not None else first_seen if first_seen is not None else now
        quiet = now - reference
        basis = {
            "from": "Herdr status: idle",
            "quiet_minutes": int(quiet // 60),
            "quiet_known": known is not None,
            "parked_after_minutes": int(parked_after // 60),
        }
        return ("parked" if quiet >= parked_after else "waiting"), basis
    return "unknown", {"from": f"Herdr status: {agent.get('status') or 'unknown'}"}
