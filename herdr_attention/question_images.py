"""Image previews inside questions (v0.9.5).

When an open question names an image file, for example Claude Code's "Read
file" permission dialog for a screenshot, the question card shows a thumbnail.
The server decides what may be shown, and a URL never carries a path:

* only files whose real path is inside one of Alex's project folders
  (setting ``questions.image_folders``, default ``~/Projects`` and ``~/Pictures``), so a
  symlink that points out of those folders is refused;
* never private work files (``~/Work``) and never questions from the protected work workspace (w4);
* every path component is opened without following symlinks;
* the type comes from the first bytes of the file, not from its name;
* at most ``MAX_BYTES``.

Anything else shows the path with "Preview not available" and the reason.
Every read checks all of this again on the descriptor it serves.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .cleanup import NEVER_WORKSPACES
from .ui_contract import CONTRACT, api_path
from .visuals import cached_thumbnail, open_beneath

SETTING = "questions.image_folders"
DEFAULT_FOLDERS = ["~/Projects", "~/Pictures"]
PRIVATE_FOLDERS = ("~/Work",)
# A folder or file named "private" anywhere on the path (private-notes, private_system, ctrl-private2)
# is private too, also inside an allowed folder.
PRIVATE_NAME = re.compile(r"(?:^|[-_.])private(?:[-_.\d]|$)", re.I)
MAX_BYTES = 20 * 1024 * 1024
MAX_FOLDERS = 20
MAX_TEXT = 8000
MAX_WRAP = 4            # a long path can wrap over this many terminal lines
MAX_CANDIDATES = 4
REASONS = CONTRACT["question_preview"]["reasons"]

_IMAGE = r"\.(?:png|jpe?g|webp|gif)"
_IS_IMAGE = re.compile(r"[^\0]{1,4096}" + _IMAGE, re.I)
# Claude Code shows the file inside the tool call: Read(/path/to/shot.png)
_CALL = re.compile(r"\bRead\(")
_CALL_PATH = re.compile(r"(.*?" + _IMAGE + r")\)", re.I)
_START = re.compile(r"(?:^|(?<=[\s(\[\"'`=]))(?:~/|/|\.\.?/)")
_RUN = re.compile(r"[^\s()\[\]\"'`<>|,;]{0,4096}")
_TRAIL = ".,:;!?"
MAX_MATCHES = 200       # path starts and Read( calls looked at per text: bounded work on any screen


def _private_named(path: Path) -> bool:
    return any(PRIVATE_NAME.search(part) for part in path.parts)


@dataclass
class Found:
    raw: str
    path: Path | None = None
    reason: str | None = None
    root: Path | None = None
    relative: Path | None = None
    mime: str = ""
    size: int = 0
    mtime_ns: int = 0


def sniff(head: bytes) -> str | None:
    """The image type from the first bytes of a file; None when it is not one we show."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


DECODER = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}


def _run(line: str, start: int) -> str:
    return _RUN.match(line, start).group()


def image_paths(text: str | None) -> list[str]:
    """Image paths named in a dialog or question, the most likely one first.

    Reads the file of a ``Read(...)`` call first, then plain paths from the bottom of
    the screen up (the dialog sits at the bottom). A long path can wrap over lines.
    """
    lines = [line.strip() for line in (text or "")[:MAX_TEXT].splitlines()]
    calls: list[list[str]] = []
    plain: list[str] = []
    budget = MAX_MATCHES
    for i, line in enumerate(lines):
        for match in _CALL.finditer(line):
            budget -= 1
            if budget < 0:
                break
            pieces = [line[match.end():]]
            for extra in lines[i + 1:i + MAX_WRAP]:
                if ")" in pieces[-1]:
                    break
                pieces.append(extra)
            # A wrap usually splits a word; a file name with spaces may also wrap at a space.
            variants = [_CALL_PATH.match(joiner.join(pieces)) for joiner in ("", " ")]
            calls.append([v.group(1).strip() for v in variants if v])
        for match in _START.finditer(line):
            budget -= 1
            if budget < 0:
                break
            value = _run(line, match.start())
            at_end = match.start() + len(value) == len(line)
            following = i + 1
            # A path that runs to the end of the line may continue on the next one.
            while at_end and not _IS_IMAGE.fullmatch(value.rstrip(_TRAIL)) and following < min(len(lines), i + MAX_WRAP):
                piece = _run(lines[following], 0)
                if not piece:
                    break
                value += piece
                at_end = len(piece) == len(lines[following])
                following += 1
            value = value.rstrip(_TRAIL)
            if _IS_IMAGE.fullmatch(value):
                plain.append(value)
    out: list[str] = []
    for value in [*(v for group in reversed(calls) for v in group), *reversed(plain)]:
        if _IS_IMAGE.fullmatch(value) and value not in out:
            out.append(value)
    return out[:MAX_CANDIDATES]


