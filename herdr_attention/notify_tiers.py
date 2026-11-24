"""Notification tiers: what to send, how, and in which preset.

Aligned with the v0.7.5 attention groups, plus coordinator milestones and
an optional verbose (every status change) tier. Classification is
deterministic and event-driven. No model calls live here.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from . import attention as tiers
from . import classify as rules

# Display order matches the Settings list (1–6).
NOTIFY_TIERS = (
    "waiting_on_you",
    "might_look",
    "fyi_finished",
    "waiting_on_coordinator",
    "coordinator_milestone",
    "everything",
)

# The only copy of tier and preset wording. The web page, the plugin and the CLI
# read it from the view (public_view), in NOTIFY_TIERS order.
ACTION_LABELS = {"open_in_herdr": "Open in Herdr", "read": "Read", "read_all": "Read all"}

TIER_LABELS = {
    "waiting_on_you": "Waiting on you",
    "might_look": "Might want a look",
    "fyi_finished": "Finished",
    "waiting_on_coordinator": "Waiting on a coordinator",
    "coordinator_milestone": "Milestones",
    "everything": "Every status change",
}

ACTIONABLE_TIERS = frozenset(("waiting_on_you", "might_look"))

# An unread row whose episode ended (the agent moved on, or the pane was viewed)
# no longer needs action, so its pill must not say it does.
CLOSED_TIER_LABELS = {
    "waiting_on_you": "Was waiting on you",
    "might_look": "Was worth a look",
}


def tier_label(tier: str, *, is_open: bool = True) -> str:
    if not is_open and tier in CLOSED_TIER_LABELS:
        return CLOSED_TIER_LABELS[tier]
    return TIER_LABELS[tier]


def batch_title(tier_names) -> str:
    """Title of a batched phone notification, in the status-header words:
    "2 waiting on you · 1 to look at"."""
    tier_names = list(tier_names)
    you = sum(t == "waiting_on_you" for t in tier_names)
    rest = len(tier_names) - you
    parts = [f"{you} waiting on you" if you else "", f"{rest} to look at" if rest else ""]
    return "Herdr: " + " · ".join(p for p in parts if p)


def actionable(tier: str) -> bool:
    return tier in ACTIONABLE_TIERS


def gate_tier(rule_tier: str, gate: dict | None, coordinator=None) -> str:
    """Use a successful gate's explicit ask; older decisions use a narrow text check."""
    if rule_tier == 'waiting_on_you' or not gate or gate.get('decision') != 'send':
        return rule_tier
    proposed = gate.get('tier')
    title = gate.get('title') or ''
    from .linking import quota_signal
    if quota_signal(title):
        return 'might_look'
    needs_owner = proposed in ACTIONABLE_TIERS or bool(re.search(
        r"\b(?:needs? your (?:input|help|approval|decision)|waiting (?:for|on) you|"
        r"(?:Alex|owner) (?:must|needs? to)|connect to .+ to unblock)\b", title, re.I))
    if not needs_owner:
        return rule_tier
    # An owner ask outranks a guessed (inferred) reporting line: only a registered
    # coordinator takes it over. One rule for the phone text and the app.
    if coordinator and rule_tier != 'waiting_on_you' and not coordinator.get('inferred'):
        return 'waiting_on_coordinator'
    return 'waiting_on_you' if proposed != 'might_look' else 'might_look'


# Events in these notify states were sent, or listed on the dashboard.
NOTICE_STATES = ("sent", "summarized", "dashboard")


def final_tier(event: dict, gate: dict | None, links: list[dict] | None, *, agent: dict | None = None,
               settings, now: float | None = None) -> str:
    """The one final tier of an event, for the phone, Notifications and the agent tier.

    The value stored when the event was sent (evidence.final_tier) wins, so a later
    change of links cannot make the phone and the app disagree. Events without it
    (older events, or not sent yet) use the rule tier plus gate_tier."""
    stored = (event.get("evidence") or {}).get("final_tier")
    if stored in NOTIFY_TIERS:
        return stored
    rule = classify_event(event, agent, settings=settings, links=links, now=now)
    if rule not in TIER_LABELS:
        rule = "might_look"
    tier = gate_tier(rule, gate, tiers.coordinator_of(event.get("identity") or "", links))
    return asks_cap(tier, agent, event.get("at"))


