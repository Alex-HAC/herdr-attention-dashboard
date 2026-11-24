"""Proven accounts (v0.9.7.1): an account for an agent not started with herdr-spawn, only from a
hard fact about that very session.

Facts are config folders; the map from folder to account is Alex's own routing accounts
(routing.toml [accounts]: env CLAUDE_CONFIG_DIR / CODEX_HOME, or no folder = the tool's documented
default folder, ~/.claude or ~/.codex). Two facts are read:

* config_folder: the session's trace file lies inside one account's config folder (the trace
  locator already found the file inside traces.claude_dirs / traces.codex_dirs);
* environment: the agent process of the pane names its config folder (CLAUDE_CONFIG_DIR or
  CODEX_HOME read from /proc/<pid>/environ, read-only, only those two names are kept); when the
  variable is not set, the tool uses its documented default folder.

Never proof: the agent kind, its working folder, a name guess, a folder two accounts share, a
trace inside two account folders. Two facts that name different folders prove nothing. A herdr-spawn
launch record always wins (account_icons / quotas read it first). Results are cached per session;
the view only looks them up. Nothing here reads tokens, e-mails or config.toml.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Callable

from .traces import split_identity

FOLDER_ENV = {"claude": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME"}
DEFAULT_FOLDER = {"claude": "~/.claude", "codex": "~/.codex"}      # each tool's documented default
TRACE_SUBDIR = {"claude": "projects", "codex": "sessions"}
AMBIGUOUS = "ambiguous"
MAX_PROCESSES = 64
RETRY_SECONDS = 600
FOLDERS_SECONDS = 60


def _folder(value: str) -> Path | None:
    path = Path(os.path.expanduser(value))
    return path.resolve() if path.is_absolute() else None


def folder_map(accounts: dict | None) -> dict[str, dict[Path, list[str]]]:
    """{kind: {config folder: [routing account names]}} from routing.toml [accounts].

    An account without a folder for its own kind (env = {}) uses that tool's default folder."""
    out: dict[str, dict[Path, list[str]]] = {kind: {} for kind in FOLDER_ENV}
    for name, account in (accounts or {}).items():
        env = (account or {}).get("env") or {}
        own = str(name).split("-")[0]
        for kind, var in FOLDER_ENV.items():
            value = env.get(var) if var in env else (DEFAULT_FOLDER[kind] if kind == own else None)
            folder = _folder(value) if isinstance(value, str) and value else None
            if folder:
                out[kind].setdefault(folder, []).append(name)
    return out


def account_for(folders: dict[Path, list[str]], folder: Path | None) -> str | None:
    """The one account whose config folder this is; a shared folder proves nothing."""
    names = folders.get(folder) if folder else None
    return names[0] if names and len(names) == 1 else None


def trace_folder(path: Path | None, kind: str, folders: dict[Path, list[str]]) -> Path | str | None:
    """The configured account folder that holds this trace (also through a symlinked subfolder),
    AMBIGUOUS when it lies in two, None when in none."""
    if path is None:
        return None
    hits = set()
    for folder in folders:
        bases = {folder}
        try:
            bases.add((folder / TRACE_SUBDIR[kind]).resolve())
        except OSError:
            pass
        if any(path.is_relative_to(base) for base in bases):
            hits.add(folder)
    return hits.pop() if len(hits) == 1 else AMBIGUOUS if hits else None


class ProcReader:
    """Read-only /proc: children, the program name, and only the two folder variables."""

    def __init__(self, root: str = "/proc"):
        self.root = Path(root)

    def children(self, pid: int) -> list[int]:
        found = []
        for task in (self.root / str(pid) / "task").iterdir():
            found += [int(x) for x in (task / "children").read_text().split()]
        return found

    def argv(self, pid: int) -> list[str]:
        return [p for p in (self.root / str(pid) / "cmdline").read_bytes().decode(errors="replace").split("\0") if p]

    def folder_env(self, pid: int) -> dict[str, str]:
        out = {}
        for entry in (self.root / str(pid) / "environ").read_bytes().split(b"\0"):
            for var in FOLDER_ENV.values():
                if entry.startswith(var.encode() + b"="):
                    out[var] = entry[len(var) + 1:].decode(errors="replace")
        return out


def is_agent(argv: list[str], kind: str) -> bool:
    """The agent program itself: `claude ...` / `codex ...` (or run by node/bun). A wrapper script
    such as `claude-personal` is not the agent: it may set the folder only for its child."""
    names = [Path(a).name for a in argv[:2]]
    return bool(names) and (names[0] == kind or (names[0] in ("node", "bun") and len(names) > 1 and names[1] == kind))


