"""Configuration: defaults, the TOML file, and runtime overrides.

Precedence (last wins): built-in defaults -> config.toml -> overrides saved
from the dashboard. Only keys listed in EDITABLE can be changed at runtime.
"""

from __future__ import annotations

import copy
import os
import tomllib
from pathlib import Path
from typing import Any, Callable

from .writing import DEFAULT_STYLE, STYLE_LABELS
from .visuals import EXTENSIONS as VISUAL_EXTENSIONS, IGNORE as VISUAL_IGNORE
from .ui_contract import CONTRACT
from . import quotas as _quotas

DEFAULTS: dict[str, Any] = {
    "herdr": {"socket": ""},
    "visuals": {"extensions": VISUAL_EXTENSIONS,
                "ignore_globs": VISUAL_IGNORE, "recent_hours": 48,
                "poll_seconds": 30},
    "routing": {"quota_wall_seconds": 3600},
    "sidebar": {"enabled": True},
    "inference": {"enabled": True},
    "watch": {"enabled_workspaces": [], "muted_workspaces": []},
    "cleanup": {"workspaces": []},   # Clean up is off until switched on per workspace
    "questions": {"image_folders": ["~/Projects", "~/Pictures"]},   # image previews in questions come only from here
    "events": {"question": True, "approval": True, "failure": True, "completion": True, "stall": True},
    "stall": {"minutes": 15, "per_workspace": {}, "output_poll_seconds": 60},
    "failure": {"when_gone_while": ["working", "blocked"]},
    "classify": {
        "approval_rules": ["permission", "trust_directory", "startup_update"],
        "question_rules": ["form", "elicitation", "workflow_prompt", "strong_blocker"],
    },
    "attention": {"ai_classify": False, "coordinator_wait_minutes": 0},
    "presence": {
        "phone_delay_minutes": 5,
        "mode": "auto",
        "heartbeat_stale_seconds": 180,
        "unknown_means": "away",
    },
    "notify": {
        "backend": "none",
        "enabled": True,
        "ntfy_url": "",
        "ntfy_topic": "",
        "dashboard_url": "",
        "max_per_10min": 8,
        "summary_max_items": 5,
        # v0.9.8.7: a plain "Finished" waits this long (a turn that resumes by then never pushes) and
        # one agent's plain finishes push at most once per finish_repeat_minutes; the rest are listed.
        "finish_settle_seconds": 90,
        "finish_repeat_minutes": 30,   # 0.9.8.9: was 20
        # v0.9.8.9 phone alert preferences (notify_prefs.py): at most rate_count pushes per
        # rate_minutes (0 = no limit); the rest wait and go out together as one digest. Blocking
        # asks skip the limit, the snooze and quiet hours while blocking_at_once is on.
        "rate_count": 3,
        "rate_minutes": 30,
        "snooze_until": 0,
        "blocking_at_once": True,
        "priority": {
            "approval": "high", "question": "high", "failure": "high", "blocked": "high",
            "completion": "default", "stall": "default",
        },
    },
    "history": {"retention_days": 7},
    "output": {
        "lines": 40,
        "redact": [
            r"(?i)\b(api[_-]?key|token|secret|password|passwd|bearer)\b\s*[:=]\s*\S+",
            r"sk-[A-Za-z0-9_-]{16,}",
            r"gh[pousr]_[A-Za-z0-9]{20,}",
            r"xox[abpr]-[A-Za-z0-9-]{10,}",
            r"AKIA[0-9A-Z]{16}",
        ],
    },
    "api": {"socket": ""},
    "phone": {
        **CONTRACT["listeners"]["phone"], "allowed_origins": [],
        "cookie_secure": True, "session_hours": 72,
    },
    "desktop": dict(CONTRACT["listeners"]["desktop"]),
    "explain": {"backend": "none"},
    "writing": {"style": DEFAULT_STYLE},
    "summaries": {
        "enabled": True,
        "auto": False,
        "account": "codex-personal",
        "model": "gpt-6.2-sol",
        "effort": "low",
        "claude_config_dir": "~/.claude-personal",
        "claude_bin": "",
        "max_per_hour": 20,
        "coalesce_seconds": 20,
        "timeout_seconds": 120,
        "max_input_chars": 14000,
        "entries": 20,
        "longer_entries": 60,
        "check_git": True,
    },
    "traces": {
        "enabled": True,
        "claude_dirs": ["~/.claude-personal/projects", "~/.claude/projects"],
        "codex_dirs": ["~/.codex/sessions"],
        "poll_seconds": 30,
        "max_bytes_per_pass": 16 * 1024 * 1024,
        "conversation_max": 50,
    },
    "keyring": {"service": "herdr-attention-dashboard"},
    "notifications": {"stale_hours": 24},
    "notify_gate": {
        "enabled": True,
        "account": "codex-personal",
        "model": "gpt-6.2-sol",
        "effort": "low",
        "timeout_seconds": 20,
        "max_per_hour": 20,
        "coalesce_seconds": 8,
        "max_input_chars": 4000,
    },
    "notify_tiers": {
        "preset": "normal",
        # 0.9.8.9: quiet hours on by default, 23:00-08:00 (was off, 22:00-08:00).
        "quiet_hours": True,
        "quiet_start": "23:00",
        "quiet_end": "08:00",
        "watch_closely": [],
        "waiting_on_you": True,
        "waiting_on_you_delivery": "ntfy",
        "waiting_on_you_gate": "always",
        "might_look": True,
        "might_look_delivery": "ntfy",
        "might_look_gate": "check",
        "fyi_finished": True,
        "fyi_finished_delivery": "ntfy",
        "fyi_finished_gate": "check",
        "waiting_on_coordinator": False,
        "waiting_on_coordinator_delivery": "ntfy",
        "waiting_on_coordinator_gate": "check",
        "coordinator_milestone": False,
        "coordinator_milestone_delivery": "ntfy",
        "coordinator_milestone_gate": "check",
        "everything": False,
        "everything_delivery": "ntfy",
        "everything_gate": "off",
        # v0.9.9: in-app visuals stay on the dashboard unless this is switched on.
        "visuals": False,
        "visuals_level": "quiet",
        "visuals_away_only": True,
    },
    "ui": {"acknowledge": True, "group_attention_first": True, "parked_after_minutes": 60},
    # Alex's own order for accounts/services and the main information blocks
    # (Arrange). The same lists drive the phone, the desktop window and the bar.
    "layout": {
        "account_order": [], "account_pinned": [], "account_hidden": [],
        "section_order": [s["id"] for s in CONTRACT["arrange"]["sections"]],
        "section_hidden": [],
    },
    # Account icons (v0.9.7): Alex's own mark, colour, badge or image per account; empty = defaults.
    # image_dir "" is the state folder's account-icons/ (uploads only; not editable at run time).
    "accounts": {"icons": {}, "image_dir": ""},
    "quotas": {
        # Empty paths use ~/.local/state/omarchy/agents/usage (the same
        # records the Omarchy Agents panel displays). Read-only.
        "usage_dir": "",
        "order_path": "",
        "timezone": "Europe/Amsterdam",
        # Quota view settings, set on the Quotas page and saved here like Arrange:
        # gauge parts and warning levels (globally, per account overrides), the
        # gauge size and grouping by provider. Defaults keep the 0.9.4 view.
        **{key: copy.deepcopy(value) for key, value in _quotas.DEFAULT_VIEW.items()},
    },
}

