"""Make terminal output safe to show, and fingerprint it for change detection."""

from __future__ import annotations

import hashlib
import re

# CSI, OSC, DCS/APC/PM/SOS and single-char escapes.
_ANSI = re.compile(
    r"""
    \x1b\[[0-?]*[ -/]*[@-~]            # CSI ... final byte
  | \x1b\][^\x07\x1b]*(?:\x07|\x1b\\)  # OSC ... BEL or ST
  | \x1b[PX^_][^\x1b]*\x1b\\           # DCS / SOS / PM / APC ... ST
  | \x1b[@-Z\\-_]                      # two-byte escapes
    """,
    re.VERBOSE,
)
# C0 controls except tab/newline, DEL, and C1 controls.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
# Bidi overrides can make text render in a misleading order.
_BIDI = re.compile(r"[‪-‮⁦-⁩]")

# Things that change every second without meaning progress: digits (timers,
# token counters), braille/circle spinners and the agents' busy glyphs.
_NOISE = re.compile(r"[0-9⠀-⣿◐-◓✢✶✻✽·*✳◐◑◒◓]+")
_SPACE = re.compile(r"\s+")


def strip_terminal(text: str) -> str:
    text = _ANSI.sub("", text or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL.sub("", text)
    return _BIDI.sub("", text)


def redact(text: str, patterns: list[str]) -> tuple[str, int]:
    """Replace matches of the configured patterns. Returns (text, count)."""
    total = 0
    for pattern in patterns:
        try:
            text, n = re.subn(pattern, "[redacted]", text)
        except re.error:
            continue
        total += n
    return text, total


def clean_output(text: str, patterns: list[str], max_lines: int) -> dict:
    cleaned = strip_terminal(text)
    lines = cleaned.rstrip("\n").split("\n")
    if len(lines) > max_lines:
        lines = lines[-max_lines:]
    cleaned, redactions = redact("\n".join(lines), patterns)
    return {"text": cleaned, "redactions": redactions, "lines": len(lines)}


def fingerprint(text: str) -> str:
    """Hash of the output with timers and spinners removed.

    Only the hash is kept; the output itself is never stored.
    """
    normalized = _NOISE.sub("", strip_terminal(text))
    normalized = _SPACE.sub(" ", normalized).strip()
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


# Readable terminal text --------------------------------------------------------
#
# The normal output view: no ANSI codes, no box borders, no spinners, no UI
# chrome (key hints, status bars), blank runs collapsed. Text and code are
# kept as they are. The unfiltered lines stay available on request.

_BORDER_ONLY = re.compile(r"^[\s─━│┃┄┅┆┇┈┉┊┋╌╍╎╏═║╒-╬╭╮╯╰╱╲╳▔▁▏▕\-_=+*·•]*$")
_EDGE = re.compile(r"^\s*[│┃║]\s?|\s?[│┃║]\s*$")
_SPINNER_ONLY = re.compile(r"^\s*[⠀-⣿◐◑◒◓✳✶✻✽✢·*]+\s*$")
_CHROME = re.compile(
    r"esc to (interrupt|cancel|close)|shift\+tab to cycle|⏵⏵|\? for shortcuts|ctrl\+[a-z] to |"
    r"Update available!|auto mode on|tab to queue|^\s*[❯›>]\s*$|context left|bypass permissions",
    re.IGNORECASE)


def readable(text: str) -> str:
    out: list[str] = []
    for line in strip_terminal(text).split("\n"):
        if _BORDER_ONLY.match(line) or _SPINNER_ONLY.match(line) or _CHROME.search(line):
            if out and out[-1] != "":
                out.append("")
            continue
        out.append(_EDGE.sub("", line).rstrip())
    while out and out[0] == "":
        out.pop(0)
    while out and out[-1] == "":
        out.pop()
    collapsed: list[str] = []
    for line in out:
        if line == "" and collapsed and collapsed[-1] == "":
            continue
        collapsed.append(line)
    return "\n".join(collapsed)
