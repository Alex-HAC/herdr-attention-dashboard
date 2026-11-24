"""Read-only mirror of the Omarchy agent-usage records the shell panel shows.

The Agents bar widget is a display: `omarchy-agent-usage-update` writes
records to ``~/.local/state/omarchy/agents/usage/`` and the panel draws
those files. This module reads the same files and never launches a
collector, refreshes a token, or switches accounts.

Emails, tokens and other credentials are dropped. Local token counts are
omitted; only plan, limit windows, freshness, status and the daily message
counts of the last 7 days (when a record has them) are kept.

Accounts keep one fixed order (the Omarchy panel order, then file order).
Urgency is a flag on the account; it never reorders the list. The card id
(``provider`` or ``provider:account``) is the stable account id that saved
layouts (view.layout.account_order / _pinned / _hidden) refer to.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .ui_contract import CONTRACT

LABELS = CONTRACT["quotas"]["labels"]
# Quota view settings (set on the Quotas page, saved like Arrange). The parts a
# gauge can show, the sizes and the default warning levels live in the contract.
GAUGE_PARTS = tuple(p["id"] for p in CONTRACT["quotas"]["parts"])
GAUGE_SIZES = tuple(s["id"] for s in CONTRACT["quotas"]["sizes"])
DEFAULT_LEVELS = dict(CONTRACT["quotas"]["levels"])
DEFAULT_VIEW = {"gauge_show": list(GAUGE_PARTS), "gauge_accounts": {}, "levels": dict(DEFAULT_LEVELS),
                "account_levels": {}, "gauge_size": GAUGE_SIZES[0], "group_by_provider": False,
                # v0.9.7.4: the mini dials in the header instead of the "Now: ..." quota line.
                "header_dials": True, "header_dials_count": 3,
                # v0.9.8.7: which limit the number in the middle of a ring shows.
                "centre": "higher"}
DIALS_MAX = 6
CENTRES = tuple(c["id"] for c in CONTRACT["quotas"]["centres"])
UNITS = dict(CONTRACT["quotas"]["units"])
BASE_TITLES = tuple(UNITS)

DEFAULT_TIMEZONE = "Europe/Amsterdam"
# Single-account records use the provider name as the label; these two Codex
# homes should read as Work vs Personal on the overview.
PROVIDER_LABELS = {
    "codex": "Work",
    "codex-personal": "Personal",
}
SECRET_KEY_RE = re.compile(
    r"(token|secret|password|passwd|api[_-]?key|authorization|cookie|"
    r"credential|refresh|access[_-]?token|id_token|email)",
    re.I,
)
# Usage counters are not credentials, even when the key says "token".
USAGE_KEY_OK = re.compile(
    r"(todayTotalTokens|todayTokensByModel|inputTokens|outputTokens|"
    r"cache.+Tokens|totalTokens|tokenCount|tokensUsed|used_pct)",
    re.I,
)


def default_state_home() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local/state"))


def default_usage_dir() -> Path:
    return default_state_home() / "omarchy" / "agents" / "usage"


def default_order_path() -> Path:
    return default_usage_dir().parent / "order.json"


def resolve_path(value: str | Path | None, fallback: Path) -> Path:
    if value in (None, ""):
        return fallback
    return Path(value).expanduser()


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name or DEFAULT_TIMEZONE)
    except Exception:
        return ZoneInfo(DEFAULT_TIMEZONE)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _as_datetime(value: Any, tz: ZoneInfo) -> datetime | None:
    if value in (None, "", 0, 0.0):
        return None
    if isinstance(value, datetime):
        return _aware(value).astimezone(tz)
    if isinstance(value, (int, float)):
        # Collector clocks are often Unix seconds; Omarchy uses epoch-ms.
        stamp = float(value)
        if stamp > 1e12:
            stamp /= 1000.0
        return datetime.fromtimestamp(stamp, tz=timezone.utc).astimezone(tz)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return _aware(datetime.fromisoformat(text.replace("Z", "+00:00"))).astimezone(tz)
        except ValueError:
            return None
    return None


def window_title(label: str, title: str = "") -> str:
    if str(title).strip():
        return str(title).strip()
    text = (label or "").lower()
    if "month" in text:
        return "Monthly"
    if "week" in text or "7-day" in text or "7 day" in text:
        return "Weekly"
    if "session" in text or "5-hour" in text or "5 hour" in text or text.startswith("5h"):
        return "Session"
    plain = re.sub(r"\s*\(.*\)\s*", "", label or "").strip()
    return plain or "Limit"


def format_reset_local(reset: datetime, now: datetime) -> str:
    if reset.date() == now.date():
        return reset.strftime("%H:%M")
    delta_days = (reset.date() - now.date()).days
    if 0 < delta_days <= 6:
        return reset.strftime("%a %H:%M")
    return reset.strftime("%d %b %H:%M")


def format_age(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    if seconds < 0:
        seconds = 0
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        return f"{hours}h ago" if minutes == 0 else f"{hours}h {minutes}m ago"
    days = int(seconds // 86400)
    return f"{days}d ago"


def _percent(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    # Records use a 0–1 fraction. Accept an accidental 0–100 value.
    if number > 1.5:
        number = number / 100.0
    return max(0.0, min(number, 1.0))


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def has_data(record: dict[str, Any]) -> bool:
    """Same rule the Agents panel uses before a record can appear at all."""
    if any(_number(record.get(key)) > 0 for key in (
        "totalPrompts", "totalSessions", "activeDays", "todayPrompts", "todaySessions",
    )):
        return True
    if record.get("limits") or record.get("accounts") or record.get("balance"):
        return True
    return bool(record.get("ready") is True and str(record.get("tierLabel") or "").strip())


def has_subscription(record: dict[str, Any]) -> bool:
    """Same rule the Agents panel uses for a quota/plan section."""
    return bool(record.get("limits") or record.get("accounts") or record.get("balance") or str(record.get("tierLabel") or "").strip())


def _drop_secrets(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if SECRET_KEY_RE.search(str(key)) and not USAGE_KEY_OK.search(str(key)):
                continue
            out[key] = _drop_secrets(value)
        return out
    if isinstance(obj, list):
        return [_drop_secrets(item) for item in obj]
    return obj


def _contains_email(obj: Any) -> bool:
    if isinstance(obj, dict):
        return any(_contains_email(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_contains_email(v) for v in obj)
    if isinstance(obj, str) and "@" in obj:
        local, _, domain = obj.partition("@")
        return bool(local) and "." in domain and " " not in obj.strip()
    return False


def _load_json(path: Path) -> Any | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _order_ids(order_path: Path, records: dict[str, dict[str, Any]]) -> list[str]:
    raw = _load_json(order_path)
    named = [str(item) for item in raw] if isinstance(raw, list) else []
    seen: list[str] = []
    for item in named:
        if item in records and item not in seen:
            seen.append(item)
    for item in records:
        if item not in seen:
            seen.append(item)
    return seen


def _normalize_windows(entries: Any, now: datetime, tz: ZoneInfo) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    if not isinstance(entries, list):
        return windows
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        percent = _percent(entry.get("percent"))
        if percent is None:
            continue
        label = str(entry.get("label") or "")
        title = window_title(label, str(entry.get("title") or ""))
        reset = _as_datetime(entry.get("resetsAt") or entry.get("resetAt"), tz)
        expired = bool(reset and reset <= now)
        if expired:
            percent = 0.0
            reset = None
        used_pct = int(round(percent * 100))
        window = {
            "label": label or title,
            "title": title,
            "percent": percent,
            "used_pct": used_pct,
            "resets_at": reset.isoformat() if reset else None,
            "resets_label": format_reset_local(reset, now) if reset else None,
            "resets_in_seconds": int((reset - now).total_seconds()) if reset and reset > now else None,
            "expired": expired,
        }
        windows.append(window)
    return windows


def _fetched(record: dict[str, Any], now: datetime, tz: ZoneInfo) -> tuple[datetime | None, int | None, str | None]:
    fetched = _as_datetime(
        record.get("fetchedAt") or record.get("limitsFetchedAt") or record.get("updatedAt"),
        tz,
    )
    if fetched is None:
        return None, None, None
    age = max(0, int((now - fetched).total_seconds()))
    return fetched, age, format_age(age)


def _status_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _account_card(provider: dict[str, Any], account: dict[str, Any] | None,
                  now: datetime, tz: ZoneInfo) -> dict[str, Any]:
    provider_id = str(provider.get("id") or "")
    provider_name = str(provider.get("name") or provider_id)
    source = account if account is not None else provider
    account_id = str(source.get("id") or provider_id)
    # A leftover record such as claude-personal.json is its own provider id.
    card_id = f"{provider_id}:{account_id}" if account is not None else provider_id
    default_label = PROVIDER_LABELS.get(provider_id) or (provider_name if account is None else account_id)
    label = str(source.get("label") or default_label)
    if account is None and provider_id in PROVIDER_LABELS:
        label = PROVIDER_LABELS[provider_id]
    plan = str(source.get("plan") or (provider.get("tierLabel") if account is None else "") or provider.get("tierLabel") or "")
    windows = _normalize_windows(source.get("limits") if account is not None else (account or provider).get("limits"), now, tz)
    if account is not None:
        windows = _normalize_windows(account.get("limits"), now, tz)
    else:
        windows = _normalize_windows(provider.get("limits"), now, tz)
    fetched, age, age_label = _fetched(source if source.get("fetchedAt") or source.get("limitsFetchedAt") else provider, now, tz)
    if account is not None:
        fetched, age, age_label = _fetched(account, now, tz)
        if fetched is None:
            fetched, age, age_label = _fetched(provider, now, tz)
    stale = bool(source.get("stale") or (provider.get("limitsStale") if account is None else False))
    if account is not None:
        stale = bool(account.get("stale"))
    else:
        stale = bool(provider.get("limitsStale") or provider.get("stale"))
    status = _status_text(
        source.get("usageStatusText"),
        provider.get("usageStatusText") if account is None else "",
    )
    note = _status_text(provider.get("localOverride") if account is None else "", provider.get("authHelpText") if account is None and not windows else "")
    # Keep notes compact and secret-free; drop long auth help unless it is the only status.
    if note and "no quota meters" in note.lower():
        _, _, rest = note.partition("(")
        note = rest.rstrip(")").strip() if rest else "No quota meters"
        if note and note[0].islower():
            note = note[0].upper() + note[1:]
    elif note and "token" in note.lower() and "lapsed" in note.lower():
        note = "Access token lapsed — showing last known limits"
    elif note and len(note) > 160:
        note = note[:157] + "..."
    return {
        "id": card_id,
        "provider": provider_id,
        "provider_name": provider_name,
        "label": label,
        "plan": plan,
        "active": bool(source.get("active")) if account is not None else True,
        "primary": bool(source.get("primary")) if account is not None else True,
        "ready": bool(provider.get("ready")),
        "stale": stale,
        "status": status,
        "note": note,
        "fetched_at": fetched.isoformat() if fetched else None,
        "age_seconds": age,
        "fetched_age_label": age_label,
        "windows": windows,
        "history": _history(provider) if account is None else [],
    }


def _history(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Daily message counts (last 7 days) when the record has them; never token totals."""
    days = record.get("recentDays")
    if not isinstance(days, list):
        return []
    out = []
    for day in days[-7:]:
        if isinstance(day, dict) and isinstance(day.get("date"), str):
            count = day.get("messageCount")
            out.append({"date": day["date"][:10], "messages": int(count) if isinstance(count, (int, float)) else 0})
    return out