EVENT_TYPES = ("question", "approval", "failure", "completion", "stall")
PRESENCE_MODES = ("auto", "always", "never")
SUMMARY_ACCOUNTS = ("codex-personal", "codex-work", "claude-personal", "claude-main")
SUMMARY_EFFORTS = ("low", "medium", "high")


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "false", "1", "0", "on", "off"):
        return value.lower() in ("true", "1", "on")
    raise ValueError("expected true or false")


def _minutes(low: float, high: float) -> Callable[[Any], float]:
    def check(value: Any) -> float:
        number = float(value)
        if not low <= number <= high:
            raise ValueError(f"expected a number between {low} and {high}")
        return int(number) if number == int(number) else number
    return check


def _choice(options: tuple[str, ...]) -> Callable[[Any], str]:
    def check(value: Any) -> str:
        if value not in options:
            raise ValueError(f"expected one of {', '.join(options)}")
        return value
    return check


def _model_id(value: Any) -> str:
    text = str(value).strip()
    if not text or len(text) > 80:
        raise ValueError("expected a model id")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-")
    if any(ch not in allowed for ch in text):
        raise ValueError("expected a model id")
    return text


def _preset(value: Any) -> str:
    from . import notify_tiers as nt
    return nt.validate_preset(value)


def _delivery(value: Any) -> str:
    from . import notify_tiers as nt
    return nt.validate_delivery(value)