def asks_cap(tier: str, agent: dict | None, event_at: float | None = None) -> str:
    """v0.9.6: the questions in the message decide whether it waits on Alex.

    A finished message with no question for Alex is "Finished" (Done, not reviewed);
    an unsure one is "Might want a look" (no red label). It only ever lowers
    "Waiting on you", so phone alerts stay the same or fewer. Rules only
    (asks.rule_state), so the phone and the app decide alike; part of final_tier,
    so the tier is stored once with the event (0.9.3)."""
    if tier != "waiting_on_you":
        return tier
    from .asks import rule_state
    return {"none": "fyi_finished", "maybe": "might_look", "later": "fyi_finished"}.get(rule_state(agent, event_at), tier)


def notice_tier(event: dict | None) -> str | None:
    """The stored final tier of an agent's current notice: open, unread and sent or
    listed. None otherwise, so Read (or the end of the episode) clears it."""
    if not event or event.get("acked_at") is not None or event.get("resolved_at") is not None:
        return None
    if event.get("notify_state") not in NOTICE_STATES:
        return None
    stored = (event.get("evidence") or {}).get("final_tier")
    return stored if stored in NOTIFY_TIERS else None


TIER_HINTS = {
    "waiting_on_you": "Approvals and direct questions. Always sent; the AI may only reword it.",
    "might_look": "Failures, errors and possible stalls.",
    "fyi_finished": "A turn ended and nobody else is waiting on it.",
    "waiting_on_coordinator": "A worker reported to its lead, not to you.",
    "coordinator_milestone": "PR opened or merged, plan done.",
    "everything": "Noisy. For debugging.",
}

DELIVERIES = ("ntfy", "desktop", "dashboard")
DELIVERY_LABELS = {
    "ntfy": "Phone (ntfy)",
    "desktop": "Desktop",
    "dashboard": "Dashboard only",
}

GATES = ("check", "always", "off")
GATE_LABELS = {
    "check": "Check and may hold back",
    "always": "Only reword",
    "off": "Send as is",
}

PRESETS = ("quiet", "normal", "close_tabs", "everything", "custom")
PRESET_LABELS = {
    "quiet": "Only me",
    "normal": "Normal",
    "close_tabs": "More",
    "everything": "All",
    "custom": "Custom",
}
PRESET_HINTS = {
    "quiet": "Only things waiting on you.",
    "normal": "Things waiting on you, failures and possible stalls, and finished turns nobody else is waiting on.",
    "close_tabs": "Normal, plus workers reporting to a coordinator and milestones. Not every status change.",
    "everything": "Everything, including every status change. Noisy.",
    "custom": "Your own mix of the switches below.",
}

# Per-tier defaults (Normal).
TIER_DEFAULTS = {
    "waiting_on_you": {"enabled": True, "delivery": "ntfy", "gate": "always"},
    "might_look": {"enabled": True, "delivery": "ntfy", "gate": "check"},
    "fyi_finished": {"enabled": True, "delivery": "ntfy", "gate": "check"},
    "waiting_on_coordinator": {"enabled": False, "delivery": "ntfy", "gate": "check"},
    "coordinator_milestone": {"enabled": False, "delivery": "ntfy", "gate": "check"},
    "everything": {"enabled": False, "delivery": "ntfy", "gate": "off"},
}

PRESET_ENABLED = {
    "quiet": {t: (t == "waiting_on_you") for t in NOTIFY_TIERS},
    "normal": {t: TIER_DEFAULTS[t]["enabled"] for t in NOTIFY_TIERS},
    "close_tabs": {t: (t != "everything") for t in NOTIFY_TIERS},
    "everything": {t: True for t in NOTIFY_TIERS},
}

# A finished turn that looks like a PR or plan milestone.
_MILESTONE = re.compile(
    r"(?is)("
    r"\b(?:opened|opening|created)\b.{0,60}\b(?:pr|pull request)\b|"
    r"\b(?:pr|pull request)\s*#?\d+\b.{0,60}\b(?:opened|merged|ready)\b|"
    r"\b(?:pr|pull request)\s+#?\d+\s+merged\b|"
    r"\bmerged\b.{0,40}\b(?:pr|pull request|#\d+)\b|"
    r"#\d+\s+merged\b|"
    r"\bplan\b.{0,40}\b(?:done|complete|completed|finished)\b"
    r")"
)


def normalize_preset(name: str | None) -> str:
    text = (name or "normal").strip().lower().replace("-", "_").replace(" ", "_")
    if text in ("close", "close_tab", "closer"):
        text = "close_tabs"
    if text not in PRESETS:
        raise ValueError(f"unknown preset {name!r}; use quiet (Only me), normal, close-tabs (More) or everything (All)")
    return text


def _hhmm(value: Any) -> str:
    text = str(value).strip()
    try:
        hour, minute = text.split(":", 1)
        h, m = int(hour), int(minute)
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError
    except ValueError:
        raise ValueError("expected a time as HH:MM") from None
    return f"{h:02d}:{m:02d}"