def _provider_cards(record: dict[str, Any], now: datetime, tz: ZoneInfo) -> list[dict[str, Any]]:
    record = _drop_secrets(record)
    if not isinstance(record, dict) or not has_data(record) or not has_subscription(record):
        return []
    accounts = record.get("accounts")
    if isinstance(accounts, list) and accounts:
        return [_account_card(record, account, now, tz) for account in accounts if isinstance(account, dict)]
    return [_account_card(record, None, now, tz)]


def read_quotas(
    usage_dir: str | Path | None = None,
    order_path: str | Path | None = None,
    tz_name: str = DEFAULT_TIMEZONE,
    now: datetime | float | None = None,
) -> dict[str, Any]:
    """Return a compact, secret-free snapshot of the panel's quota records."""
    tz = _zone(tz_name)
    if now is None:
        current = datetime.now(tz)
    elif isinstance(now, datetime):
        current = _aware(now).astimezone(tz)
    else:
        stamp = float(now)
        if stamp > 1e12:
            stamp /= 1000.0
        current = datetime.fromtimestamp(stamp, tz=timezone.utc).astimezone(tz)

    directory = resolve_path(usage_dir, default_usage_dir())
    order = resolve_path(order_path, directory.parent / "order.json") if order_path or usage_dir else resolve_path(order_path, default_order_path())
    payload: dict[str, Any] = {
        "source": "omarchy-agent-usage",
        "dir": str(directory),
        "timezone": str(tz),
        "generated_at": current.isoformat(),
        "accounts": [],
        "error": None,
    }
    if not directory.is_dir():
        payload["error"] = "usage records not found"
        return payload

    records: dict[str, dict[str, Any]] = {}
    try:
        paths = sorted(directory.glob("*.json"))
    except OSError as exc:
        payload["error"] = "could not read usage records"
        payload["detail"] = type(exc).__name__
        return payload

    for path in paths:
        if path.name.startswith("."):
            continue
        raw = _load_json(path)
        if not isinstance(raw, dict):
            continue
        provider_id = str(raw.get("id") or path.stem)
        raw.setdefault("id", provider_id)
        records[provider_id] = raw

    accounts: list[dict[str, Any]] = []
    for provider_id in _order_ids(order, records):
        accounts.extend(_provider_cards(records[provider_id], current, tz))

    # Final safety net: never ship an email or leftover credential key.
    cleaned = _drop_secrets(accounts)
    if _contains_email(cleaned):
        # Extremely defensive: drop any string that looks like an email.
        def scrub(obj: Any) -> Any:
            if isinstance(obj, dict):
                return {k: scrub(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [scrub(v) for v in obj]
            if isinstance(obj, str) and "@" in obj and "." in obj.split("@")[-1]:
                return None
            return obj
        cleaned = scrub(cleaned)

    payload["accounts"] = cleaned
    return payload


SUMMARY_EMPTY = "Quotas"


def _account_id(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 120 or not value.isprintable():
        raise ValueError("expected an account id")
    return value


def validate_parts(value: Any) -> list[str]:
    """What a gauge shows, in the fixed order. An empty list is allowed."""
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError("expected a list of gauge parts")
    unknown = [v for v in value if v not in GAUGE_PARTS]
    if unknown:
        raise ValueError(f"unknown gauge part {unknown[0]!r}; expected {', '.join(GAUGE_PARTS)}")
    return [p for p in GAUGE_PARTS if p in value]


def validate_levels(value: Any) -> dict:
    """Warning and nearly-full levels in whole percent; warning below nearly full."""
    if not isinstance(value, dict) or set(value) != {"warning", "critical"}:
        raise ValueError("expected {warning, critical}")
    out = {}
    for key in ("warning", "critical"):
        number = value[key]
        if isinstance(number, bool) or not isinstance(number, (int, float)) or number != int(number) or not 1 <= number <= 100:
            raise ValueError(f"{key} must be a whole number from 1 to 100")
        out[key] = int(number)
    if out["warning"] >= out["critical"]:
        raise ValueError(LABELS["settings_levels_order"])
    return out


def _per_account(check):
    def run(value: Any) -> dict:
        if not isinstance(value, dict) or len(value) > 200:
            raise ValueError("expected {account_id: value}")
        return {_account_id(k): check(v) for k, v in value.items()}
    return run


validate_account_parts = _per_account(validate_parts)
validate_account_levels = _per_account(validate_levels)


def validate_size(value: Any) -> str:
    if value not in GAUGE_SIZES:
        raise ValueError(f"expected one of {', '.join(GAUGE_SIZES)}")
    return value


def validate_centre(value: Any) -> str:
    if value not in CENTRES:
        raise ValueError(f"expected one of {', '.join(CENTRES)}")
    return value


def window_scope(window: dict) -> str | None:
    """The model or pool a limit window is for, else None for an account-wide window.

    "Weekly", "Session" and "Monthly" are the account's own limits; "Fable Weekly" or "Named
    models Monthly" count only one model or pool (v0.9.8.7). A full model window stops only
    the agents on that model, so it never makes the whole account read as full.
    """
    title = (window.get("title") or "").strip()
    for base in BASE_TITLES:
        if title.endswith(" " + base) and title != base:
            return title[: -len(base) - 1].strip() or None
    return None


def ring_unit(title: str) -> str:
    """The small label under a centre number: wk, 5h, mo, else a short form of the title."""
    for base, unit in UNITS.items():
        if title == base or title.endswith(" " + base):
            return unit
    return (title or "").split(" ")[0][:4].lower()


def centre(rings: list[dict], choice: str = "higher") -> dict | None:
    """The number in the middle of an account's rings and which limit it belongs to.

    weekly: the outer ring; session: the 5-hour ring (else the weekly one, labelled so);
    higher: the fuller of the two; both: "62 · 21" with "wk · 5h".
    """
    if not rings:
        return None
    pick = lambda r: {"text": f"{r['pct']}%", "pct": r["pct"], "unit": r["unit"], "title": r["title"], "both": False}
    outer = next((r for r in rings if r["kind"] == "weekly"), rings[0])
    inner = next((r for r in rings if r["kind"] == "session"), None)
    if choice == "weekly":
        return pick(outer)
    if choice == "session":
        return pick(inner or outer)
    if choice == "both" and inner:
        return {"text": f"{outer['pct']} \u00b7 {inner['pct']}", "pct": max(outer["pct"], inner["pct"]),
                "unit": f"{outer['unit']} \u00b7 {inner['unit']}", "title": f"{outer['title']} \u00b7 {inner['title']}", "both": True}
    return pick(max(rings, key=lambda r: r["pct"]))


def validate_dials_count(value: Any) -> int:
    """How many mini dials the header shows: 1 to DIALS_MAX."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f"expected a number from 1 to {DIALS_MAX}")
    try:
        number = int(value)
    except ValueError:
        raise ValueError(f"expected a number from 1 to {DIALS_MAX}") from None
    if not 1 <= number <= DIALS_MAX:
        raise ValueError(f"expected a number from 1 to {DIALS_MAX}")
    return number


def arranged(accounts: list[dict], layout: dict | None = None) -> list[dict]:
    """The Arrange order (the same rule as arrangedAccounts() in app.js): pinned first, then
    Alex's saved order, then the order the records give. Hidden accounts are left out."""
    layout = layout or {}
    order = list(layout.get('account_order') or [])
    pinned = set(layout.get('account_pinned') or [])
    hidden = set(layout.get('account_hidden') or [])
    pos = {account_id: i for i, account_id in enumerate(order)}
    ranked = sorted(enumerate(accounts), key=lambda item: (item[1]['id'] not in pinned,
                                                          pos.get(item[1]['id'], len(order)), item[0]))
    return [a for _, a in ranked if a['id'] not in hidden]


def header_dials(accounts: list[dict], layout: dict | None = None, count: int = 3) -> list[str]:
    """The accounts the header's mini dials show: the first ``count`` in the Arrange order.
    The nearly-full rule of the old header line stays: an active account at or above its
    nearly-full level that is not among them takes the last place (the fullest one)."""
    shown = arranged(accounts, layout)
    pick = shown[:count]
    hot = sorted((a for a in shown if a.get('active') is not False and a not in pick
                  and a.get('worst_pct', 0) >= (a.get('levels') or DEFAULT_LEVELS)['critical']),
                 key=lambda a: -a.get('worst_pct', 0))
    if hot and pick:
        # Only the fullest one, and only in the last place: Alex's first accounts always stay.
        crit = lambda a: a.get('worst_pct', 0) >= (a.get('levels') or DEFAULT_LEVELS)['critical']
        if not crit(pick[-1]):
            pick[-1] = hot[0]
    order = {a['id']: i for i, a in enumerate(shown)}
    return [a['id'] for a in sorted(pick, key=lambda a: order[a['id']])]


def quota_level(pct: float, levels: dict | None = None) -> str:
    """The one quota level rule: wall at 100 %, high from nearly full (default
    90 %), mid from the warning level (default 70 %). Alex may set both per account."""
    levels = levels or DEFAULT_LEVELS
    return "wall" if pct >= 100 else "high" if pct >= levels["critical"] else "mid" if pct >= levels["warning"] else "low"


def checked_label(seconds: float | None) -> str:
    """Plain age of the data: 'checked 40 min ago'."""
    if seconds is None:
        return LABELS["checked_unknown"]
    seconds = max(0, int(seconds))
    if seconds < 60:
        age = LABELS["just_now"]
    elif seconds < 3600:
        age = f"{seconds // 60} min ago"
    elif seconds < 86400:
        hours, minutes = divmod(seconds // 60, 60)
        age = f"{hours} h {minutes} min ago" if minutes else f"{hours} h ago"
    else:
        days = seconds // 86400
        age = f"{days} day ago" if days == 1 else f"{days} days ago"
    return LABELS["checked"].format(age=age)


def _ring(window: dict, kind: str, levels: dict | None) -> dict:
    pct = window.get("used_pct", 0)
    title = window.get("title") or window.get("label") or ""
    return {"kind": kind, "title": title, "pct": pct, "unit": ring_unit(title),
            "level": quota_level(pct, levels), "resets": window.get("resets_label")}


def gauge_rings(windows: list[dict], levels: dict | None = None) -> list[dict]:
    """Outer ring ("weekly"): the fullest weekly window (else the first). Inner
    ring ("session"): the 5-hour session (else the fullest other window).
    Model-only windows ("Fable Weekly") never take a ring when the account has its own (v0.9.8.7)."""
    windows = [w for w in windows if not window_scope(w)] or windows
    if not windows:
        return []
    weekly = [w for w in windows if "Weekly" in (w.get("title") or "")]
    outer = max(weekly, key=lambda w: w.get("used_pct", 0)) if weekly else windows[0]
    rest = [w for w in windows if w is not outer]
    inner = (next((w for w in rest if w.get("title") == "Session"), None)
             or max(rest, key=lambda w: w.get("used_pct", 0), default=None))
    return [_ring(outer, "weekly", levels)] + ([_ring(inner, "session", levels)] if inner else [])


def quota_flag(account: dict) -> dict | None:
    """Urgency flag only; it never changes the order."""
    levels = account.get("levels") or DEFAULT_LEVELS
    stopped = sum(1 for a in account.get("agents", []) if a.get("stopped"))
    if stopped:
        # Say how many: one agent on a full model limit is not the whole account stopping.
        return {"id": "stopped", "label": LABELS["flag_stopped_one"] if stopped == 1 else LABELS["flag_stopped"].format(n=stopped)}
    elif account.get("worst_pct", 0) >= 100:
        flag = "full"
    elif account.get("worst_pct", 0) >= levels["critical"]:
        flag = "nearly_full"
    else:
        full = [w for w in account.get("scoped", []) if w["pct"] >= 100]
        if full:
            return {"id": "scoped_full", "label": LABELS["flag_scoped_full"].format(scope=full[0]["scope"])}
        return None
    return {"id": flag, "label": LABELS["flag_" + flag]}


def agents_label(count: int) -> str:
    if count == 0:
        return LABELS["agents_none"]
    return LABELS["agents_one"] if count == 1 else LABELS["agents"].format(n=count)


def summary(accounts: list[dict], pins: list[str] | None = None, layout: dict | None = None) -> dict:
    """One quota line for the header chip, the List row and the Quotas tab.

    Accounts Alex hid (Arrange) never show here. The fullest active account at
    or above its nearly-full level (default 90 %) wins. Otherwise the fullest pinned account is shown: Arrange
    pins first, else the "Pin in header" accounts, else all shown accounts.
    """
    layout = layout or {}
    hidden = set(layout.get('account_hidden') or [])
    accounts = [a for a in accounts if a['id'] not in hidden]
    pins = list(layout.get('account_pinned') or []) or list(pins or [])
    hot = [a for a in accounts if a.get('active') is not False
           and a.get('worst_pct', 0) >= (a.get('levels') or DEFAULT_LEVELS)['critical']]
    pinned = [a for a in accounts if a['id'] in pins] if pins else accounts
    pinned = pinned or accounts
    pick = max(hot or pinned, key=lambda a: a['worst_pct'], default=None)
    if not pick:
        return {'label': SUMMARY_EMPTY, 'level': 'low', 'account': None, 'hot': 0}
    return {'label': pick['header_label'], 'level': quota_level(pick.get('worst_pct', 0), pick.get('levels')),
            'account': pick['id'], 'hot': len(hot)}


def duration_label(seconds: int) -> str:
    minutes = max(0, seconds) // 60
    days, hours = divmod(minutes // 60, 24)
    if days:
        return f"{days}d {hours}h"
    return f"{hours}h {minutes % 60}m" if hours else f"{minutes}m"


def account_matches(account: dict, agent: dict) -> bool:
    """Use the recorded launch account, else a proven one (account_proof.py: the session's own
    config folder); never guess a multi-account launch."""
    recorded = (agent.get('launch') or {}).get('account') or (agent.get('account_proof') or {}).get('account')
    if not recorded:
        return False
    key = str(recorded).lower().replace('_', '-').replace(':', '-')
    card = account['id'].lower().replace(':', '-')
    provider = account['provider'].replace('-personal', '')
    label = account['label'].lower()
    aliases = {card, f'{provider}-{label}'}
    if account['id'] == 'codex':
        aliases.add('codex-work')
    if account['id'].startswith('codex-personal'):
        aliases.add('codex-personal')
    return key in aliases


def real_agents(agents: list[dict]) -> list[dict]:
    """Each running agent once: a live pane, no in-process subagent copies, no folded or ended cards."""
    seen: dict[str, dict] = {}
    for a in agents:
        if (a.get('present') and a.get('pane_id') and not a.get('in_process') and not a.get('in_process_parent')
                and not a.get('folded')):
            seen.setdefault(a['pane_id'], a)
    return list(seen.values())


def unassigned_agents(accounts: list[dict], agents: list[dict]) -> list[dict]:
    """Running agents of a provider that has quota records but no recorded launch account."""
    families = {a['provider'].split('-')[0] for a in accounts}
    return [{"identity": a['identity'], "name": a.get('name') or a.get('pane_id') or 'Agent'}
            for a in real_agents(agents)
            if a['identity'].split(':', 1)[0] in families and not any(account_matches(q, a) for q in accounts)]


def enrich(snapshot: dict, agents: list[dict], pins: list[str] | None = None, layout: dict | None = None,
           view: dict | None = None) -> dict:
    """One account definition and urgency/reset wording for web, plugin and CLI.

    ``view`` holds the quota view settings (config ``quotas.*``): which parts a
    gauge shows and the warning levels, globally and per account, gauge size and
    grouping. The levels drive the flag, the ring colours and the header summary.
    """
    view = {**DEFAULT_VIEW, **(view or {})}
    # Two accounts with the same label (Codex and Claude "Personal") also show the provider.
    labels = Counter(a['label'] for a in snapshot['accounts'])
    for account in snapshot['accounts']:
        windows = account.get('windows') or []
        # v0.9.8.7: the account's own limits decide how full it reads; model-only limits are listed apart.
        general = [w for w in windows if not window_scope(w)] or windows
        worst = max(general, key=lambda w: w['used_pct'], default={})
        top = worst.get('used_pct', 0)
        own_levels = view['account_levels'].get(account['id'])
        own_show = view['gauge_accounts'].get(account['id'])
        levels = dict(own_levels or view['levels'])
        account['levels'], account['levels_own'] = levels, own_levels is not None
        account['show'] = list(own_show if own_show is not None else view['gauge_show'])
        account['show_own'] = own_show is not None
        for window in windows:
            window['level'] = quota_level(window['used_pct'], levels)
        account['agents'] = [{"identity": a['identity'], "name": a.get('name') or a.get('pane_id') or 'Agent',
                              "stopped": bool(a.get('quota_wall')), "present": bool(a.get('present'))}
                             for a in agents if account_matches(account, a) and not a.get('in_process')
                             and (a.get('present') or a.get('quota_wall'))]
        account['out_of_quota'] = top >= 100 or any(a['stopped'] for a in account['agents'])
        account['worst_pct'] = top
        account['session_pct'] = max((w['used_pct'] for w in general if w['title'] == 'Session'), default=None)
        account['weekly_pct'] = max((w['used_pct'] for w in general if 'Weekly' in w['title']), default=None)
        account['scoped'] = [{"scope": window_scope(w), "title": w['title'], "pct": w['used_pct'], "level": w['level'],
                              "resets": w.get('resets_label'),
                              "label": (LABELS['scoped'] if w.get('resets_label') else LABELS['scoped_no_reset']).format(
                                  scope=window_scope(w), pct=w['used_pct'], time=w.get('resets_label'))}
                             for w in windows if window_scope(w) and w not in general]
        remaining = worst.get('resets_in_seconds')
        stamp = worst.get('resets_at')
        local = datetime.fromisoformat(stamp).astimezone(_zone(snapshot['timezone'])) if stamp else None
        # A weekday name only for resets in the next 6 days; later resets show the date.
        account['reset_text'] = (f"resets {format_reset_local(local, local - timedelta(seconds=remaining))}, in {duration_label(remaining)}"
                                 if local and remaining is not None else 'Reset time unknown')
        # Files without an explicit stale flag can still have old or unknown data.
        account['stale'] = bool(account.get('stale') or account.get('age_seconds') is None or account.get('age_seconds', 0) > 900)
        account['freshness'] = (f"updated {account['fetched_age_label']}" if account.get('fetched_age_label') else 'Update time unknown')
        short = 'wk' if 'Weekly' in worst.get('title', '') else 'session' if worst.get('title') == 'Session' else worst.get('title', '')
        name = (f"{account.get('provider_name') or account['provider']} {account['label']}"
                if labels[account['label']] > 1 else account['label'])
        account['header_label'] = f"{name} {top}% {short}".strip() if windows else name
        # Gauge data for the Quotas overview: rings, centre %, reset, agents, age, flag.
        account['name'] = name
        account['group'] = account.get('provider_name') or account['provider']
        account['rings'] = gauge_rings(windows, levels)
        account['centre'] = centre(account['rings'], view['centre'])
        account['level'] = quota_level(top, levels)
        account['reset_label'] = (LABELS['resets'].format(time=worst['resets_label']) if worst.get('resets_label')
                                  else LABELS['no_reset'] if windows else LABELS['no_limits'])
        account['reset_short'] = worst.get('resets_label') or account['reset_label']   # compact gauges
        account['agent_count'] = sum(1 for a in account['agents'] if a['present'])
        account['agents_label'] = agents_label(account['agent_count'])
        account['checked'] = checked_label(account.get('age_seconds'))
        account['flag'] = quota_flag(account)
        account['healthy'] = account['flag'] is None and top < levels['warning']
    # Fixed order from read_quotas (Omarchy panel order); urgency never reorders it.
    snapshot['account_ids'] = [a['id'] for a in snapshot['accounts']]
    snapshot['summary'] = summary(snapshot['accounts'], pins, layout)
    # Older readers (plugin, CLI) use header; it is the same summary.
    snapshot['header'] = {'label': snapshot['summary']['label'], 'urgent': snapshot['summary']['level'] in ('high', 'wall')}
    snapshot['view'] = {'size': view['gauge_size'], 'group_by_provider': bool(view['group_by_provider']),
                        'show': list(view['gauge_show']), 'levels': dict(view['levels']),
                        'header_dials': bool(view['header_dials']), 'header_dials_count': int(view['header_dials_count']),
                        'centre': view['centre']}
    snapshot['dials'] = header_dials(snapshot['accounts'], layout, int(view['header_dials_count'])) if view['header_dials'] else []
    # Counted once per running pane; subagents, folded and ended cards never count.
    unassigned = unassigned_agents(snapshot['accounts'], agents)
    snapshot['unassigned_agents'] = len(unassigned)
    snapshot['unassigned'] = unassigned[:20]
    return snapshot


def refresh_records(usage_dir: str | Path | None = None) -> dict:
    """Explicit refresh only. Call collectors directly to avoid wrapper autoswitches."""
    import subprocess
    import tempfile
    directory = resolve_path(usage_dir, default_usage_dir())
    directory.mkdir(parents=True, exist_ok=True)
    completed, failed = [], []
    for path in sorted(directory.glob('*.json')):
        provider = path.stem
        if not re.fullmatch(r'[a-z][a-z0-9-]{0,40}', provider):
            continue
        collector = Path('/usr/share/omarchy/bin') / ('omarchy-agent-usage-' + provider)
        if not collector.is_file() or provider == 'update':
            continue
        try:
            result = subprocess.run([str(collector), '--limits-only', '--force'], capture_output=True, timeout=75)
            if result.returncode or len(result.stdout) > 4_000_000:
                raise ValueError('collector failed')
            record = json.loads(result.stdout)
            if not isinstance(record, dict) or record.get('id') != provider:
                raise ValueError('invalid usage record')
            with tempfile.NamedTemporaryFile(dir=directory, prefix='.' + provider, mode='wb', delete=False) as tmp:
                tmp.write(result.stdout)
                temporary = Path(tmp.name)
            temporary.replace(path)
            completed.append(provider)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            failed.append(provider)
    return {'ok': not failed, 'refreshed': completed, 'failed': failed}