def _gate(value: Any) -> str:
    from . import notify_tiers as nt
    return nt.validate_gate(value)


def _np():
    from . import notify_prefs
    return notify_prefs


def _level(value: Any) -> str:
    return _np().validate_level(value)


def _snooze_until(value: Any) -> float:
    return _np().validate_snooze_until(value)


def _rate_count(value: Any) -> int:
    return _np().validate_rate_count(value)


def _rate_minutes(value: Any) -> int:
    return _np().validate_rate_minutes(value)


_NOTIFY_ROWS = ("waiting_on_you", "might_look", "fyi_finished", "coordinator_milestone",
                "waiting_on_coordinator", "quota_stop", "visuals", "everything")


def _locked_tier(value: Any) -> bool:
    from . import notify_tiers as nt
    return nt.validate_locked_tier(value)


def _hhmm(value: Any) -> str:
    from . import notify_tiers as nt
    return nt.validate_hhmm(value)


def _identities(value: Any) -> list[str]:
    from . import notify_tiers as nt
    return nt.validate_identities(value)


def _workspace_list(value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise ValueError("expected a list of workspace ids")
    return sorted(set(value))


LAYOUT_SECTIONS = tuple(s["id"] for s in CONTRACT["arrange"]["sections"])
LAYOUT_LOCKED = {s["id"]: s["label"] for s in CONTRACT["arrange"]["sections"] if not s.get("hideable", True)}
LAYOUT_KEYS = ("account_order", "account_pinned", "account_hidden", "section_order", "section_hidden")


def _id_list(value: Any) -> list[str]:
    """An ordered list of ids; duplicates are dropped, the first place wins.

    Account ids come from the quota records and may be new, so any short
    printable string is valid.
    """
    if not isinstance(value, list) or len(value) > 200:
        raise ValueError("expected a list of ids")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item) > 120 or not item.isprintable():
            raise ValueError("expected a list of ids")
        if item not in out:
            out.append(item)
    return out


def _sections(value: Any) -> list[str]:
    ids = _id_list(value)
    unknown = [v for v in ids if v not in LAYOUT_SECTIONS]
    if unknown:
        raise ValueError(f"unknown section {unknown[0]!r}; expected {', '.join(LAYOUT_SECTIONS)}")
    return ids


def _section_order(value: Any) -> list[str]:
    ids = _sections(value)
    return ids + [s for s in LAYOUT_SECTIONS if s not in ids]


def _section_hidden(value: Any) -> list[str]:
    ids = _sections(value)
    locked = [LAYOUT_LOCKED[v] for v in ids if v in LAYOUT_LOCKED]
    if locked:
        raise ValueError(f"{locked[0]} cannot be hidden")
    return [s for s in LAYOUT_SECTIONS if s in ids]


LAYOUT_CHECKS: dict[str, Callable[[Any], list[str]]] = {
    "account_order": _id_list, "account_pinned": _id_list, "account_hidden": _id_list,
    "section_order": _section_order, "section_hidden": _section_hidden,
}

# The quota view settings (rules in quotas.py).
QUOTA_VIEW_CHECKS: dict[str, Callable[[Any], Any]] = {
    "gauge_show": _quotas.validate_parts, "gauge_accounts": _quotas.validate_account_parts,
    "levels": _quotas.validate_levels, "account_levels": _quotas.validate_account_levels,
    "gauge_size": _quotas.validate_size, "group_by_provider": _bool,
    "header_dials": _bool, "header_dials_count": _quotas.validate_dials_count,
    "centre": _quotas.validate_centre,
}


def _account_icons(value: Any) -> dict:
    from .account_icons import validate_icons
    return validate_icons(value)


def _cleanup_workspaces(value: Any) -> list[str]:
    from .cleanup import NEVER_WORKSPACES
    value = _workspace_list(value)
    if set(value) & NEVER_WORKSPACES:
        raise ValueError("clean up can never be switched on for " + ", ".join(sorted(set(value) & NEVER_WORKSPACES)))
    return value