def _time(text: str) -> time:
    hour, minute = _hhmm(text).split(":")
    return time(int(hour), int(minute))


LOCKED_TIER = "waiting_on_you"
LOCKED_MESSAGE = "Waiting on you is always on; it cannot be switched off."


def tier_config(settings, name: str) -> dict:
    defaults = TIER_DEFAULTS[name]
    enabled = settings.get(f"notify_tiers.{name}", defaults["enabled"])
    delivery = settings.get(f"notify_tiers.{name}_delivery", defaults["delivery"])
    gate = settings.get(f"notify_tiers.{name}_gate", defaults["gate"])
    if delivery not in DELIVERIES:
        delivery = defaults["delivery"]
    if gate not in GATES:
        gate = defaults["gate"]
    if name == LOCKED_TIER:
        # Tier 1 is always on and may only be reworded, never skipped.
        enabled = True
        if gate == "check":
            gate = "always"
    return {"enabled": bool(enabled), "delivery": delivery, "gate": gate}


def current_enabled(settings) -> dict[str, bool]:
    return {name: tier_config(settings, name)["enabled"] for name in NOTIFY_TIERS}


def match_preset(enabled: dict[str, bool]) -> str:
    for name, pattern in PRESET_ENABLED.items():
        if all(bool(enabled.get(t)) is bool(pattern[t]) for t in NOTIFY_TIERS):
            return name
    return "custom"


def current_preset(settings) -> str:
    stored = settings.get("notify_tiers.preset", "normal")
    matched = match_preset(current_enabled(settings))
    if stored == "custom":
        return "custom" if matched == "custom" else matched
    return matched


def apply_preset(name: str) -> dict:
    """Flat settings dict for a named preset (enabled flags + preset key)."""
    preset = normalize_preset(name)
    if preset == "custom":
        return {"notify_tiers.preset": "custom"}
    out = {"notify_tiers.preset": preset}
    for tier, on in PRESET_ENABLED[preset].items():
        out[f"notify_tiers.{tier}"] = on
    return out


def expand_changes(settings, changes: dict) -> dict:
    """Fill in preset/enabled consistency for a settings PATCH."""
    patch = dict(changes)
    if "notify_tiers.preset" in patch:
        preset = normalize_preset(patch["notify_tiers.preset"])
        if preset != "custom":
            patch.update(apply_preset(preset))
            return patch
        patch["notify_tiers.preset"] = "custom"
    enabled = current_enabled(settings)
    touched = False
    for name in NOTIFY_TIERS:
        key = f"notify_tiers.{name}"
        if key in patch:
            enabled[name] = True if name == LOCKED_TIER else bool(patch[key])
            touched = True
    if touched or "notify_tiers.preset" in changes:
        patch["notify_tiers.preset"] = match_preset(enabled)
    return patch


def watch_closely(settings) -> list[str]:
    values = settings.get("notify_tiers.watch_closely", []) or []
    return [str(v) for v in values if str(v).strip()]


def is_watched_closely(settings, identity: str | None) -> bool:
    return bool(identity) and identity in set(watch_closely(settings))


def quiet_hours_enabled(settings) -> bool:
    # 0.9.8.9: on by default, 23:00-08:00 (was off, 22:00-08:00).
    return bool(settings.get("notify_tiers.quiet_hours", True))


def in_quiet_hours(settings, now: float) -> bool:
    if not quiet_hours_enabled(settings):
        return False
    start = _time(settings.get("notify_tiers.quiet_start", "23:00"))
    end = _time(settings.get("notify_tiers.quiet_end", "08:00"))
    zone = settings.get("quotas.timezone") or "Europe/Amsterdam"
    try:
        tz = ZoneInfo(zone)
    except Exception:
        tz = ZoneInfo("Europe/Amsterdam")
    current = datetime.fromtimestamp(now, tz).time()
    if start <= end:
        return start <= current < end
    return current >= start or current < end


def looks_like_milestone(*texts: str | None) -> bool:
    blob = "\n".join(t for t in texts if t)
    return bool(blob and _MILESTONE.search(blob))


