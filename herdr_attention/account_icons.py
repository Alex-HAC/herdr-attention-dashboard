"""Account icons (v0.9.7): one icon object per account, per agent with a recorded account.

The contract is docs/briefs/V0.9.7-ICONS-CONTRACT.md. Glyphs, colour names and labels live in
ui_contract.json (account_icons), so the page and the bar panel draw the same marks. Colours are
names; the page maps them to the live theme tokens --icon-<name> (herdr_theme.py).

Never guess an account: an agent gets an icon only when its launch recorded an account, or the
collector proved it from the session's own config folder (account_proof.py, v0.9.7.1), and that
account matches a quota account (quotas.account_matches). Unknown stays unknown, with no icon.
An agent's icon also says where the account comes from: icon.source = {id, label}.

Alex's own choices are the setting ``accounts.icons``: {account: {glyph, color, badge, image}}; a
missing key keeps the default, a missing entry is the default. An uploaded image is stored in the
state folder under a name made from a hash of the account id (never a path from the client), only
when its content is PNG, JPEG or WebP, and is served with a fixed type and nosniff.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .quotas import _account_id, account_matches
from .ui_contract import API, CONTRACT

SPEC = CONTRACT["account_icons"]
LABELS = SPEC["labels"]
GLYPHS = tuple(g["id"] for g in SPEC["glyphs"])
COLORS = tuple(c["id"] for c in SPEC["colors"])
BADGE_MAX = SPEC["badge_max"]
IMAGE_MAX_BYTES = SPEC["image_max_bytes"]
ENTRY_KEYS = ("glyph", "color", "badge", "image")


# ----------------------------------------------------------------- settings

def _badge(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > BADGE_MAX or not value.isprintable() or value != value.strip():
        raise ValueError(f"a badge is 1 or {BADGE_MAX} characters")
    return value


def validate_entry(value: Any) -> dict:
    """One account's choices; keys left out keep the default."""
    if not isinstance(value, dict) or not set(value) <= set(ENTRY_KEYS):
        raise ValueError(f"expected an object with {', '.join(ENTRY_KEYS)}")
    out: dict = {}
    if "glyph" in value:
        if value["glyph"] is not None and value["glyph"] not in GLYPHS:
            raise ValueError(f"unknown mark {value['glyph']!r}")
        out["glyph"] = value["glyph"]
    if "color" in value:
        if value["color"] not in COLORS:
            raise ValueError(f"expected a colour from {', '.join(COLORS)}")
        out["color"] = value["color"]
    if "badge" in value:
        out["badge"] = _badge(value["badge"])
    if "image" in value:
        if not isinstance(value["image"], bool):
            raise ValueError("image is true or false")
        out["image"] = value["image"]
    return out


def validate_icons(value: Any) -> dict:
    """The setting accounts.icons. Reset is removing an account's entry."""
    if not isinstance(value, dict) or len(value) > 200:
        raise ValueError("expected {account: {glyph, color, badge, image}}")
    return {_account_id(k): validate_entry(v) for k, v in value.items()}


# ----------------------------------------------------------------- defaults

def _family(provider: str) -> str:
    return (provider or "").split("-")[0].split(":")[0]


def _initials(label: str, taken: set[str]) -> str | None:
    words = [w for w in (label or "").replace("-", " ").split() if w[:1].isalnum()]
    if not words:
        return None
    for badge in (words[0][0].upper(), (words[0][0] + (words[1][0] if len(words) > 1 else words[0][1:2])).upper()):
        if badge and badge not in taken:
            return badge
    return None


def defaults(accounts: list[dict]) -> dict[str, dict]:
    """{account: {glyph, color, badge}}: the provider's mark; accounts that share a mark get
    different colours and a badge from their own label (Personal P, Work W), in the fixed order."""
    out: dict[str, dict] = {}
    groups: dict[str, list[dict]] = {}
    for account in accounts:
        glyph = SPEC["defaults"]["glyph"].get(account.get("provider")) or SPEC["defaults"]["glyph"].get(_family(account.get("provider", "")))
        groups.setdefault(glyph or f"text:{account['id']}", []).append(account)
        out[account["id"]] = {"glyph": glyph, "color": SPEC["defaults"]["color"], "badge": None}
    for glyph, members in groups.items():
        palette = SPEC["defaults"]["colors"].get(glyph) or [SPEC["defaults"]["color"]]
        taken: set[str] = set()
        for i, account in enumerate(members):
            out[account["id"]]["color"] = palette[i % len(palette)]
            if len(members) > 1:
                badge = _initials(account.get("label") or "", taken)
                out[account["id"]]["badge"] = badge
                taken.add(badge or "")
    return out