def _image_folders(value: Any) -> list[str]:
    from .question_images import validate_folders
    return validate_folders(value)


def _per_workspace_minutes(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        raise ValueError("expected {workspace_id: minutes}")
    check = _minutes(1, 24 * 60)
    return {str(k): check(v) for k, v in value.items()}


# Runtime-editable settings and how to validate them. Phone users may only
# change the subset marked True (they cannot see unwatched workspaces).
EDITABLE: dict[str, tuple[Callable[[Any], Any], bool]] = {
    # Switching workspaces on/off from the phone was asked for in the second
    # iteration (the phone sees only workspace labels for this, see docs/requirements.md).
    "sidebar.enabled": (_bool, False),
    "quotas.header_accounts": (_workspace_list, True),
    "watch.enabled_workspaces": (_workspace_list, True),
    "watch.muted_workspaces": (_workspace_list, True),
    "cleanup.workspaces": (_cleanup_workspaces, True),
    # Which folders may be shown on the phone: changed on this computer only.
    "questions.image_folders": (_image_folders, False),
    **{f"events.{t}": (_bool, True) for t in EVENT_TYPES},
    "stall.minutes": (_minutes(1, 24 * 60), True),
    "stall.per_workspace": (_per_workspace_minutes, False),
    "presence.phone_delay_minutes": (_minutes(0, 24 * 60), True),
    "presence.mode": (_choice(PRESENCE_MODES), True),
    "ui.parked_after_minutes": (_minutes(5, 7 * 24 * 60), True),
    **{f"layout.{key}": (check, True) for key, check in LAYOUT_CHECKS.items()},
    **{f"quotas.{key}": (check, True) for key, check in QUOTA_VIEW_CHECKS.items()},
    "accounts.icons": (_account_icons, True),
    "writing.style": (_choice(STYLE_LABELS), True),
    "summaries.enabled": (_bool, True),
    "summaries.auto": (_bool, True),
    "summaries.account": (_choice(SUMMARY_ACCOUNTS), True),
    "summaries.model": (_model_id, True),
    "summaries.effort": (_choice(SUMMARY_EFFORTS), True),
    "notify.enabled": (_bool, True),
    "notifications.stale_hours": (_minutes(1, 8760), True),
    "notify_gate.enabled": (_bool, True),
    "notify_gate.account": (_choice(SUMMARY_ACCOUNTS), True),
    "notify_gate.model": (_model_id, True),
    "notify_gate.effort": (_choice(SUMMARY_EFFORTS), True),
    "attention.ai_classify": (_bool, True),
    "attention.coordinator_wait_minutes": (_minutes(0, 10080), True),
    "notify_tiers.quota_stops": (_bool, True),
    "notify_tiers.visuals": (_bool, True),
    # v0.9.8.9 Phone alerts: loudness and "only when I'm away" per kind, snooze, rate limit.
    **{f"notify_tiers.{row}_level": (_level, True) for row in _NOTIFY_ROWS},
    **{f"notify_tiers.{row}_away_only": (_bool, True) for row in _NOTIFY_ROWS},
    "notify.snooze_until": (_snooze_until, True),
    "notify.rate_count": (_rate_count, True),
    "notify.rate_minutes": (_rate_minutes, True),
    "notify.finish_repeat_minutes": (_minutes(0, 24 * 60), True),
    "notify_tiers.preset": (_preset, True),
    "notify_tiers.quiet_hours": (_bool, True),
    "notify_tiers.quiet_start": (_hhmm, True),
    "notify_tiers.quiet_end": (_hhmm, True),
    "notify_tiers.watch_closely": (_identities, True),
    "notify_tiers.waiting_on_you": (_locked_tier, True),
    "notify_tiers.waiting_on_you_delivery": (_delivery, True),
    "notify_tiers.waiting_on_you_gate": (_gate, True),
    "notify_tiers.might_look": (_bool, True),
    "notify_tiers.might_look_delivery": (_delivery, True),
    "notify_tiers.might_look_gate": (_gate, True),
    "notify_tiers.fyi_finished": (_bool, True),
    "notify_tiers.fyi_finished_delivery": (_delivery, True),
    "notify_tiers.fyi_finished_gate": (_gate, True),
    "notify_tiers.waiting_on_coordinator": (_bool, True),
    "notify_tiers.waiting_on_coordinator_delivery": (_delivery, True),
    "notify_tiers.waiting_on_coordinator_gate": (_gate, True),
    "notify_tiers.coordinator_milestone": (_bool, True),
    "notify_tiers.coordinator_milestone_delivery": (_delivery, True),
    "notify_tiers.coordinator_milestone_gate": (_gate, True),
    "notify_tiers.everything": (_bool, True),
    "notify_tiers.everything_delivery": (_delivery, True),
    "notify_tiers.everything_gate": (_gate, True),
}



def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "herdr-attention"


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(base) / "herdr-attention"


def default_api_socket() -> str:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return str(Path(runtime) / "herdr-attention" / "api.sock")


def deep_merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict) and key not in ("per_workspace",):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def load_file(path: Path | None) -> dict:
    path = path or config_dir() / "config.toml"
    if not path.exists():
        return {}
    with open(path, "rb") as fh:
        return tomllib.load(fh)


