"""What each agent runs on: service, model and reasoning effort. Recorded values only.

Sources, in this order, per value:

* service: Herdr's agent kind (``agent``: claude, codex, agy, cursor, ...);
* model and effort: the agent's session trace (the latest main-thread request, see traces.py);
* otherwise the herdr-spawn launch record (what the agent was started with, ``launch.model`` and
  ``launch.effort``). The agent can change these later, so the source is always sent along.

Nothing is guessed: a value without a source is None, and the screens show nothing or "unknown".
The labels only reformat a recorded id ("claude-opus-5-5" -> "Opus 5.5"); an id that does not match a
known pattern is shown as it is. Names, the effort scale and the wording live in ui_contract.json
(``runtime``), which the web page reads too.
"""

from __future__ import annotations

import re

from .ui_contract import CONTRACT

RUNTIME = CONTRACT["runtime"]
STEPS = RUNTIME["effort_steps"]

_CLAUDE_NEW = re.compile(r"claude-([a-z]+)-(\d{1,2})(?:-(\d{1,2}))?(?:-\d{8})?")      # claude-opus-4-1-20250805
_CLAUDE_OLD = re.compile(r"claude-(\d{1,2})(?:-(\d{1,2}))?-([a-z]+)(?:-\d{8})?")      # claude-3-5-sonnet-20241022
_ALIAS = re.compile(r"(opus|sonnet|haiku|fable|opusplan)")                                 # claude --model opus
_GPT = re.compile(r"gpt-(\d+(?:\.\d+)?)((?:-[a-z]+)*)")                                 # gpt-5.1-codex-max
_GEMINI = re.compile(r"gemini-(\d+(?:\.\d+)?)((?:-[a-z]+)*)")                            # gemini-2.5-pro
_CONTEXT = re.compile(r"\[(\d+)([km])\]$", re.I)                                         # claude-opus-4-6[1m]


def _text(value) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > 80 or not value.isprintable():
        return None
    return value


def _words(tail: str) -> str:
    return "".join(" " + w.capitalize() for w in tail.split("-") if w)


def model_label(model: str | None) -> str | None:
    """A short name for a recorded model id. Unknown patterns come back unchanged."""
    model = _text(model)
    if not model:
        return None
    context = ""
    found = _CONTEXT.search(model)
    if found:
        context = f" {found.group(1)}{found.group(2).upper()}"
        model = model[:found.start()]
    lower = model.lower()
    if m := _CLAUDE_NEW.fullmatch(lower):
        family, major, minor = m.groups()
        return f"{family.capitalize()} {major}{'.' + minor if minor else ''}{context}"
    if m := _CLAUDE_OLD.fullmatch(lower):
        major, minor, family = m.groups()
        return f"{family.capitalize()} {major}{'.' + minor if minor else ''}{context}"
    if _ALIAS.fullmatch(lower):
        return lower.capitalize() + context
    if m := _GPT.fullmatch(lower):
        return f"GPT-{m.group(1)}{_words(m.group(2))}{context}"
    if m := _GEMINI.fullmatch(lower):
        return f"Gemini {m.group(1)}{_words(m.group(2))}{context}"
    return model + context