def classify_event(
    event: dict | None = None,
    agent: dict | None = None,
    *,
    settings,
    links: list[dict] | None = None,
    now: float | None = None,
) -> str:
    """Return the notify tier for an event (and optional live agent)."""
    event = event or {}
    if (event.get('type') or event.get('kind')) == 'quota_stop':
        return 'might_look'
    if (event.get("type") or event.get("kind")) == "status":
        return "everything"
    stored = (event.get("evidence") or {}).get("notify_tier")
    if stored in NOTIFY_TIERS and stored != "everything":
        # Re-check milestones on stored non-verbose tiers when text is available.
        if stored in ("fyi_finished", "waiting_on_coordinator") and _milestone_from(event, agent):
            return "coordinator_milestone"
        return stored

    if agent:
        found = tiers.classify_tier(agent, settings=settings, links=links, now=now)
        name = found.tier if found else None
        if name in ("fyi_finished", "waiting_on_coordinator") and _milestone_from(event, agent):
            return "coordinator_milestone"
        if name:
            return name
        if (agent.get("kind") or event.get("type")) == "status":
            return "everything"

    kind = event.get("type") or (agent or {}).get("kind") or "unknown"
    if kind == "status":
        return "everything"
    if kind in ("approval", "question"):
        return "waiting_on_you"
    if kind in ("failure", "stall", "quota_stop"):
        return "might_look"
    if kind == "blocked":
        rule = (event.get("evidence") or {}).get("blocked_rule") or (agent or {}).get("blocked_rule")
        mapped = rules.blocked_kind(rule, settings) if rule else None
        if mapped in ("approval", "question"):
            return "waiting_on_you"
        return "might_look"
    if kind == "completion":
        if _milestone_from(event, agent):
            return "coordinator_milestone"
        identity = event.get("identity") or (agent or {}).get("identity") or ""
        if links and tiers.coordinator_of(identity, links):
            return "waiting_on_coordinator"
        return "fyi_finished"
    return "might_look"


def _milestone_from(event: dict, agent: dict | None) -> bool:
    last = ((agent or {}).get("last_assistant") or {})
    last_text = last.get("text") if isinstance(last, dict) else None
    summary = (agent or {}).get("summary") or {}
    headline = summary.get("headline") if isinstance(summary, dict) else None
    return looks_like_milestone(
        event.get("title"),
        last_text,
        headline,
        (event.get("evidence") or {}).get("snippet"),
    )


def effective_gate(settings, tier_name: str) -> str:
    """Gate mode used when sending this tier. Honours the master switch."""
    if not settings.get("notify_gate.enabled", True):
        return "off"
    gate = tier_config(settings, tier_name)["gate"]
    if tier_name == "waiting_on_you" and gate == "check":
        return "always"
    return gate


def allowed_now(settings, tier_name: str, identity: str | None, now: float) -> str | None:
    """None if the event may be notified, else a park state (off / quiet)."""
    pinned = is_watched_closely(settings, identity)
    on = pinned or tier_config(settings, tier_name)["enabled"]
    if not on:
        return "off"
    # 0.9.8.9: watching an agent closely turns all its alerts on but no longer skips quiet
    # hours (97 of 137 pushes in a day came from one watched coordinator).
    if in_quiet_hours(settings, now) and tier_name != "waiting_on_you":
        return "quiet"
    return None


def delivery_for(settings, tier_name: str) -> str:
    return tier_config(settings, tier_name)["delivery"]


def verbose_enabled(settings, identity: str | None = None) -> bool:
    return tier_config(settings, "everything")["enabled"] or is_watched_closely(settings, identity)


def estimate_tokens(events: list[dict], settings, *, now: float, days: int = 7,
                    links: list[dict] | None = None, capped: bool = False) -> dict:
    """How many gate checks each preset would have made on recent events."""
    cutoff = now - days * 86400
    recent = [e for e in events if (e.get("at") or 0) >= cutoff]
    by_tier: Counter[str] = Counter()
    for event in recent:
        by_tier[classify_event(event, settings=settings, links=links)] += 1
    presets = {}
    for name in ("quiet", "normal", "close_tabs", "everything"):
        enabled = PRESET_ENABLED[name]
        gated = 0
        sent = 0
        for tier, count in by_tier.items():
            if not enabled.get(tier):
                continue
            sent += count
            gate = TIER_DEFAULTS[tier]["gate"]
            if name == "everything" and tier == "everything":
                gate = "off"
            if gate in ("check", "always"):
                gated += count
        presets[name] = {
            "label": PRESET_LABELS[name],
            "events": sent,
            "gate_checks": gated,
            "hint": _hint(name, sent, gated, days, len(recent), capped=capped),
        }
    current = current_preset(settings)
    return {
        "days": days,
        "events": len(recent),
        "capped": capped,
        "by_tier": dict(by_tier),
        "presets": presets,
        "current": current,
        "alert_count": sum(count for tier, count in by_tier.items() if tier_config(settings, tier)["enabled"]),
        "hint": (presets.get(current) or {}).get("hint") or _custom_hint(settings, by_tier, days, capped),
    }


