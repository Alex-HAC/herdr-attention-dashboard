"""Live terminal view (v0.9.8): an agent's screen with its colours, read-only.

Herdr's socket ``agent.read`` with ``format: "ansi"`` returns the visible screen with SGR colour
codes (truecolor ``38;2;r;g;b``, 256 colours, bold, dim, ...). It is a passive read of Herdr's own
screen copy: it never scrolls, types or focuses, so it is safe for a busy agent. History ("recent")
is only read once per opening, as plain text, and only while the agent is not working.

Only SGR sequences survive :func:`clean_ansi`; every other escape (cursor moves, OSC titles and
links, device queries) and control character is dropped before anything leaves the server. The
configured ``output.redact`` patterns apply to the visible text, as for the plain output view.
Nothing read here is logged or stored.
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
from typing import Callable

_SGR = re.compile(r"\x1b\[[0-9;:]*m")
_ESC = re.compile(r"\x1b(?:\[[0-9;:?<>=!]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)?|[PX^_][^\x1b]*(?:\x1b\\)?|[ -/]*[0-~])")
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\x9b]")

MIN_INTERVAL = 0.75      # one Herdr read per pane per 0.75 s, however many viewers
MAX_PARALLEL = 2         # terminal reads at the same time, all panes together
HISTORY_LINES = 200
SCREEN_LINES = 200


def clean_ansi(text: str) -> str:
    """Keep the text and its SGR (colour/style) codes; drop every other escape and control char."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "")
    out: list[str] = []
    last = 0
    for m in _SGR.finditer(text):
        out.append(_CTRL.sub("", _ESC.sub("", text[last:m.start()])))
        out.append(m.group(0))
        last = m.end()
    out.append(_CTRL.sub("", _ESC.sub("", text[last:])))
    return "".join(out)


def plain(text: str) -> str:
    return _SGR.sub("", text or "")


def redact_ansi(text: str, patterns: list[str]) -> tuple[str, int]:
    """Apply the redaction patterns to the visible characters, keeping the colours around them."""
    if not patterns or not text:
        return text, 0
    items: list[tuple[bool, str]] = []   # (is_code, text); visible items are single characters
    last = 0
    for m in _SGR.finditer(text):
        items += [(False, c) for c in text[last:m.start()]]
        items.append((True, m.group(0)))
        last = m.end()
    items += [(False, c) for c in text[last:]]
    total = 0
    for pattern in patterns:
        try:
            rx = re.compile(pattern)
        except re.error:
            continue
        chars = [i for i, (code, _) in enumerate(items) if not code]
        visible = "".join(items[i][1] for i in chars)
        spans = [(m.start(), m.end()) for m in rx.finditer(visible) if m.end() > m.start()]
        for start, end in reversed(spans):
            idx = chars[start:end]
            items[idx[0]] = (False, "[redacted]")
            for i in reversed(idx[1:]):
                items[i] = (True, "")   # keep the slot; colour codes in between stay
        total += len(spans)
    return "".join(t for _, t in items), total


def digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:16]


class TerminalReader:
    """Throttled, shared screen reads: at most one Herdr read per pane per MIN_INTERVAL."""

    def __init__(self, client, clock: Callable[[], float] = time.monotonic):
        self.client = client
        self.clock = clock
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, str]] = {}
        self._pane_locks: dict[str, threading.Lock] = {}
        self._parallel = threading.BoundedSemaphore(MAX_PARALLEL)

    def screen(self, pane_id: str) -> str:
        with self._lock:
            lock = self._pane_locks.setdefault(pane_id, threading.Lock())
        with lock:
            cached = self._cache.get(pane_id)
            if cached and self.clock() - cached[0] < MIN_INTERVAL:
                return cached[1]
            with self._parallel:
                text = self.client.read_ansi(pane_id, SCREEN_LINES)
            text = clean_ansi(text)
            with self._lock:
                self._cache[pane_id] = (self.clock(), text)
                if len(self._cache) > 64:
                    oldest = min(self._cache, key=lambda k: self._cache[k][0])
                    self._cache.pop(oldest, None)
            return text

    def history(self, pane_id: str, screen_lines: int) -> str:
        """Earlier output as plain text (Herdr keeps no colours for it), without the visible screen."""
        with self._parallel:
            text = self.client.read_output(pane_id, HISTORY_LINES, "recent")
        lines = clean_ansi(text).rstrip("\n").split("\n")
        if screen_lines and len(lines) > screen_lines:
            lines = lines[:-screen_lines]
        else:
            lines = []
        while lines and not lines[-1].strip():
            lines.pop()
        return "\n".join(lines)
