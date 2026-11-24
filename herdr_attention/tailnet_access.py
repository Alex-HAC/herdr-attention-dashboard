"""Tailnet allow list (v0.9.7.4): which tailnet devices may reach the phone dashboard.

The phone listener is bound to 127.0.0.1 with Tailscale Serve in front (tailnet only, never
Funnel). Serve adds the caller's tailnet address as ``X-Forwarded-For``; a request without it
came from a process on this laptop. Because the listener is loopback-only, nothing outside the
laptop can set that header itself.

* Off (the default), or an empty list: every tailnet device may open the login page, as before.
  Turning it off keeps the saved list, so turning it on again restores it.
* On with a non-empty list: a request whose tailnet address is not on the list gets a bare 403, before the
  login page, static files or any agent data. The laptop's own tailnet addresses always pass.
  The login still applies on top.

Entries are tailnet IPs (100.x, fd7a:115c:a1e0::/48) or device names (MagicDNS name such as
``phone`` or ``phone.tail1234.ts.net``, or the host name). Names are resolved from
``tailscale status --json``, refreshed every minute; a name that does not resolve allows nothing.

The list and the peer list are only shown and changed on the laptop itself (the Unix socket and
the 127.0.0.1 desktop listener); the phone listener answers 403. Stored in state.db (meta), so it
survives restarts, is kept by the installer's state backup and is not cleared by "reset settings".
"""

from __future__ import annotations

import ipaddress
import json
import re
import shutil
import subprocess
import threading
import time
from typing import Any, Callable

META_ALLOW = "tailnet_allow"
META_SEEN = "tailnet_seen"
META_ON = "tailnet_allow_on"
REFRESH_SECONDS = 60
SEEN_KEEP_SECONDS = 14 * 24 * 3600
SEEN_RECENT_SECONDS = 7 * 24 * 3600
MAX_ENTRIES = 64
_NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*\.?", re.I)
_TAILNET_V4 = ipaddress.ip_network("100.64.0.0/10")
_TAILNET_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")


