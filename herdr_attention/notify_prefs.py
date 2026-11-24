"""v0.9.8.9 phone alert preferences: what pushes, how loudly, when.

Alex 19 Nov 22:55: "make the notification to the phone easier to adapt and tune how i like, now
theyre quite intense haha". Audit of the 24 h before: 137 pushes, 118 of them "Finished" turns;
97 from one coordinator that was watched closely (which skipped the finish guard and quiet hours).

One row per kind of alert (Settings > Phone alerts), each with on/off, a loudness and "only when
I'm away from the laptop"; plus a snooze, quiet hours, a rate limit whose extra alerts go out as
one digest, and per-workspace mute (watch.muted_workspaces, as before).

Blocking asks always push at once (Alex, for the ask protocol): they skip the snooze, quiet hours,
the rate limit, the finish guard and "only when away". Only Off (presence.mode = never), an
unwatched or muted workspace, or notify.blocking_at_once = false hold them. is_blocking() is the
hook: today an event is blocking when its tier is "Waiting on you"; an event whose evidence says
"blocking": true or false (the ask protocol can set it) decides for itself.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from . import notify_tiers as nt

# Rows in Settings order: (id, label, hint). Ids are notify tiers plus quota_stop.
TYPES = (
    ("waiting_on_you", "Questions and approvals", "An agent is waiting for your answer. Always at once: also in quiet hours, a snooze or over the limit."),
    ("might_look", "Problems", "Failures, errors and possible stalls."),
    ("fyi_finished", "Finished turns", "An agent ended its turn and nobody else is waiting on it."),
    ("coordinator_milestone", "Milestones", "A PR opened or merged, a plan done."),
    ("waiting_on_coordinator", "Worker reports", "A worker reported to its coordinator, not to you."),
    ("quota_stop", "Quota stops", "An agent stopped at a usage limit."),
    ("visuals", "Visuals", "A new screenshot, image or video from an agent. Off unless you switch it on."),
    ("everything", "Every status change", "Noisy; for debugging."),
)
TYPE_IDS = tuple(t[0] for t in TYPES)
LEVELS = ("quiet", "normal", "urgent")
LEVEL_LABELS = {"quiet": "Quiet", "normal": "Normal", "urgent": "Urgent"}
# ntfy priorities: low = in the list without sound, default = normal sound, high = pops up.
LEVEL_NTFY = {"quiet": "low", "normal": "default", "urgent": "high"}
LEVEL_HINTS = {"quiet": "No sound; in the ntfy list", "normal": "Normal sound",
               "urgent": "Sound and pop-up (ntfy high)"}

# Calmer defaults (0.9.8.9). Before: questions, approvals and failures high, the rest default;
# everything sent even while at the laptop (presence.mode always).
DEFAULT_LEVEL = {"waiting_on_you": "urgent", "might_look": "normal", "fyi_finished": "quiet",
                 "coordinator_milestone": "quiet", "waiting_on_coordinator": "quiet",
                 "quota_stop": "quiet", "visuals": "quiet", "everything": "quiet"}
DEFAULT_AWAY_ONLY = {"waiting_on_you": False, "might_look": True, "fyi_finished": True,
                     "coordinator_milestone": True, "waiting_on_coordinator": True,
                     "quota_stop": True, "visuals": True, "everything": True}
DEFAULT_QUIET = {"on": True, "start": "23:00", "end": "08:00"}
DEFAULT_RATE = {"count": 3, "minutes": 30}
PUSH_KINDS = ("single", "gated", "summary")


def enabled_key(type_id: str) -> str:
    if type_id == "quota_stop":
        return "notify_tiers.quota_stops"
    return f"notify_tiers.{type_id}"


def row_of(event: dict, tier: str) -> str:
    return "quota_stop" if event.get("type") == "quota_stop" else tier


def enabled(settings, type_id: str) -> bool:
    if type_id == "waiting_on_you":
        return True
    if type_id == "quota_stop":
        # A standing preference: no quota alerts on the phone unless switched on.
        return bool(settings.get("notify_tiers.quota_stops", False))
    if type_id == "visuals":
        # Off by default: visuals stay in the dashboard unless Alex turns phone alerts on.
        return bool(settings.get("notify_tiers.visuals", False))
    return nt.tier_config(settings, type_id)["enabled"]


def level(settings, type_id: str) -> str:
    value = settings.get(f"notify_tiers.{type_id}_level", DEFAULT_LEVEL.get(type_id, "normal"))
    return value if value in LEVELS else DEFAULT_LEVEL.get(type_id, "normal")


def ntfy_priority(settings, type_id: str) -> str:
    return LEVEL_NTFY[level(settings, type_id)]


def away_only(settings, type_id: str) -> bool:
    return bool(settings.get(f"notify_tiers.{type_id}_away_only", DEFAULT_AWAY_ONLY.get(type_id, True)))


def is_blocking(settings, event: dict, tier: str) -> bool:
    """The hook for "blocking asks always push at once" (see the module docstring)."""
    if not settings.get("notify.blocking_at_once", True):
        return False
    flag = (event.get("evidence") or {}).get("blocking")
    if isinstance(flag, bool):
        return flag
    return tier == "waiting_on_you"


def _zone(settings) -> ZoneInfo:
    try:
        return ZoneInfo(settings.get("quotas.timezone") or "Europe/Amsterdam")
    except Exception:
        return ZoneInfo("Europe/Amsterdam")


def quiet(settings) -> dict:
    return {"on": bool(settings.get("notify_tiers.quiet_hours", DEFAULT_QUIET["on"])),
            "start": settings.get("notify_tiers.quiet_start", DEFAULT_QUIET["start"]) or DEFAULT_QUIET["start"],
            "end": settings.get("notify_tiers.quiet_end", DEFAULT_QUIET["end"]) or DEFAULT_QUIET["end"]}


def in_quiet_hours(settings, now: float) -> bool:
    q = quiet(settings)
    if not q["on"]:
        return False
    start, end = nt._time(q["start"]), nt._time(q["end"])
    current = datetime.fromtimestamp(now, _zone(settings)).time()
    return start <= current < end if start <= end else (current >= start or current < end)


def snooze_until(settings) -> float:
    try:
        return float(settings.get("notify.snooze_until", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def snoozed(settings, now: float) -> bool:
    return snooze_until(settings) > now


def morning(settings, now: float) -> float:
    """The next end of quiet hours (08:00 by default), in Alex's time zone."""
    tz = _zone(settings)
    end = nt._time(quiet(settings)["end"])
    local = datetime.fromtimestamp(now, tz)
    target = local.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
    if target <= local:
        target += timedelta(days=1)
    return target.timestamp()