def _under(path: Path, folder: Path) -> bool:
    return path == folder or path.is_relative_to(folder)


def _expand(value: str, home: Path) -> Path | None:
    if value.startswith("~/"):
        return home / value[2:]
    return Path(value) if value.startswith("/") else None


def validate_folders(value) -> list[str]:
    """The setting: 0 to 20 folders, each starting with / or ~/, never private and never too broad."""
    if not isinstance(value, list) or len(value) > MAX_FOLDERS:
        raise ValueError(f"expected a list of at most {MAX_FOLDERS} folders")
    out: list[str] = []
    home = Path.home()
    private = [_expand(f, home) for f in PRIVATE_FOLDERS]
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item) > 400 or not item.isprintable():
            raise ValueError("expected folder paths")
        item = item.strip().rstrip("/")
        path = _expand(item, home)
        if path is None or ".." in Path(item).parts:
            raise ValueError(f"{item or '/'!r}: a folder must start with / or ~/")
        if len(path.parts) < 3:
            raise ValueError(f"{item or '/'!r} is too broad")
        if any(_under(path, h) or _under(h, path) for h in private):
            raise ValueError(f"{item!r}: private work folders can never be shown")
        if item not in out:
            out.append(item)
    return out


class QuestionImages:
    """Adds ``image`` to open questions in a view, and serves the checked image."""

    def __init__(self, settings, cache, home: Path | None = None):
        self.settings = settings
        self.cache = Path(cache)
        self.home = home

    # checks ---------------------------------------------------------------------

    def _home(self) -> Path:
        return self.home or Path.home()

    def _private(self) -> list[Path]:
        home = self._home()
        lexical = [_expand(f, home) for f in PRIVATE_FOLDERS]
        return lexical + [p.resolve() for p in lexical]

    def _roots(self) -> list[Path]:
        folders = self.settings.get(SETTING, DEFAULT_FOLDERS)
        if not isinstance(folders, list):
            folders = DEFAULT_FOLDERS
        home, private, roots = self._home(), self._private(), []
        for folder in folders:
            path = _expand(str(folder).rstrip("/"), home) if isinstance(folder, str) else None
            if path is None or len(path.parts) < 3:
                continue
            try:
                real = path.resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if real.is_dir() and not any(_under(real, h) or _under(h, real) for h in private):
                roots.append(real)
        return roots

    def inspect(self, raw: str, *, workspace: str | None, cwd: str | None) -> Found:
        """Every rule, in order. ``reason`` is None only for a file that may be shown."""
        found = Found(raw)
        if workspace in NEVER_WORKSPACES:
            found.reason = "workspace"
            return found
        home = self._home()
        path = _expand(raw, home)
        if path is None and cwd and not raw.startswith("~"):
            path = Path(cwd) / raw
        if path is None:
            found.reason = "outside"
            return found
        path = Path(os.path.normpath(path))
        found.path = path
        private = self._private()
        if any(_under(path, h) for h in private) or _private_named(path):
            found.reason = "private"
            return found
        try:
            real = path.resolve(strict=True)
            info = os.stat(real)
        except (OSError, RuntimeError):
            found.reason = "missing"
            return found
        if any(_under(real, h) for h in private) or _private_named(real):
            found.reason = "private"
            return found
        root = next((r for r in self._roots() if _under(real, r)), None)
        if root is None:
            found.reason = "outside"
            return found
        if not os.path.isfile(real):
            found.reason = "type"
            return found
        if info.st_size > MAX_BYTES:
            found.reason = "size"
            return found
        found.root, found.relative = root, real.relative_to(root)
        try:
            file, mime = self._open(found)
        except ValueError as exc:
            found.reason = str(exc)
            return found
        except OSError:
            found.reason = "missing"
            return found
        file.close()
        found.mime = mime
        return found

    def _open(self, found: Found):
        """Open without following symlinks; check size and type on that descriptor."""
        try:
            file, info = open_beneath(found.root, found.relative)
        except ValueError:
            raise ValueError("type") from None
        try:
            if info.st_size > MAX_BYTES:
                raise ValueError("size")
            mime = sniff(file.read(16))
            if not mime:
                raise ValueError("type")
            file.seek(0)
        except BaseException:
            file.close()
            raise
        found.size, found.mtime_ns = info.st_size, info.st_mtime_ns
        return file, mime

    # view -------------------------------------------------------------------------

    def _find(self, question: dict, agent: dict) -> Found | None:
        texts = [agent.get("prompt_text") if question.get("terminal") else None, question.get("text")]
        candidates: list[str] = []
        for text in texts:
            candidates += [c for c in image_paths(text) if c not in candidates]
        if not candidates:
            return None
        checked = [self.inspect(c, workspace=agent.get("workspace_id"), cwd=agent.get("cwd"))
                   for c in candidates[:MAX_CANDIDATES]]
        return next((f for f in checked if f.reason is None), None) or \
            next((f for f in checked if f.reason != "missing"), checked[0])

    def _token(self, question: dict, found: Found) -> str:
        source = f"{question.get('id')}\0{found.root / found.relative}\0{found.mtime_ns}\0{found.size}"
        return hashlib.sha256(source.encode()).hexdigest()[:32]

    def _display(self, found: Found) -> str:
        shown = str(found.path or found.raw)
        home = str(self._home())
        return "~" + shown[len(home):] if shown == home or shown.startswith(home + "/") else shown

    def describe(self, question: dict, agent: dict) -> dict | None:
        found = self._find(question, agent)
        if found is None:
            return None
        image = {"id": None, "name": Path(found.raw).name, "path": self._display(found),
                 "available": found.reason is None, "reason": "", "url": None, "thumbnail": None}
        if found.reason:
            image["reason"] = REASONS[found.reason].replace("{mb}", str(MAX_BYTES // (1024 * 1024)))
        else:
            token = self._token(question, found)
            image.update(id=token, url=api_path("question_image", token), thumbnail=api_path("question_thumbnail", token))
        return image

    def decorate(self, view: dict) -> None:
        by_id = {a["identity"]: a for a in view.get("agents", [])}
        for question in view.get("questions") or []:
            question.pop("image", None)
            image = self.describe(question, by_id.get(question.get("identity")) or {})
            if image:
                question["image"] = image

    def read(self, view: dict, token: str, thumbnail: bool = False):
        """(file, mime, size) for an image of an open question; ValueError otherwise."""
        by_id = {a["identity"]: a for a in view.get("agents", [])}
        for question in view.get("questions") or []:
            if (question.get("image") or {}).get("id") != token:
                continue
            found = self._find(question, by_id.get(question.get("identity")) or {})
            if not found or found.reason or self._token(question, found) != token:
                break
            file, mime = self._open(found)
            if thumbnail:
                cached = cached_thumbnail(self.cache, f"{found.root / found.relative}:{found.mtime_ns}:{found.size}",
                                          file, DECODER[mime])
                if cached:
                    file.close()
                    return cached.open("rb"), "image/jpeg", cached.stat().st_size
                file.seek(0)
            return file, mime, found.size
        raise ValueError("no such image")