# ----------------------------------------------------------------- images

def sniff(data: bytes) -> str | None:
    """The image type from the content, never from a name or header: PNG, JPEG or WebP only."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


class ImageStore:
    """Uploaded account images in the state folder; one file per account, named by a hash."""

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self._versions: dict[str, tuple] = {}

    def path(self, account: str) -> Path:
        return self.directory / hashlib.sha256(account.encode()).hexdigest()[:32]

    def save(self, account: str, data: bytes) -> str:
        if not data or len(data) > IMAGE_MAX_BYTES:
            raise ValueError(LABELS["too_big"])
        if sniff(data) is None:
            raise ValueError(LABELS["not_image"])
        self.directory.mkdir(parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        with tempfile.NamedTemporaryFile(dir=self.directory, prefix=".up-", delete=False) as tmp:
            tmp.write(data)
            temporary = Path(tmp.name)
        os.chmod(temporary, 0o600)
        temporary.replace(self.path(account))
        return self.version(account) or ""

    def version(self, account: str) -> str | None:
        """A short hash of the stored image (for ?v= in its URL), or None when there is none."""
        path = self.path(account)
        try:
            stat = path.stat()
        except OSError:
            return None
        stamp = (stat.st_mtime_ns, stat.st_size)
        cached = self._versions.get(account)
        if cached and cached[0] == stamp:
            return cached[1]
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
        except OSError:
            return None
        self._versions[account] = (stamp, digest)
        return digest

    def open(self, account: str):
        """(file, mime, size) of the stored image; the type is sniffed again from the file."""
        path = self.path(account)
        handle = open(path, "rb")
        try:
            head = handle.read(16)
            mime = sniff(head)
            size = os.fstat(handle.fileno()).st_size
            if mime is None or size > IMAGE_MAX_BYTES:
                raise ValueError("not an account image")
            handle.seek(0)
            return handle, mime, size
        except Exception:
            handle.close()
            raise

    def delete(self, account: str) -> bool:
        try:
            self.path(account).unlink()
        except FileNotFoundError:
            return False
        self._versions.pop(account, None)
        return True


# ----------------------------------------------------------------- the icon object

def image_url(account: str, version: str) -> str:
    return API["account_icon"].replace("{id}", quote(account, safe="")) + f"?v={version}"


def icon_for(account: dict, default: dict, entry: dict | None, version: str | None) -> dict:
    entry = entry or {}
    pick = lambda key: entry[key] if key in entry else default.get(key)
    label = (f"{account.get('provider_name')} {account['label']}"
             if account.get("provider_name") and account.get("label") and account["label"] != account["provider_name"]
             else account.get("label") or account.get("provider_name") or account["id"])
    return {"account": account["id"], "label": label, "glyph": pick("glyph"), "color": pick("color") or SPEC["defaults"]["color"],
            "badge": pick("badge"), "image": image_url(account["id"], version) if entry.get("image") and version else None,
            "custom": bool(entry)}


def decorate(view: dict, saved: dict, images: ImageStore | None = None) -> None:
    """Adds ``icon`` to every quota account, every agent whose recorded account matches one, and
    the asked_by / answer_to of each question item from such an agent. Nothing else gets one."""
    accounts = (view.get("quotas") or {}).get("accounts") or []
    base = defaults(accounts)
    icons = {}
    for account in accounts:
        version = images.version(account["id"]) if images and (saved.get(account["id"]) or {}).get("image") else None
        account["icon"] = icons[account["id"]] = icon_for(account, base[account["id"]], saved.get(account["id"]), version)
    by_identity = {}
    for agent in view.get("agents") or []:
        agent.pop("icon", None)
        recorded = (agent.get("launch") or {}).get("account")
        proof = agent.get("account_proof") or {}
        if (agent.get("in_process") and not proof) or not (recorded or proof.get("account")):
            continue
        match = next((a for a in accounts if account_matches(a, agent)), None)
        if match:
            source = "launch" if recorded else proof.get("source")
            agent["icon"] = dict(icons[match["id"]], source={"id": source, "label": LABELS.get(f"source_{source}", source)})
            by_identity[agent["identity"]] = agent["icon"]
    for agent in view.get("agents") or []:
        for item in (agent.get("asks") or {}).get("items") or []:
            for key in ("asked_by", "answer_to"):
                who = item.get(key)
                if isinstance(who, dict):
                    who.pop("icon", None)
                    if who.get("identity") in by_identity:
                        who["icon"] = dict(by_identity[who["identity"]])
