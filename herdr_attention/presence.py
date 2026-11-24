"""Laptop presence from keyboard/mouse idle signals.

The desktop plugin reports only "idle" (no input for N seconds) or "active"
(input happened), plus a heartbeat. No keystrokes, no window titles.
Idle input is a guess at whether you are away, not proof.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from .config import Settings


class Presence:
    def __init__(self, settings: Settings, clock: Callable[[], float] = time.time):
        self.settings = settings
        self.clock = clock
        self._lock = threading.Lock()
        self.idle = False
        self.idle_since: float | None = None
        self.last_report: float | None = None
        self.source: str | None = None

    def report(self, state: str, idle_seconds: float = 0.0, source: str = "desktop") -> None:
        if state not in ("idle", "active"):
            raise ValueError("state must be idle or active")
        with self._lock:
            now = self.clock()
            self.last_report = now
            self.source = source
            if state == "active":
                self.idle = False
                self.idle_since = None
            else:
                since = now - max(0.0, float(idle_seconds))
                # Heartbeats while idle must not move the start forward.
                if not self.idle or self.idle_since is None or since < self.idle_since:
                    self.idle_since = since
                self.idle = True

    def state(self) -> dict:
        """present | away | unknown, plus what it is based on."""
        with self._lock:
            now = self.clock()
            delay = float(self.settings.get("presence.phone_delay_minutes", 5)) * 60
            stale = float(self.settings.get("presence.heartbeat_stale_seconds", 180))
            out = {"last_report": self.last_report, "idle_since": self.idle_since, "delay_seconds": delay}
            if self.last_report is None or now - self.last_report > stale:
                out["state"] = "unknown"
                out["counts_as"] = self.settings.get("presence.unknown_means", "away")
                return out
            if self.idle and self.idle_since is not None:
                out["idle_seconds"] = now - self.idle_since
                out["state"] = "away" if now - self.idle_since >= delay else "idle"
                out["counts_as"] = "away" if out["state"] == "away" else "present"
                return out
            out["state"] = "present"
            out["counts_as"] = "present"
            return out