def snooze_value(choice: Any, settings, now: float | None = None) -> float:
    """notify.snooze = 1h | morning | off -> the notify.snooze_until timestamp to save."""
    now = time.time() if now is None else now
    text = str(choice).strip().lower()
    if text in ("off", "0", "none", ""):
        return 0.0
    if text in ("1h", "hour", "60"):
        return now + 3600
    if text == "morning":
        return morning(settings, now)
    raise ValueError("expected 1h, morning or off")


def rate(settings) -> dict:
    try:
        count = int(settings.get("notify.rate_count", DEFAULT_RATE["count"]))
        minutes = int(settings.get("notify.rate_minutes", DEFAULT_RATE["minutes"]))
    except (TypeError, ValueError):
        count, minutes = DEFAULT_RATE["count"], DEFAULT_RATE["minutes"]
    return {"count": max(0, count), "minutes": max(1, minutes)}


def validate_level(value: Any) -> str:
    text = str(value).strip().lower()
    if text not in LEVELS:
        raise ValueError("expected quiet, normal or urgent")
    return text


def validate_snooze_until(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("expected a time stamp") from None
    if number < 0 or number > time.time() + 8 * 86400:
        raise ValueError("a snooze lasts at most a week")
    return number


def validate_rate_count(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError("expected a whole number") from None
    if not 0 <= number <= 60:
        raise ValueError("expected 0 (no limit) to 60")
    return number


def validate_rate_minutes(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError("expected minutes") from None
    if not 5 <= number <= 240:
        raise ValueError("expected 5 to 240 minutes")
    return number


def _clock(ts: float, settings) -> str:
    local = datetime.fromtimestamp(ts, _zone(settings))
    return local.strftime("%H:%M")


def public_view(settings, now: float, *, sent_24h: int | None = None) -> dict:
    """Settings > Phone alerts: rows, choices and the current state. No secrets."""
    until = snooze_until(settings)
    q, r = quiet(settings), rate(settings)
    return {
        "types": [{"id": tid, "label": label, "hint": hint, "enabled": enabled(settings, tid),
                   "enabled_key": enabled_key(tid), "locked": tid == "waiting_on_you",
                   "level": level(settings, tid), "away_only": away_only(settings, tid),
                   "advanced": tid == "everything"}
                  for tid, label, hint in TYPES],
        "levels": [{"id": lv, "label": LEVEL_LABELS[lv], "hint": LEVEL_HINTS[lv], "ntfy": LEVEL_NTFY[lv]} for lv in LEVELS],
        "mode": settings.get("presence.mode", "auto"),
        "snooze_until": until if until > now else 0,
        "snooze_label": f"Snoozed until {_clock(until, settings)}" if until > now else "",
        "morning": q["end"],
        "quiet": q,
        "in_quiet_hours": in_quiet_hours(settings, now),
        "rate": r,
        "blocking_at_once": bool(settings.get("notify.blocking_at_once", True)),
        "finish_repeat_minutes": settings.get("notify.finish_repeat_minutes", 30),
        "sent_24h": sent_24h,
    }
