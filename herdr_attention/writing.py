"""One optional writing-style block for AI summaries, notifications and answers."""
from __future__ import annotations

SECTION_NAMES = {"action": "What to do", "why": "Why it matters", "done": "Done"}

DEFAULT_STYLE = "plain"
STYLE_LABELS = {
    "plain": "Plain English",
    "ste100": "Simplified Technical English (approximate)",
}
_RULES = {
    "plain": "Use clear, short sentences in Plain English. Use familiar words and active voice.",
    "ste100": (
        "Approximate Simplified Technical English. Do not claim ASD-STE100 compliance. "
        "Use common words, active voice and short sentences. Aim for at most 20 words per sentence. "
        "Give one instruction per sentence. Use one name for each thing. "
        "Keep necessary technical names, including agent, workspace, coordinator, branch, pull request, test and token."
    ),
}


def current_style(settings) -> str:
    value = settings.get("writing.style", DEFAULT_STYLE)
    return value if value in STYLE_LABELS else DEFAULT_STYLE


def public_config(settings) -> dict:
    return {"style": current_style(settings),
            "options": [{"id": key, "label": label} for key, label in STYLE_LABELS.items()]}


def style_block(settings) -> str:
    # Imported here so config can use the style choices without a config/rules cycle.
    from .notify_tiers import NOTIFY_TIERS, TIER_LABELS
    style = current_style(settings)
    fixed = "; ".join(TIER_LABELS[tier] for tier in NOTIFY_TIERS)
    return (f"Writing style: {STYLE_LABELS[style]}\n{_RULES[style]}\n"
            "Apply this style only to prose you write. Keep evidence quotes, menu options, "
            "commands, identifiers and fixed UI names verbatim. Do not change the requested JSON schema.\n"
            f"Use these fixed dashboard names exactly when relevant: {fixed}.\n"
            f"In prose, lead with {SECTION_NAMES['action']}, explain {SECTION_NAMES['why']}, then {SECTION_NAMES['done']}. "
            "When no action is required, say Nothing to do — for your information. Never present an answered or old decision as pending.")


def apply_style(prompt: str, settings, *, block: str | None = None) -> str:
    return prompt + "\n\n" + (style_block(settings) if block is None else block)
