"""Useful sent notifications, grouped by pane/task or shared AI message.

No model calls. ``what`` / ``why`` / ``action`` come from templates keyed on the
event type and tier; an AI wording already stored by the notification gate is
reused for ``what``. Evidence goes through ``sanitize.redact``.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from . import attention as tiers
from . import notify_tiers as nt
from .sanitize import redact
from .content import presentation, agent_name
from .classify import LABELS
from .linking import quota_signal

# notify_state values that count as a notification Alex was (or would have been) told about.
SENT_STATES = nt.NOTICE_STATES
HELD_STATES = ("gated_skip",)
LISTED_STATES = SENT_STATES
# The Notifications screen loads this many rows (app.js loadNotifications); the unread badge counts
# the same rows, so every unread it counts is one you can see there (v0.9.8.7).
LIST_LIMIT = 50

CHANNELS = {"ntfy": ["phone"], "desktop": ["desktop"], "dashboard": []}

_PR = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+")


def _templates(kind: str, tier: str, name: str, coordinator: dict | None) -> tuple[str, str, str | None]:
    if kind == "approval":
        return (f"{name} is asking for approval", "It cannot continue until someone answers.",
                "Answer it in the terminal.")
    if kind == "question":
        return (f"{name} asked a question", "It is waiting for an answer before it goes on.",
                "Answer it in the terminal.")
    if kind == "blocked":
        if tier == "waiting_on_you":
            return (f"{name} is waiting for input", "It is paused on a screen only a person can clear.",
                    "Answer it in the terminal.")
        return (f"{name} looks blocked", "It is paused, but it is unclear on what.",
                "Have a look at its terminal output.")
    if kind == "quota_stop":
        return (f"{name} is out of quota", "Its account limit stopped this agent.", "Wait for the reset, then resume it.")
    if kind == "failure":
        return (f"{name} stopped unexpectedly", "Work may be lost or half done.",
                "Check its last output and restart it if needed.")
    if kind == "stall":
        return (f"{name} is possibly stalled", "It has been working with no new output for a while.",
                "Have a look at its terminal output.")
    if kind == "completion":
        if tier == "coordinator_milestone":
            return (f"{name} reached a milestone", "A PR was opened or merged, or a plan finished.",
                    "Review it when you have a moment.")
        if coordinator:
            lead = coordinator['name']
            who = "Its coordinator" if lead == "its coordinator" else f"Its coordinator {lead}"
            return (f"{name} finished a turn", f"{who} picks this up.", None)
        return (f"{name} finished a turn", "Nobody else is waiting on it.",
                "Review the result when you have a moment.")
    return (f"{name}: status changed", "For your information.", None)


def at_label(at: float, settings) -> str:
    zone = settings.get("quotas.timezone") or "Europe/Amsterdam"
    try:
        tz = ZoneInfo(zone)
    except Exception:
        tz = ZoneInfo("Europe/Amsterdam")
    stamp = datetime.fromtimestamp(at, tz)
    return f"{stamp.day} {stamp.strftime('%b %H:%M')}"


def _gate(decision: dict | None) -> dict | None:
    if not decision:
        return None
    raw = decision.get("decision")
    if raw == "skip":
        state = "held"
    elif raw == "send" and decision.get("title"):
        state = "reworded"
    elif raw == "fallback":
        state = "unavailable"
    else:
        state = "sent"
    reason = decision.get("reason") or ""
    if state == "unavailable":
        from .notify_gate import failure_reason
        reason = failure_reason(decision)
    return {"decision": state, "reason": reason}


def build_item(event: dict, *, agents: dict, links: list[dict], gate: dict | None, settings,
               now: float, text: dict | None = None) -> dict:
    identity = event.get("identity") or ""
    agent = agents.get(identity) or {}
    evidence_meta = event.get("evidence") or {}
    tier = nt.final_tier(event, gate, links, agent=agent or None, settings=settings, now=now)
    name = agent_name(agent, event)
    coordinator = tiers.coordinator_of(identity, links)
    kind = event.get("type") or ""
    if kind == "quota_stop" or (gate and gate.get('decision') == 'send' and quota_signal(gate.get("title") or "")):
        kind = "quota_stop"
    what, why, action = _templates(kind, tier, name, coordinator)
    technical = {"what": what, "why": why, "action": action}
    gate_view = _gate(gate)
    if gate and gate.get("title") and gate_view and gate_view["decision"] == "reworded":
        what = str(gate["title"])[:200]

    canonical = text or ({"title": gate.get("title"), "body": gate.get("body") or "", "source": "ai"} if gate and gate.get("decision") == "send" and gate.get("title") else None)
    if gate and gate.get('decision') == 'fallback':
        canonical = None
    if canonical:
        what = canonical["title"]
    if what.casefold().startswith("out of quota is out of quota"):
        what = f"{name} is out of quota"
    body = canonical["body"] if canonical else why

    # The actual question / last message while the episode is still open; else what we noted.
    raw = None
    if event.get("resolved_at") is None and agent.get("snippet"):
        raw = agent.get("snippet")
    raw = raw or evidence_meta.get("note") or event.get("title")
    evidence = None
    if raw:
        text, _ = redact(tiers.snippet_of(str(raw), 400), settings.get("output.redact", []))
        evidence = text or None

    links_out = []
    for url in dict.fromkeys(_PR.findall(" ".join(str(v) for v in (raw, event.get("title")) if v))):
        links_out.append({"kind": "pr", "label": "PR #" + url.rsplit("/", 1)[1], "href": url})

    state = event.get("notify_state")
    delivery_state = "held" if state in HELD_STATES else ("sent" if state in SENT_STATES else "not_sent")
    channels = CHANNELS.get(nt.tier_config(settings, tier)["delivery"], []) if delivery_state == "sent" else []
    if state == "dashboard":
        channels = []

    # Open: the episode still runs and nobody read it. Only an open row needs action,
    # and only an open row keeps the tier label; the agent tier uses the same test.
    is_open = not event.get('resolved_at') and not event.get('acked_at')
    next_action = presentation(agent, resolved=not is_open)
    if nt.actionable(tier) and next_action['kind'] == 'none' and is_open:
        next_action = {'kind':'open_agent', 'label':'Open agent', 'target':identity,
                       'text':'Open the agent and check this request.', 'out_of_date':False}
    return {
        "id": event["id"],
        "at": event["at"],
        "at_label": at_label(event["at"], settings),
        "tier": tier,
        "tier_label": LABELS["quota_stop"] if kind == "quota_stop" else nt.tier_label(tier, is_open=is_open or bool(event.get("acked_at"))),
        "text_source": 'ai' if gate and gate.get('decision') == 'send' and what == gate.get('title') else (canonical or {}).get("source", "template"),
        "pane_id": event.get("pane_id"),
        "task": evidence_meta.get("task") or event.get("title") or name,
        "what": what,
        "body": body,
        "technical": technical,
        "actionable": nt.actionable(tier) and is_open,
        "why": body if canonical else why,
        "action": action,
        "next_action": next_action,
        "agent": {"identity": identity, "name": name, "present": bool(agent.get("present"))},
        "workspace": {"id": event.get("workspace_id"),
                      "label": agent.get("workspace_label") or event.get("workspace_id") or "?"},
        "coordinator": ({"identity": coordinator.get("identity"), "name": coordinator["name"]}
                        if coordinator else None),
        "evidence": evidence,
        "delivery": {"state": delivery_state, "channels": channels},
        "gate": gate_view,
        "read": event.get("acked_at") is not None,
        "links": links_out,
    }


def build_items(events: list[dict], **kwargs: Any) -> list[dict]:
    gates = kwargs.pop("gates", {}) or {}
    texts = kwargs.pop("texts", {})
    items = []
    for event in events:
        gate = gates.get(event['id'])
        if event.get('notify_state') == 'gated_skip' or (gate or {}).get('decision') == 'skip':
            continue
        item = build_item(event, gate=gate, text=texts.get(event['id']), **kwargs)
        # Routine rule-only turn endings remain in agent history, unless one went to the phone:
        # an alert always has its row here (v0.9.8.7).
        if 'finished a turn' in item['what'].casefold() and event.get('notify_state') != 'sent':
            continue
        age = float(kwargs['settings'].get('notifications.stale_hours', 24)) * 3600
        if not item['agent']['present'] and kwargs['now'] - item['at'] > age and not item['actionable']:
            continue
        items.append(item)
    return group_items(items)


def group_items(items):
    groups, owners = {}, {}
    for item in items:
        # Session ids change after resume. Pane + task survives those changes.
        task = re.sub(r'^(?:finished|out of quota):\s*', '', item.get('task') or '', flags=re.I).strip().casefold()
        read = bool(item.get('read'))
        workspace = item.get('workspace', {}).get('id')
        keys = [('pane', workspace, item.get('pane_id') or item['agent']['identity'] or str(item['id']), task, read)]
        # One gate message can name several agents in a batch.
        if item.get('text_source') == 'ai' and item.get('what'):
            keys.append(('message', workspace, item['what'], item.get('body'), read))
        matches = {owners[key] for key in keys if key in owners}
        owner = min(matches) if matches else len(items) + len(owners)
        group = groups.setdefault(owner, [])
        for other in matches - {owner}:
            group.extend(groups.pop(other))
            owners = {key: owner if value == other else value for key, value in owners.items()}
        group.append(item)
        owners.update({key: owner for key in keys})
    for group in groups.values():
        latest = max(group, key=lambda n: (bool(n.get('actionable')) or nt.actionable(n.get('tier')), n.get('text_source') == 'ai', n["at"], n["id"]))
        group.sort(key=lambda n: (n['at'], n['id']), reverse=True)
        for item in group:
            item["group_count"] = len(group)
            item["group_ids"] = [n["id"] for n in group]
            item["is_group_latest"] = item is latest
            item['group_at'] = max(n['at'] for n in group)
    return items


def row_counts(items):
    rows = [n for n in items if n.get('is_group_latest') is not False]
    return {'read': sum(bool(n['read']) for n in rows), 'unread': sum(not n['read'] for n in rows)}