def agent_process(info: dict, procs, kind: str) -> int | None:
    """The agent's own process in the pane: the topmost `claude`/`codex` under the pane's shell.
    Two at the same depth (or none) is not proof."""
    try:
        level = [int(info["shell_pid"])]
    except (KeyError, TypeError, ValueError):
        return None
    seen = set()
    while level and len(seen) < MAX_PROCESSES:
        matches = [pid for pid in level if is_agent(procs.argv(pid), kind)]
        if matches:
            return matches[0] if len(matches) == 1 else None
        seen.update(level)
        level = [child for pid in level for child in procs.children(pid) if child not in seen]
    return None


def env_folder(info: dict, procs, kind: str) -> Path | None:
    """The config folder the agent process runs with, from its own environment."""
    pid = agent_process(info, procs, kind)
    if pid is None:
        return None
    value = procs.folder_env(pid).get(FOLDER_ENV[kind])
    return _folder(value) if value else _folder(DEFAULT_FOLDER[kind])


class AccountProofs:
    """Proofs per session, worked out on the trace thread and only looked up by the view."""

    def __init__(self, accounts: Callable[[], dict], find_trace: Callable[[str, str], Path | None],
                 process_info: Callable[[str], dict], procs=None, clock=time.monotonic):
        self.accounts, self.find_trace, self.process_info = accounts, find_trace, process_info
        self.procs = procs or ProcReader()
        self.clock = clock
        self._lock = threading.Lock()
        self._facts: dict[str, dict] = {}
        self._folders: tuple | None = None

    def folders(self) -> dict[str, dict[Path, list[str]]]:
        """Alex's routing accounts as folders, read at most once a minute (the view stays fast)."""
        now = self.clock()
        if self._folders and now - self._folders[0] < FOLDERS_SECONDS:
            return self._folders[1]
        try:
            folders = folder_map(self.accounts())
        except Exception:      # no routing file or a bad one: no proofs
            folders = folder_map({})
        self._folders = (now, folders)
        return folders

    def observe(self, agents: list[dict]) -> None:
        """Read the facts of sessions without a launch record. The trace fact is looked up until
        found (the locator has its own back-off); the environment once per session and pane, again
        after RETRY_SECONDS only when it could not be read."""
        folders = None
        now = self.clock()
        live = set()
        for agent in agents:
            identity = agent.get("identity") or ""
            parsed = split_identity(identity)
            if not parsed or not agent.get("present") or agent.get("in_process") or (agent.get("launch") or {}).get("account"):
                continue
            live.add(identity)
            kind, session = parsed
            pane = agent.get("pane_id")
            with self._lock:
                facts = dict(self._facts.get(identity) or {"kind": kind})
            if facts.get("pane") != pane:
                facts.update(pane=pane, env=None, env_at=None)
            if facts.get("trace") is None:
                folders = folders or self.folders()
                try:
                    facts["trace"] = trace_folder(self.find_trace(kind, session), kind, folders[kind])
                except OSError:
                    pass
            if facts.get("env") is None and pane and (facts.get("env_at") is None or now - facts["env_at"] >= RETRY_SECONDS):
                facts["env_at"] = now
                try:
                    facts["env"] = env_folder(self.process_info(pane), self.procs, kind)
                except Exception:   # Herdr or /proc not readable now: try again later
                    facts["env"] = None
            with self._lock:
                self._facts[identity] = facts
        with self._lock:
            for gone in [i for i in self._facts if i not in live]:
                del self._facts[gone]

    def result(self, identity: str, folders=None) -> dict | None:
        """{"account", "source"} when the facts agree on one account's folder, else None."""
        with self._lock:
            facts = dict(self._facts.get(identity) or {})
        if not facts:
            return None
        trace, env = facts.get("trace"), facts.get("env")
        if trace == AMBIGUOUS or (trace is not None and env is not None and trace != env):
            return None                       # two facts that disagree prove nothing
        folder = trace or env
        folders = folders or self.folders()
        account = account_for(folders.get(facts["kind"], {}), folder)
        if not account:
            return None
        source = "both" if trace and env else "config_folder" if trace else "environment"
        return {"account": account, "source": source}

    def decorate(self, agents: list[dict]) -> None:
        """agent.account_proof for agents without a launch record; an in-process subagent takes its
        parent's account only when the parent's is certain (recorded at launch or proven)."""
        folders = self.folders()
        by_id = {a.get("identity"): a for a in agents}
        for agent in agents:
            agent.pop("account_proof", None)
            if agent.get("in_process") or (agent.get("launch") or {}).get("account"):
                continue
            proof = self.result(agent.get("identity") or "", folders)
            if proof:
                agent["account_proof"] = proof
        for agent in agents:
            if not agent.get("in_process") or (agent.get("launch") or {}).get("account"):
                continue
            parent = by_id.get(agent.get("in_process_parent")) or {}
            account = (parent.get("launch") or {}).get("account") or (parent.get("account_proof") or {}).get("account")
            if account:
                agent["account_proof"] = {"account": account, "source": "parent"}
