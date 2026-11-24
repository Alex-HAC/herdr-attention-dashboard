"""v0.9.8.8 file viewer: one text file an agent mentioned, read on request, read-only.

Alex's brief: tap a path like team-registry/docs/working-styles.md in a question, report, reply or
the terminal and read it in the dashboard. Rules:

- GET only, behind the same login and tailnet allow list as all agent data (api.py).
- Only files under the watched agents' folders (their cwd and its git repo root) and ~/Projects,
  ~/Work, ~/Documents. The real path (symlinks resolved) must stay inside one of them.
- No ``..`` at all, no hidden files or folders (.env, .ssh, .claude, .codex, .git ...), nothing
  named like a secret (credentials, tokens, passwords, API or private keys).
- Text types only (markdown, plain text and a few like it), UTF-8, at most 1 MB.
- The text goes to the browser that asked and nowhere else: never stored, never logged, never
  given to the summary agent (summarize.py does not import this module; tests check it).
"""
from __future__ import annotations

import difflib
import os
import re
from pathlib import Path

from .ui_contract import CONTRACT

FILES = CONTRACT["files"]
EXTENSIONS = frozenset(FILES["extensions"])
MARKDOWN = frozenset(FILES["markdown"])
MAX_BYTES = int(FILES["max_bytes"])
FIXED_ROOTS = ("~/Projects", "~/Work", "~/Documents")
MAX_PATH = 1024
# Names that look like secrets, matched on each path part between separators.
SECRET_NAME = re.compile(
    r"(^|[._ -])(env|secrets?|credentials?|creds|passwords?|passwd|tokens?|api[_-]?keys?|private[_-]?keys?|"
    r"id_(rsa|dsa|ecdsa|ed25519)|keychain|keyring|vault|htpasswd|netrc|pgpass)([._ -]|$)", re.I)
# Never opened, also as the target of a symlink (hidden folders are refused anyway; named for clarity).
DENIED_HOME = (".ssh", ".claude", ".codex", ".config", ".local", ".gnupg", ".aws", ".docker", ".kube", ".password-store")
DIFF_LINES = 20000
DIFF_CONTEXT = 3


class FileError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _home() -> Path:
    return Path(os.path.expanduser("~")).resolve()