def tailscale_status() -> dict | None:
    """``tailscale status --json`` or None. Read-only; never changes Tailscale."""
    binary = shutil.which("tailscale")
    if not binary:
        return None
    try:
        out = subprocess.run([binary, "status", "--json"], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    try:
        data = json.loads(out.stdout)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _ip(value: str) -> str | None:
    try:
        return str(ipaddress.ip_address(value.strip().strip("[]")))
    except ValueError:
        return None


def validate_entry(value: Any) -> str:
    """One allow-list entry, normalised: an IP, or a lower-case device name without a trailing dot."""
    if not isinstance(value, str) or not value.strip() or len(value) > 253:
        raise ValueError("expected a tailnet IP or device name")
    text = value.strip()
    ip = _ip(text)
    if ip:
        addr = ipaddress.ip_address(ip)
        if addr not in _TAILNET_V4 and addr not in _TAILNET_V6:
            raise ValueError(f"{ip} is not a tailnet address (100.64.0.0/10 or fd7a:115c:a1e0::/48)")
        return ip
    if not _NAME.fullmatch(text):
        raise ValueError(f"{text!r} is not a device name or IP")
    return text.lower().rstrip(".")


def validate_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("expected a list of tailnet IPs or device names")
    out: list[str] = []
    for item in value:
        entry = validate_entry(item)
        if entry not in out:
            out.append(entry)
    if len(out) > MAX_ENTRIES:
        raise ValueError(f"at most {MAX_ENTRIES} entries")
    return out


def _node(raw: dict, *, is_self: bool = False) -> dict:
    dns = str(raw.get("DNSName") or "").lower().rstrip(".")
    return {"hostname": str(raw.get("HostName") or ""), "dns": dns, "name": dns.split(".")[0] if dns else "",
            "os": str(raw.get("OS") or ""), "online": bool(raw.get("Online")) or is_self,
            "ips": [ip for ip in (_ip(str(x)) for x in raw.get("TailscaleIPs") or []) if ip], "self": is_self}


def nodes(status: dict | None) -> list[dict]:
    if not status:
        return []
    out = [_node(status["Self"], is_self=True)] if isinstance(status.get("Self"), dict) else []
    out += [_node(p) for p in (status.get("Peer") or {}).values() if isinstance(p, dict)]
    return out


def resolve(entry: str, known: list[dict]) -> set[str]:
    """The tailnet IPs an entry stands for. An IP stands for itself; a name matches a device's
    MagicDNS name (full or first label) or its host name, case-insensitively."""
    ip = _ip(entry)
    if ip:
        return {ip}
    name = entry.lower().rstrip(".")
    found: set[str] = set()
    for node in known:
        if name in (node["dns"], node["name"], node["hostname"].lower()):
            found.update(node["ips"])
    return found


class TailnetAccess:
    def __init__(self, store, status: Callable[[], dict | None] = tailscale_status, clock: Callable[[], float] = time.time):
        self.store = store
        self._status = status
        self._clock = clock
        self._lock = threading.Lock()
        self._known: list[dict] = []
        self._fetched = 0.0
        self._allowed: set[str] = set()
        self._self_ips: set[str] = set()
        self._entries = self._load()
        self._on = self.store.get_meta(META_ON) == "1"
        self._seen_written = 0.0

    # storage -------------------------------------------------------------------

    def _load(self) -> list[str]:
        try:
            return validate_list(json.loads(self.store.get_meta(META_ALLOW) or "[]"))
        except (ValueError, TypeError):
            return []

    def entries(self) -> list[str]:
        with self._lock:
            return list(self._entries)

    def enabled(self) -> bool:
        with self._lock:
            return self._on

    def set_entries(self, value: Any = None, enabled: Any = None) -> list[str]:
        """Save the list and/or the on/off switch (None = leave as it is)."""
        entries = validate_list(value) if value is not None else None
        if enabled is not None and not isinstance(enabled, bool):
            raise ValueError("enabled must be true or false")
        with self._lock:
            if entries is not None:
                self.store.set_meta(META_ALLOW, json.dumps(entries))
                self._entries = entries
            if enabled is not None:
                self.store.set_meta(META_ON, "1" if enabled else "0")
                self._on = enabled
            self._fetched = 0.0   # resolve the new names at once
            result = list(self._entries)
        self._refresh(force=True)
        return result

    # resolution ------------------------------------------------------------------

    def _refresh(self, force: bool = False) -> None:
        now = self._clock()
        with self._lock:
            if not force and now - self._fetched < REFRESH_SECONDS:
                return
            self._fetched = now
        known = nodes(self._status())
        with self._lock:
            if known:
                self._known = known
            self._self_ips = {ip for n in self._known if n["self"] for ip in n["ips"]}
            self._allowed = set().union(*(resolve(e, self._known) for e in self._entries)) if self._entries else set()

    def client_ip(self, peer: str, forwarded: str | None) -> str | None:
        """The caller's tailnet address: the last X-Forwarded-For hop (added by Serve), else None
        when the request came straight from this laptop."""
        if forwarded:
            return _ip(forwarded.split(",")[-1]) or "invalid"
        return None if _ip(peer) and ipaddress.ip_address(_ip(peer)).is_loopback else (_ip(peer) or "invalid")

    def allowed(self, ip: str | None) -> bool:
        """None = this laptop itself (no forwarded address, loopback peer): always allowed."""
        if ip is None:
            return True
        with self._lock:
            if not self._on or not self._entries:
                return True   # off, or nothing listed: no filtering (the pre-0.9.7.4 behaviour)
        self._refresh()
        with self._lock:
            return ip in self._allowed or ip in self._self_ips

    # recently signed in -------------------------------------------------------------

    def seen(self, ip: str | None) -> None:
        """Remember a tailnet address that passed the login (for pre-selecting the list)."""
        if not ip or ip == "invalid":
            return
        now = self._clock()
        with self._lock:
            data = self._seen_map()
            if now - data.get(ip, 0) < 300 and now - self._seen_written < 300:
                return
            data[ip] = now
            data = {k: v for k, v in data.items() if now - v < SEEN_KEEP_SECONDS}
            self.store.set_meta(META_SEEN, json.dumps(data))
            self._seen_written = now

    def _seen_map(self) -> dict:
        try:
            data = json.loads(self.store.get_meta(META_SEEN) or "{}")
            return {str(k): float(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except (ValueError, TypeError):
            return {}

    # the laptop-only view ----------------------------------------------------------

    def overview(self) -> dict:
        self._refresh(force=True)
        now = self._clock()
        with self._lock:
            seen = {ip: at for ip, at in self._seen_map().items() if now - at < SEEN_RECENT_SECONDS}
            known, entries, on = list(self._known), list(self._entries), self._on
        peers = []
        for node in known:
            last = max((seen.get(ip, 0) for ip in node["ips"]), default=0)
            peers.append({**node, "signed_in_at": last or None,
                          "listed": any(set(node["ips"]) & resolve(e, known) for e in entries)})
        peers.sort(key=lambda p: (not p["self"], not p["signed_in_at"], not p["online"], p["name"] or p["hostname"]))
        resolved = [{"entry": e, "ips": sorted(resolve(e, known))} for e in entries]
        return {"entries": entries, "resolved": resolved, "peers": peers,
                "recent": sorted(seen), "available": bool(known),
                "enabled": on, "enforced": bool(on and entries)}
