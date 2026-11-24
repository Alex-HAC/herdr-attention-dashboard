"""v0.9.7 account icons: a page fixture with every kind of icon, for the views and their tests.

    from tests.icon_fixture import icon_fixture, RECORDED
    data = icon_fixture()

Accounts (view.quotas.accounts[].icon):
  * default marks: claude:main (yellow, M) and claude:personal (magenta, P), codex-personal:personal
    (cyan, P), grokbot, agy (no badge: one account per mark);
  * custom colour and badge: codex (Codex Work) -> red, badge "Wk";
  * uploaded image: cursor -> a small PNG (a data: URL here, so file:// test pages can show it; the
    server sends /api/account-icons/cursor?v=<hash>);
  * no icon at all: copilot -> glyph null and no image, so the text label shows.
Agents: RECORDED maps agent names to the recorded launch account; those agents carry ``icon``.
Every other agent has no recorded account and so no ``icon`` (unknown stays unknown).
"""

from __future__ import annotations

import base64
import struct
import zlib

from herdr_attention import account_icons
from tests.web_fixture import page_fixture

SAVED = {"codex": {"color": "red", "badge": "Wk"}, "cursor": {"image": True}, "copilot": {"glyph": None}}
# Agent name in the page fixture -> launch account as herdr-spawn records it.
RECORDED = {"web-lead": "claude-main", "ui-builder": "codex-work", "etl": "codex-personal"}


def tiny_png(size: int = 32) -> bytes:
    """Our own test image: two diagonal halves (no logo)."""
    rows = b"".join(b"\x00" + b"".join(bytes((122, 162, 247) if x > y else (158, 206, 106)) for x in range(size))
                    for y in range(size))
    chunk = lambda kind, data: struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


class _Images:
    """Stands in for the state folder: cursor has an uploaded image."""
    def version(self, account):
        return "0123456789ab" if account == "cursor" else None


def icon_fixture(**kwargs) -> dict:
    data = page_fixture(**kwargs)
    view = data["view"]
    for agent in view["agents"]:
        if agent.get("name") in RECORDED and not agent.get("in_process"):
            agent["launch"] = {**(agent.get("launch") or {}), "account": RECORDED[agent["name"]]}
    view["settings"]["accounts.icons"] = SAVED
    account_icons.decorate(view, SAVED, _Images())
    data_url = "data:image/png;base64," + base64.b64encode(tiny_png()).decode()
    for holder in [*view["quotas"]["accounts"], *view["agents"]]:
        icon = holder.get("icon")
        if icon and icon.get("image"):
            icon["image"] = data_url   # the server sends /api/account-icons/cursor?v=0123456789ab
    return data