class Settings:
    """Merged settings. ``overrides`` is a flat {"a.b": value} dict."""

    def __init__(self, file_values: dict | None = None, overrides: dict | None = None):
        self.file_values = file_values or {}
        self.overrides = dict(overrides or {})
        self._rebuild()

    def _rebuild(self) -> None:
        merged = deep_merge(DEFAULTS, self.file_values)
        for dotted, value in self.overrides.items():
            section, key = dotted.split(".", 1)
            merged.setdefault(section, {})[key] = copy.deepcopy(value)
        self.values = merged

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.values
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def validate(self, dotted: str, value: Any, *, from_phone: bool = False) -> Any:
        if dotted not in EDITABLE:
            raise KeyError(f"{dotted} cannot be changed at runtime")
        check, phone_ok = EDITABLE[dotted]
        if from_phone and not phone_ok:
            raise PermissionError(f"{dotted} can only be changed on the laptop")
        return check(value)

    def set_override(self, dotted: str, value: Any) -> None:
        self.overrides[dotted] = value
        self._rebuild()

    def clear_overrides(self) -> None:
        self.overrides = {}
        self._rebuild()

    # Frequently used views ------------------------------------------------

    def watched(self, workspace_id: str | None) -> bool:
        return bool(workspace_id) and workspace_id in self.get("watch.enabled_workspaces", [])

    def muted(self, workspace_id: str | None) -> bool:
        return bool(workspace_id) and workspace_id in self.get("watch.muted_workspaces", [])

    def stall_minutes(self, workspace_id: str | None) -> float:
        per = self.get("stall.per_workspace", {}) or {}
        return float(per.get(workspace_id, self.get("stall.minutes", 15)))

    def event_enabled(self, event_type: str) -> bool:
        if event_type == "blocked":  # unclassified blocked counts as either kind
            return bool(self.get("events.approval") or self.get("events.question"))
        return bool(self.get(f"events.{event_type}", False))

    def arrangement(self) -> dict:
        """Alex's saved order (Arrange). A bad value from the file falls back to the default."""
        out = {}
        for key, check in LAYOUT_CHECKS.items():
            try:
                out[key] = check(self.get(f"layout.{key}", DEFAULTS["layout"][key]))
            except (TypeError, ValueError):
                out[key] = check(DEFAULTS["layout"][key])
        return out

    def quota_view(self) -> dict:
        """The quota view settings. A bad value from the file falls back to the default."""
        out = {}
        for key, check in QUOTA_VIEW_CHECKS.items():
            try:
                out[key] = check(self.get(f"quotas.{key}", DEFAULTS["quotas"][key]))
            except (TypeError, ValueError):
                out[key] = check(copy.deepcopy(DEFAULTS["quotas"][key]))
        return out

    def account_icons(self) -> dict:
        """accounts.icons; a bad value from the file falls back to no choices (the defaults)."""
        try:
            return _account_icons(self.get("accounts.icons", {}) or {})
        except (TypeError, ValueError):
            return {}

    def account_image_dir(self) -> Path:
        return Path(self.get("accounts.image_dir") or state_dir() / "account-icons")

    def editable_snapshot(self, *, from_phone: bool = False) -> dict:
        return {k: self.get(k) for k, (_, phone_ok) in EDITABLE.items() if phone_ok or not from_phone}

    @property
    def api_socket(self) -> str:
        return self.get("api.socket") or default_api_socket()