def _hint(preset: str, sent: int, gated: int, days: int, total: int, capped: bool = False) -> str:
    about = "at least " if capped else "about "
    if total == 0:
        return (f"{PRESET_LABELS[preset]}: no recent events in the last {days} days to estimate from. "
                "The gate is a small, low-effort call only when an alert would already be sent.")
    if gated == 0:
        return (f"{PRESET_LABELS[preset]}: {about}{sent} alert"
                f"{'s' if sent != 1 else ''} in the last {days} days, none needing an AI check.")
    return (f"{PRESET_LABELS[preset]}: {about}{gated} AI check"
            f"{'s' if gated != 1 else ''} in the last {days} days "
            f"({sent} alert{'s' if sent != 1 else ''}). "
            "Each check is a small, low-effort call; skipped or failed checks fall back to plain text.")


def _custom_hint(settings, by_tier: Counter, days: int, capped: bool = False) -> str:
    gated = sent = 0
    for tier, count in by_tier.items():
        if not tier_config(settings, tier)["enabled"]:
            continue
        sent += count
        if effective_gate(settings, tier) in ("check", "always"):
            gated += count
    return _hint("custom", sent, gated, days, sum(by_tier.values()), capped=capped)


def public_view(settings, events: list[dict] | None = None, *, now: float | None = None,
                links: list[dict] | None = None, capped: bool = False) -> dict:
    """What the Settings page and CLI need, with no secrets."""
    enabled = current_enabled(settings)
    preset = current_preset(settings)
    items = []
    for name in NOTIFY_TIERS:
        cfg = tier_config(settings, name)
        items.append({
            "id": name,
            "actionable": actionable(name),
            "label": TIER_LABELS[name],
            "hint": TIER_HINTS[name],
            "enabled": cfg["enabled"],
            "delivery": cfg["delivery"],
            "delivery_label": DELIVERY_LABELS[cfg["delivery"]],
            "gate": cfg["gate"],
            "gate_label": GATE_LABELS[cfg["gate"]],
            "lock_skip": name == LOCKED_TIER,
            "locked": name == LOCKED_TIER,
        })
    estimate = None
    if events is not None and now is not None:
        estimate = estimate_tokens(events, settings, now=now, links=links, capped=capped)
    return {
        "preset": preset,
        "preset_label": PRESET_LABELS[preset],
        "preset_hint": PRESET_HINTS[preset],
        "presets": [{"id": p, "label": PRESET_LABELS[p], "hint": PRESET_HINTS[p]}
                    for p in ("quiet", "normal", "close_tabs", "everything", "custom")],
        "tiers": items,
        "quiet_hours": quiet_hours_enabled(settings),
        "quiet_start": settings.get("notify_tiers.quiet_start", "23:00"),
        "quiet_end": settings.get("notify_tiers.quiet_end", "08:00"),
        "watch_closely": watch_closely(settings),
        "deliveries": [{"id": d, "label": DELIVERY_LABELS[d]} for d in DELIVERIES],
        "gates": [{"id": g, "label": GATE_LABELS[g]} for g in GATES],
        "estimate": estimate,
    }


# Validators used by config.EDITABLE ------------------------------------------------

def _bool_like(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "false", "1", "0", "on", "off"):
        return value.lower() in ("true", "1", "on")
    raise ValueError("expected true or false")


def validate_locked_tier(value: Any) -> bool:
    """The Waiting-on-you switch only accepts on; off is refused with a plain reason."""
    from .config import _bool
    if not _bool(value):
        raise ValueError(LOCKED_MESSAGE)
    return True


def validate_preset(value: Any) -> str:
    return normalize_preset(str(value))


def validate_delivery(value: Any) -> str:
    text = str(value).strip().lower()
    aliases = {"phone": "ntfy", "ntfy_phone": "ntfy", "phone_ntfy": "ntfy",
               "laptop": "desktop", "none": "dashboard", "dash": "dashboard"}
    text = aliases.get(text, text)
    if text not in DELIVERIES:
        raise ValueError("expected ntfy, desktop or dashboard")
    return text


def validate_gate(value: Any) -> str:
    text = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {"check_first": "check", "ai": "check", "rewrite": "always",
               "always_send": "always", "plain": "off", "plain_text": "off"}
    text = aliases.get(text, text)
    if text not in GATES:
        raise ValueError("expected check, always or off")
    return text


def validate_hhmm(value: Any) -> str:
    return _hhmm(value)


def validate_identities(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [v.strip() for v in value.split(",") if v.strip()]
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise ValueError("expected a list of agent identities")
    # Preserve order, drop duplicates.
    seen, out = set(), []
    for item in value:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