def _under(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def short_home(path: Path, home: Path | None = None) -> str:
    home = home or _home()
    return "~/" + str(path.relative_to(home)) if _under(path, home) and path != home else str(path)


def repo_root(path: Path, home: Path) -> Path | None:
    """The git work tree that holds ``path`` (a .git folder or file), below home."""
    for candidate in (path, *path.parents):
        if candidate == home or not _under(candidate, home):
            return None
        if (candidate / ".git").exists():
            return candidate
    return None


def _refused_part(parts) -> str | None:
    for part in parts:
        if part.startswith("."):
            return "hidden files and folders are not opened"
        if SECRET_NAME.search(part):
            return "files that may hold secrets are not opened"
    return None


def roots(cwds, home: Path | None = None) -> list[Path]:
    """The folders the viewer may read: ~/Projects, ~/Work, ~/Documents and each watched agent's
    folder and repo. An agent sitting in home itself (or outside it) adds nothing: that would open
    every file in home."""
    home = home or _home()
    out: list[Path] = []
    for raw in FIXED_ROOTS:
        path = home / raw[2:]
        if path.is_dir():
            out.append(path.resolve())
    for cwd in cwds:
        if not cwd or not isinstance(cwd, str):
            continue
        path = Path(os.path.expanduser(cwd)) if cwd.startswith("~/") else Path(cwd)
        if not path.is_absolute():
            continue
        try:
            path = path.resolve(strict=True)
        except OSError:
            continue
        for root in (path, repo_root(path, home)):
            if root is None or root == home or not _under(root, home):
                continue
            if _refused_part(root.relative_to(home).parts):
                continue
            out.append(root)
    seen, unique = set(), []
    for root in out:
        if root not in seen:
            seen.add(root)
            unique.append(root)
    return unique


def _check_name(path: Path) -> None:
    ext = path.suffix.lower().lstrip(".")
    if ext not in EXTENSIONS:
        raise FileError(415, "only text files (" + ", ".join(sorted(EXTENSIONS)) + ") are opened")


def resolve(raw: str, bases, allowed: list[Path], home: Path | None = None) -> Path:
    """The real path of ``raw`` (absolute, ~/..., or relative to one of ``bases``), or FileError."""
    home = home or _home()
    if not isinstance(raw, str) or not raw.strip():
        raise FileError(400, "no path given")
    raw = raw.strip()
    if len(raw) > MAX_PATH or "\x00" in raw or "\\" in raw or re.match(r"^[a-z][a-z0-9+.-]*:", raw, re.I):
        raise FileError(400, "not a file path")
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if ".." in parts:
        raise FileError(403, "paths with .. are not opened")
    if raw.startswith("~") and not raw.startswith("~/"):
        raise FileError(403, "only your own home folder")
    if not parts:
        raise FileError(400, "not a file path")
    reason = _refused_part(parts)
    if reason:
        raise FileError(403, reason)
    if raw.startswith("~/"):
        candidates = [home.joinpath(*parts[1:])]
    elif raw.startswith("/"):
        candidates = [Path("/").joinpath(*parts)]
    else:
        candidates = [Path(b).joinpath(*parts) for b in bases]
    _check_name(candidates[0] if candidates else Path(parts[-1]))
    inside = [c for c in candidates if any(_under(c, r) for r in allowed)]
    if not inside:
        raise FileError(403, "outside the folders the dashboard may read")
    for candidate in inside:
        try:
            real = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        # The real path (after symlinks) must pass every check again.
        if not any(_under(real, r) for r in allowed):
            raise FileError(403, "the link points outside the folders the dashboard may read")
        if _under(real, home) and real.relative_to(home).parts[:1] and real.relative_to(home).parts[0] in DENIED_HOME:
            raise FileError(403, "hidden files and folders are not opened")
        reason = _refused_part(real.parts[1:])
        if reason:
            raise FileError(403, reason)
        _check_name(real)
        if not real.is_file():
            raise FileError(404, "file not found")
        return real
    raise FileError(404, "file not found")


def read(raw: str, cwd: str | None, cwds, home: Path | None = None) -> dict:
    """One file for the viewer: resolved against the asking agent's folder and repo first, then
    every allowed folder. The text is returned, never kept."""
    home = home or _home()
    allowed = roots(list(cwds) + ([cwd] if cwd else []), home)
    first = roots([cwd], home) if cwd else []
    first = [r for r in first if str(r) not in {str(home / f[2:]) for f in FIXED_ROOTS}]
    bases = first + [r for r in allowed if r not in first]
    real = resolve(raw, bases, allowed, home)
    stat = real.stat()
    if stat.st_size > MAX_BYTES:
        raise FileError(413, f"too big to show ({stat.st_size / 1048576:.1f} MB; the viewer opens up to 1 MB)")
    with open(real, "rb") as handle:
        data = handle.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise FileError(413, "too big to show (the viewer opens up to 1 MB)")
    if b"\x00" in data:
        raise FileError(415, "not a text file")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise FileError(415, "not UTF-8 text") from None
    ext = real.suffix.lower().lstrip(".")
    return {"requested": raw, "path": str(real), "shown": short_home(real, home), "name": real.name,
            "mtime": stat.st_mtime, "size": len(data), "kind": "markdown" if ext in MARKDOWN else "text",
            "text": text}


def diff(original: str, draft: str) -> dict:
    """Line diff, original -> draft: rows {op: same|add|del|skip, text|n}; long unchanged runs fold."""
    a, b = original.splitlines()[:DIFF_LINES], draft.splitlines()[:DIFF_LINES]
    rows, added, removed = [], 0, 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            same = a[i1:i2]
            if len(same) > DIFF_CONTEXT * 2 + 1:
                head = same[:DIFF_CONTEXT] if rows else []
                tail = same[-DIFF_CONTEXT:] if i2 < len(a) or j2 < len(b) else []
                rows += [{"op": "same", "text": t} for t in head]
                rows.append({"op": "skip", "n": len(same) - len(head) - len(tail)})
                rows += [{"op": "same", "text": t} for t in tail]
            else:
                rows += [{"op": "same", "text": t} for t in same]
            continue
        rows += [{"op": "del", "text": t} for t in a[i1:i2]]
        rows += [{"op": "add", "text": t} for t in b[j1:j2]]
        removed += i2 - i1
        added += j2 - j1
    return {"rows": rows, "added": added, "removed": removed, "same": not added and not removed}