# v0.9.7.4: the compact model name that replaces the CC / CX service badge: "Sol 6.2", "Astra 6",
# "Fable 5.2", "Opus 5.5", "Grok 4.7". Effort and speed suffixes (Cursor ids such as
# gpt-5.6-sol-high or grok-4.7-high-fast) are dropped: effort is shown on its own.
_SUFFIXES = {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra", "fast", "thinking", "latest", "preview"}
_NAMED = re.compile(r"([a-z]+)-(\d+(?:\.\d+)?)((?:-[a-z]+)*)")                             # grok-4.7, gemini-2.5-pro


def _strip_suffixes(lower: str) -> str:
    parts = lower.split("-")
    while len(parts) > 1 and parts[-1] in _SUFFIXES and not (parts[-1] == "max" and parts[-2] == "codex"):
        parts.pop()   # gpt-5.1-codex-max keeps "Max": there it is the model, not the effort
    return "-".join(parts)


def compact_label(model: str | None) -> str | None:
    """The shortest clear name for a recorded model id, for badges. Unknown ids come back as recorded."""
    model = _text(model)
    if not model:
        return None
    found = _CONTEXT.search(model)
    if found:
        model = model[:found.start()]
    lower = _strip_suffixes(model.lower())
    if m := _CLAUDE_NEW.fullmatch(lower):
        family, major, minor = m.groups()
        return f"{family.capitalize()} {major}{'.' + minor if minor else ''}"
    if m := _CLAUDE_OLD.fullmatch(lower):
        major, minor, family = m.groups()
        return f"{family.capitalize()} {major}{'.' + minor if minor else ''}"
    if _ALIAS.fullmatch(lower):
        return lower.capitalize()
    if m := _GPT.fullmatch(lower):
        words = _words(m.group(2)).strip()
        return f"{words} {m.group(1)}" if words else f"GPT-{m.group(1)}"
    if m := _NAMED.fullmatch(lower):
        return f"{m.group(1).capitalize()} {m.group(2)}{_words(m.group(3))}"
    return model


def badge(service_info: dict | None, model: str | None, effort_value: str | None = None) -> dict | None:
    """The small chip next to an agent: the same for every service (v0.9.8.6).

    text: the short model name ("Opus 5.5", "Sol 6.2"), else the service by name (never CC / CX);
    effort: the short effort word ("high", "xhigh", "med") when recorded; label: what the chip shows,
    "Opus 5.5 · high"; from: "model", or "service" when the model is not recorded (an honest unknown).
    """
    text = compact_label(model)
    word = None
    if effort_value:
        key = str(effort_value).strip().lower()
        word = RUNTIME["effort_short"].get(key, str(effort_value).strip())
    labels = RUNTIME["labels"]
    if text:
        title = f"{model}, effort {word}" if word else labels["badge_effort_unknown"].replace("{model}", str(model))
        return {"text": text, "effort": word, "label": f"{text} \u00b7 {word}" if word else text, "from": "model", "title": title}
    if service_info:
        return {"text": service_info["name"], "effort": None, "label": service_info["name"], "from": "service",
                "title": labels["badge_unknown"].replace("{service}", service_info["label"])}
    return None


def service(kind) -> dict | None:
    kind = _text(kind)
    if not kind:
        return None
    known = RUNTIME["services"].get(kind.lower())
    if known:
        return {"id": kind.lower(), "label": known["label"], "short": known["short"], "name": known.get("name", known["label"])}
    letters = re.sub(r"[^A-Za-z0-9]", "", kind)
    return {"id": kind, "label": kind, "short": (letters[:2] or "?").upper(), "name": kind}


def effort(value) -> dict | None:
    value = _text(value)
    if not value:
        return None
    key = value.lower()
    word = RUNTIME["effort_words"].get(key, value)
    if key in STEPS:
        level = STEPS.index(key) + 1
    elif key in RUNTIME["effort_below_steps"]:
        level = 0
    else:
        level = None   # recorded, but not on the scale: shown as its word, never as dots
    return {"id": value, "label": word, "level": level, "steps": len(STEPS)}


def describe(card: dict) -> dict:
    """The ``runtime`` field of one agent card (view, detail)."""
    metrics = card.get("metrics") or {}
    launch = card.get("launch") or {}
    trace = metrics.get("trace") or {}
    reading = bool(trace.get("available")) and trace.get("caught_up") is False
    if launch.get("kind") and card.get("agent") and launch["kind"] != card["agent"]:
        launch = {}   # a launch record for another agent kind says nothing about this one

    def pick(name):
        if _text(metrics.get(name)):
            return _text(metrics[name]), "trace"
        if _text(launch.get(name)):
            return _text(launch[name]), "launch"
        return None, None

    model, model_from = pick("model")
    effort_value, effort_from = pick("effort")
    runs = service(card.get("agent"))
    return {
        "service": runs,
        "badge": badge(runs, model, effort_value if model else None),
        "model": {"id": model, "label": model_label(model), "from": model_from} if model else None,
        "effort": dict(effort(effort_value), **{"from": effort_from}) if effort_value else None,
        "trace_at": metrics.get("runtime_at") if "trace" in (model_from, effort_from) else None,
        "reading": reading and "trace" in (model_from, effort_from),
        "in_process": bool(card.get("in_process")),
    }


def decorate(cards: list[dict]) -> None:
    for card in cards:
        card["runtime"] = describe(card)
