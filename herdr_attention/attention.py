"""How strongly an agent actually needs Alex, in plain language.

classify() still names the underlying kind (approval, completion, …). This
module turns that, plus who the agent reports to and the last assistant
text, into a short attention tier for the overview:

* waiting_on_you — blocked at a human prompt, or it asked Alex a question
  and stopped
* waiting_on_coordinator — finished or waiting, but it reports to someone
  else (not Alex)
* fyi_finished — finished a turn, no question, no coordinator
* might_look — a possible stall, an unclear block, or it disappeared while
  busy

No model calls live here. An optional, event-driven AI assist can annotate
ambiguous items elsewhere, and only when that toggle is on.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from . import classify as rules
from .config import Settings

TIERS = ("waiting_on_you", "might_look", "fyi_finished", "waiting_on_coordinator")  # notify_tiers order

# Permission / trust screens only a human at the pane can dismiss.
HUMAN_ONLY_KIND = "approval"

# Phrases that mean the agent is asking the user, not narrating a blocker.
_ASK = re.compile(
    r"(?is)(?:"
    r"\?$|"
    r"\bshould I\b|\bshall I\b|\bmay I\b|\bcan I (?:proceed|continue|go ahead)\b|"
    r"\bdo you (?:want|ok|okay|approve|agree)\b|"
    r"\bwould you like\b|\bplease (?:confirm|approve|advise|choose|decide)\b|"
    r"\bawait(?:ing)? your\b|\byour go-ahead\b|\bneed your (?:ok|okay|approval|decision|go)\b|"
    r"\bwaiting for (?:you|your)\b|\bover to you\b"
    r")"
)
_ADDRESS = re.compile(r"(?i)\b(you|your|yours|should I|shall I|may I)\b")
# Work being blocked (API, quota, another team) is not a question for Alex.
_WORK_BLOCK = re.compile(
    r"(?i)\b(blocked by|remains blocked|waiting on (?:the )?(?:api|ci|registry|remote)|"
    r"quota|rate[- ]limit|until (?:the )?(?:fix|merge))\b"
)


@dataclass
class Tier:
    tier: str
    reason: str
    snippet: str = ""
    waiting_on: dict | None = None
    evidence: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        from .notify_tiers import TIER_LABELS  # the single wording table
        return TIER_LABELS[self.tier]

    def as_dict(self) -> dict:
        waiting = self.waiting_on or {"kind": "you", "name": "you", "identity": None}
        return {
            "tier": self.tier,
            "tier_label": self.label,
            "reason": self.reason,
            "snippet": self.snippet,
            "waiting_on": waiting,
            "evidence": self.evidence,
        }


def snippet_of(text: str | None, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def question_text(text: str | None, *, terminal: bool = False) -> str:
    """Quote a current direct ask, never an AI-inferred ask from an older turn.

    In a message (not a terminal dialog), code, quoted lines and text addressed to
    someone else ("For Grok Bot: …") are not an ask for Alex (asks.not_for_alex)."""
    numbered = [(i, line.strip().strip("│┃ ")) for i, line in enumerate((text or "").splitlines()) if line.strip()]
    if not terminal:
        from .asks import not_for_alex
        skip = not_for_alex(text or "")
        numbered = [(i, line) for i, line in numbered[-6:] if i not in skip]
    candidates = [line for _, line in numbered]
    for line in reversed(candidates):
        line = re.sub(r"^[>*#\s]+", "", line)
        if not line or line.startswith("```"):
            continue
        addressed = bool(_ADDRESS.search(line) or re.match(r"(?i)(which|what (?:should|would)|choose|select|allow|approve|run)\b", line))
        if addressed and (_ASK.search(line) or (terminal and line.endswith("?"))):
            return snippet_of(line, 500)
    return ""


def looks_like_user_question(text: str | None) -> bool:
    return bool(question_text(text))


def summary_asks_user(needs: dict | None) -> str | None:
    """Return the ask text if an existing (already paid-for) summary records a user ask."""
    if not needs or not isinstance(needs, dict):
        return None
    kind = (needs.get("kind") or "none").lower()
    ask = (needs.get("ask") or "").strip()
    if kind not in ("approval", "question", "decision") or not ask:
        return None
    return ask


_RAW_ID = re.compile(r"^[a-z][\w-]*:[0-9a-f-]{8,}", re.I)


def coordinator_name(link: dict) -> str:
    """A readable coordinator name: never a status word ("Idle") or a raw session ID."""
    from .content import short_name
    for value in (link.get("to_name"), link.get("label")):
        if value and not _RAW_ID.match(str(value).strip()):
            name = short_name({"name": value})
            if not name.startswith("Agent "):
                return name
    return "its coordinator"


def coordinator_of(identity: str, links: list[dict] | None) -> dict | None:
    """The agent this one reports to, if any (first live reports_to link)."""
    for link in links or []:
        if link.get("kind") != "reports_to" or link.get("from") != identity:
            continue
        if link.get("cycle"):
            continue
        if not (link.get("to_name") or link.get("label") or link.get("to")):
            continue
        return {
            "identity": None if link.get("hidden") else link.get("to"),
            "name": coordinator_name(link),
            "hidden": bool(link.get("hidden")),
            "inferred": bool(link.get("inferred")),
        }
    return None


def human_only_prompt(kind: str, blocked_rule: str | None, settings: Settings) -> bool:
    if kind == "approval":
        return True
    if kind != "blocked":
        return False
    return rules.blocked_kind(blocked_rule, settings) == "approval"


def _quiet_too_long(agent: dict, settings: Settings, now: float) -> bool:
    parked_after = float(settings.get("ui.parked_after_minutes", 60)) * 60
    reference = agent.get("last_activity_at") or agent.get("status_since") or agent.get("first_seen")
    if reference is None:
        return False
    return (now - reference) >= parked_after


NOTICE_REASON = "Its latest message asks you to decide or act."


def classify_tier(agent: dict, *, notice_tier: str | None = None, **kwargs) -> Tier | None:
    """Return a tier for the overview, or None if the agent should not appear there.

    notice_tier is the stored final tier of the agent's current, unread notice
    (notify_tiers.notice_tier). Only waiting_on_you lifts the rule tier, so the
    app never says "not for you" about a message that asked Alex to act. A lifted
    row is an attention row, never a question: it has no quote to answer."""
    found = _rule_tier(agent, **kwargs)
    if notice_tier != "waiting_on_you" or (found and found.tier == "waiting_on_you"):
        return found
    if agent.get("acked") or not agent.get("present", True):
        return found
    if (agent.get("status") or "") not in ("idle", "done", "blocked"):
        return found
    evidence = dict(found.evidence if found else {"kind": agent.get("kind") or "unknown",
                                                  "herdr_status": agent.get("status")})
    evidence.update(notice=True, question="", rule_tier=found.tier if found else None)
    return Tier("waiting_on_you", NOTICE_REASON, found.snippet if found else "",
                {"kind": "you", "name": "you", "identity": None}, evidence)


def _rule_tier(
    agent: dict,
    *,
    settings: Settings,
    links: list[dict] | None = None,
    last_assistant: str | None = None,
    last_role: str | None = None,
    summary_needs: dict | None = None,
    now: float | None = None,
) -> Tier | None:
    kind = agent.get("kind") or "unknown"
    status = agent.get("status") or "unknown"
    present = agent.get("present", True)
    identity = agent.get("identity") or ""
    acked = bool(agent.get("acked"))
    rule = agent.get("blocked_rule")
    # The full message when the engine kept it (last_message); the view's copy stops at 2000 characters.
    last_assistant = last_assistant if last_assistant is not None else (
        agent.get("last_message") or (agent.get("last_assistant") or {}).get("text"))
    last_role = last_role if last_role is not None else (agent.get("last_assistant") or {}).get("last_role") or "assistant"
    summary_needs = summary_needs if summary_needs is not None else ((agent.get("summary") or {}).get("needs_you"))
    coord = coordinator_of(identity, links)
    now = time.time() if now is None else now
    quoted_question = question_text(last_assistant) if last_role != "user" else ""
    prompt = agent.get("prompt_text") or ""
    if status == "blocked":
        quoted_question = question_text(prompt, terminal=True) or quoted_question
    asked = bool(quoted_question)
    # Summaries may explain a current quote, but cannot create a pending ask.
    ask_text = None
    if ask_text and not asked and status in ("idle", "done") and _quiet_too_long(agent, settings, now):
        ask_text = None
    snippet = snippet_of(ask_text or last_assistant or "")
    option_pattern = r"(?m)^\s*[│┃❯> ]*([0-9]+[.)] [^\n│┃]+)"
    options = re.findall(option_pattern, prompt or last_assistant or "")[:8]
    if not options and identity.startswith('codex:') and re.search(r'(?i)shift\s*\+\s*(?:left|←)', prompt):
        options = re.findall(option_pattern, last_assistant or "")[:8]
    evidence = {
        "kind": kind,
        "herdr_status": status,
        "blocked_rule": rule,
        "asked_user": asked,
        "summary_ask": bool(ask_text),
        "last_role": last_role,
        "question": quoted_question,
        "options": options,
    }
    if coord:
        evidence["coordinator"] = coord["name"]

    def on_you(reason: str, extra_snippet: str = "") -> Tier:
        return Tier("waiting_on_you", reason, quoted_question,
                    {"kind": "you", "name": "you", "identity": None}, evidence)

    def on_coord(reason: str) -> Tier:
        name = coord["name"] if coord else "its coordinator"
        return Tier("waiting_on_coordinator", reason, snippet,
                    {"kind": "coordinator", "name": name, "identity": (coord or {}).get("identity")},
                    evidence)

    # Gone-while-busy is a "might want a look", never a sure prompt for Alex.
    if not present:
        if kind == "failure" and not acked:
            return Tier("might_look",
                        "It disappeared while it was busy. It may also have been closed on purpose.",
                        snippet, None, evidence)
        return None

    human_prompt = human_only_prompt(kind, rule, settings) or (
        status == "blocked" and rules.blocked_kind(rule, settings) == "approval"
    )

    # A permission / trust prompt on the pane can only be answered by a human,
    # even if the agent reports to a coordinator.
    if status == "blocked" and human_prompt and quoted_question and not acked:
        reason = "It is stopped at a permission or approval prompt that only you can answer."
        return on_you(reason, snippet or (rule or "approval prompt"))

    if coord and not human_prompt:
        if status == "blocked" and not acked:
            name = coord["name"]
            return on_coord(f"It is waiting for input, but it reports to {name} — not to you.")
        if kind == "stall" and not acked:
            return Tier("might_look",
                        f"Same screen for a while; it reports to {coord['name']}. Long jobs can look like this too.",
                        snippet, {"kind": "coordinator", "name": coord["name"],
                                  "identity": coord.get("identity")}, evidence)
        if status == "done" and not acked:
            return on_coord(f"It finished a turn and reports to {coord['name']}, so this is for them to pick up.")
        if status in ("idle", "done") and not acked and asked:
            return on_coord(f"It asked something and stopped, but it reports to {coord['name']}.")
        return None

    if status == "blocked" and not acked:
        mapped = rules.blocked_kind(rule, settings)
        if mapped == "question" and quoted_question:
            return on_you("It is stopped at a question or form in the pane.", snippet or (rule or ""))
        return Tier("might_look",
                    "Herdr sees a blocked screen, but the type of prompt is unclear.",
                    snippet or (rule or ""), None, evidence)

    if kind == "stall" and not acked:
        return Tier("might_look",
                    "It is still marked working, but the visible screen has not changed for a while. "
                    "Long builds or tests can look like this too.",
                    snippet, None, evidence)

    if status in ("idle", "done") and not acked and asked:
        reason = "It asked you a question and then stopped."
        if ask_text and not asked:
            reason = "Its latest summary records a direct ask for you, and it has stopped."
        return on_you(reason, ask_text or snippet)

    if kind == "completion" and status == "done" and not acked:
        return Tier("fyi_finished",
                    "It finished a turn. Herdr keeps it marked done until the pane is viewed.",
                    snippet, {"kind": "you", "name": "you", "identity": None}, evidence)
    return None


def awaiting_user(tier: Tier | None) -> bool:
    """True when Alex has a quoted question to answer. A notice-lifted row is not one."""
    return bool(tier and tier.tier == "waiting_on_you" and not tier.evidence.get("notice"))
