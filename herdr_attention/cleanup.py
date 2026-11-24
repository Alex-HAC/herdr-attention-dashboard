"""Clean up Herdr panes: a preview the server makes, then a confirmed close.

Read-only until Alex confirms. The preview lists candidate panes with a plain
reason, and what it left out and why. Confirm accepts only panes from a preview
this server made, checks each pane again against the same rules, and closes
the ones that still pass with ``herdr pane close``. Nothing on disk is deleted
(folders, worktrees and branches stay). Every close is logged in the store.

Slow checks (git status, process trees) run in a worker thread, never on the
HTTP thread. A check that cannot give an answer makes the pane NOT a candidate.
"""

from __future__ import annotations

import itertools
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from .herdr import HerdrClient, HerdrError

# The protected work workspace: never cleaned up, even if switched on by mistake.
NEVER_WORKSPACES = frozenset({"w4"})
SETTING = "cleanup.workspaces"
PREVIEW_SECONDS = 15 * 60
GIT_SECONDS = 5.0
MAX_PANES = 200
MAX_PROCESSES = 400
# stdio MCP servers that agents keep as children; they are not background jobs.
MCP_HELPER = re.compile(r"(?i)(--stdio\b|modelcontextprotocol|(?:^|[/@_-])mcp(?:$|[/_-]))")

# The only copy of the reasons the preview gives.
REASONS = {
    "done": "Finished. Nothing unread, no open question, no background job, no uncommitted work.",
    "idle": "Idle. Nothing unread, no open question, no background job, no uncommitted work.",
    "ended": "The agent session ended (closed or crashed). Only the shell is left.",
    "shell": "Plain shell left from {name}, which ended.",
    "never": "Never cleaned up.",
    "off": "Clean up is off for this workspace.",
    "unwatched": "Not watched by the dashboard.",
    "focused": "Herdr shows this pane now.",
    "caller": "You are using this pane.",
    "working": "Working.",
    "blocked": "Waiting at a prompt.",
    "question": "Has an open question for you.",
    "unread": "Has an unread update. Read it first.",
    "attention": "Is in the attention list ({tier}).",
    "coordinator": "Coordinator with {n} active worker(s).",
    "job": "A background job is running ({name}).",
    "program": "A program is running ({name}).",
    "processes_unknown": "Could not check its processes.",
    "dirty": "Uncommitted work in its folder ({n} file(s)).",
    "git_unknown": "Could not check its folder for uncommitted work.",
    "untracked": "The dashboard does not track this agent yet.",
    "plain_shell": "Plain shell. No agent ran here.",
    "changed": "Changed since the preview.",
    "gone": "The pane is gone.",
}
CANDIDATE_KINDS = ("done", "idle", "ended", "shell")


class CleanupReader(HerdrClient):
    """Read-only Herdr calls the preview needs. Writes go through PaneCloser."""
    ALLOWED_METHODS = frozenset({"pane.list", "pane.process_info"})

    def process_info(self, pane_id: str) -> dict:
        return self.call("pane.process_info", {"pane_id": pane_id}).get("process_info") or {}


class PaneCloser:
    """The one write: ``herdr pane close <pane_id>``. Nothing else, nothing on disk."""

    def __init__(self, socket_path: str | None = None):
        self.socket_path = socket_path

    def close(self, pane_id: str) -> None:
        env = dict(os.environ)
        if self.socket_path:
            env["HERDR_SOCKET_PATH"] = self.socket_path
        subprocess.run(["herdr", "pane", "close", pane_id], env=env, capture_output=True,
                       text=True, timeout=10, check=True)


def git_state(cwd: str | None) -> tuple[str, int]:
    """('clean'|'dirty'|'none'|'unknown', changed files). 'none' = not a git folder."""
    if not cwd or not os.path.isdir(cwd):
        return "unknown", 0
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    try:
        out = subprocess.run(["git", "-C", cwd, "status", "--porcelain", "--untracked-files=normal"],
                             env=env, capture_output=True, text=True, timeout=GIT_SECONDS)
    except (OSError, subprocess.SubprocessError):
        return "unknown", 0
    if out.returncode == 0:
        count = sum(1 for line in out.stdout.splitlines() if line.strip())
        return ("dirty", count) if count else ("clean", 0)
    if "not a git repository" in (out.stderr or ""):
        return "none", 0
    return "unknown", 0


class ProcFS:
    """Process tree from /proc. Any read error is reported, never guessed."""

    def __init__(self, root: str = "/proc"):
        self.root = Path(root)

    def children(self, pid: int) -> list[int]:
        found = []
        for task in (self.root / str(pid) / "task").iterdir():
            text = (task / "children").read_text()
            found += [int(x) for x in text.split()]
        return found

    def cmdline(self, pid: int) -> list[str]:
        return [p for p in (self.root / str(pid) / "cmdline").read_text().split("\0") if p]


def process_state(info: dict, procs, *, agent: bool) -> tuple[str, str]:
    """('quiet'|'busy'|'unknown', process name).

    A shell is quiet when it is at its own prompt with no children. An agent is
    quiet when nothing runs under it except stdio MCP helpers."""
    try:
        shell = int(info["shell_pid"])
        foreground = int(info["foreground_process_group_id"])
    except (KeyError, TypeError, ValueError):
        return "unknown", ""
    names = {int(p.get("pid", -1)): (p.get("name") or "") for p in info.get("foreground_processes") or []}
    if not agent and foreground != shell:
        return "busy", names.get(foreground) or "a program"
    seen, todo = set(), [(shell, False)]
    try:
        while todo:
            pid, helper = todo.pop()
            if pid in seen:
                continue
            seen.add(pid)
            if len(seen) > MAX_PROCESSES:
                return "unknown", ""
            for child in procs.children(pid):
                argv = procs.cmdline(child)
                is_helper = helper or bool(MCP_HELPER.search(" ".join(argv)))
                if not (agent and child == foreground) and not is_helper:
                    return "busy", Path(argv[0]).name if argv else "a program"
                todo.append((child, is_helper))
    except (OSError, ValueError):
        return "unknown", ""
    return "quiet", ""


def short_path(path: str | None) -> str:
    home = os.path.expanduser("~")
    path = path or ""
    return "~" + path[len(home):] if path == home or path.startswith(home + "/") else path


class Cleanup:
    def __init__(self, *, settings, store, view: Callable[[], dict], ended_agents: Callable[[], list[dict]],
                 reader, closer, clock=time.time, git=git_state, procs=None, unread=None):
        self.settings, self.store, self.view, self.ended_agents = settings, store, view, ended_agents
        self.reader, self.closer, self.clock, self.git = reader, closer, clock, git
        self.procs = procs or ProcFS()
        self.unread = unread or (lambda: [])
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._jobs: dict[int, dict] = {}
        self._previews: dict[int, dict] = {}
        self.store._exec("CREATE TABLE IF NOT EXISTS cleanup_log (id INTEGER PRIMARY KEY, at REAL NOT NULL, "
                         "who TEXT, preview_id INTEGER, pane_id TEXT, workspace_id TEXT, name TEXT, "
                         "reason TEXT, result TEXT, error TEXT)")

    # public -------------------------------------------------------------------

    def start_preview(self, workspace: str | None, *, who: str, caller_pane: str | None = None) -> dict:
        job = self._new_job("preview", who=who, workspace=workspace)
        self._run(job, lambda: self._preview(job, workspace, caller_pane))
        return self.job(job["id"])

    def start_confirm(self, preview_id: int, panes: list[str], *, who: str) -> dict:
        with self._lock:
            preview = self._previews.get(preview_id)
            if not preview or preview["used"] or self.clock() > preview["expires_at"]:
                raise ValueError("That preview is no longer valid. Make a new preview.")
            unknown = [p for p in panes if p not in preview["candidates"]]
            if unknown or not panes:
                raise ValueError("Only panes from this preview can be closed.")
            preview["used"] = True
        job = self._new_job("confirm", who=who, workspace=preview["workspace"], preview_id=preview_id)
        self._run(job, lambda: self._confirm(job, preview, list(dict.fromkeys(panes))))
        return self.job(job["id"])

    def job(self, job_id: int) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def log(self, limit: int = 30) -> list[dict]:
        rows = self.store._exec("SELECT at, who, pane_id, workspace_id, name, reason, result, error FROM cleanup_log "
                                "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        keys = ("at", "who", "pane_id", "workspace_id", "name", "reason", "result", "error")
        return [dict(zip(keys, row)) for row in rows]

    def overview(self) -> dict:
        return {"log": self.log(), "workspaces": self.allowed(), "never": sorted(NEVER_WORKSPACES)}

    def allowed(self) -> list[str]:
        watched = set(self.settings.get("watch.enabled_workspaces", []) or [])
        return sorted(w for w in self.settings.get(SETTING, []) or [] if w in watched and w not in NEVER_WORKSPACES)

    # jobs ---------------------------------------------------------------------

    def _new_job(self, kind: str, **extra) -> dict:
        with self._lock:
            job = {"id": next(self._ids), "kind": kind, "status": "running", "at": self.clock(), **extra}
            self._jobs[job["id"]] = job
            for old in sorted(self._jobs)[:-20]:
                self._jobs.pop(old, None)
            return job

    def _run(self, job: dict, work: Callable[[], dict]) -> None:
        def target():
            try:
                result = work()
                update = {"status": "ready", **result}
            except Exception as exc:  # a worker must always end its job
                update = {"status": "failed", "error": str(exc) or "The check failed."}
            with self._lock:
                job.update(update)
        threading.Thread(target=target, name=f"cleanup-{job['kind']}", daemon=True).start()

    # rules --------------------------------------------------------------------

    def _context(self, caller_pane: str | None) -> dict:
        view = self.view()
        agents = [a for a in view.get("agents", []) if a.get("present") and a.get("pane_id")]
        questions = set()
        for q in view.get("questions", []):
            questions.update(i for i in (q.get("identity"), q.get("source_identity"), q.get("about")) if i)
        unread_ids, unread_panes = set(), set()
        for a in view.get("agents", []):
            if a.get("event_id") and not a.get("acked"):
                unread_ids.add(a["identity"])
        for event in self.unread():
            unread_ids.add(event.get("identity"))
            unread_panes.add(event.get("pane_id"))
        workers: dict[str, int] = {}
        for a in agents:
            lead = (a.get("reports_to") or {}).get("identity")
            if lead and (a.get("status") in ("working", "blocked") or a.get("tier") or a.get("question")):
                workers[lead] = workers.get(lead, 0) + 1
        ended = {}
        for a in self.ended_agents():
            if a.get("pane_id") and not a.get("present"):
                ended.setdefault(a["pane_id"], a)
        labels = {w["id"]: w.get("label") or w["id"] for w in view.get("workspaces", [])}
        return {"by_pane": {a["pane_id"]: a for a in agents}, "questions": questions, "unread_ids": unread_ids,
                "unread_panes": unread_panes, "workers": workers, "ended": ended, "labels": labels,
                "caller": caller_pane, "watched": set(self.settings.get("watch.enabled_workspaces", []) or []),
                "allowed": set(self.allowed())}

    def _panes(self, workspace: str | None) -> list[dict]:
        panes = [p for p in self.reader.panes() if p.get("pane_id")]
        if workspace:
            panes = [p for p in panes if p.get("workspace_id") == workspace]
        return panes[:MAX_PANES]

    def _check(self, pane: dict, ctx: dict, git_cache: dict) -> dict:
        """One pane against every rule. Returns {candidate, kind, reason, ...}."""
        pane_id = pane["pane_id"]
        agent = ctx["by_pane"].get(pane_id)
        ended = ctx["ended"].get(pane_id)
        has_agent = bool(pane.get("agent") or pane.get("agent_session"))
        name = (agent or {}).get("name") or (ended or {}).get("name") or pane.get("terminal_title_stripped") or pane_id
        row = {"pane_id": pane_id, "workspace_id": pane.get("workspace_id"), "terminal_id": pane.get("terminal_id"),
               "identity": (agent or {}).get("identity") if has_agent else None, "name": name,
               "folder": short_path(pane.get("foreground_cwd") or pane.get("cwd"))}

        def no(reason, **fmt):
            return dict(row, candidate=False, kind=None, reason=REASONS[reason].format(**fmt))

        if pane_id == ctx["caller"]:
            return no("caller")
        if pane.get("focused"):
            return no("focused")
        status = pane.get("agent_status") or "unknown"
        if status == "working":
            return no("working")
        if status == "blocked":
            return no("blocked")
        identities = {i for i in ((agent or {}).get("identity"), (ended or {}).get("identity")) if i}
        if identities & ctx["questions"]:
            return no("question")
        if identities & ctx["unread_ids"] or pane_id in ctx["unread_panes"]:
            return no("unread")
        if has_agent and status in ("done", "idle"):
            if not agent:
                return no("untracked")
            if agent.get("tier"):
                return no("attention", tier=(agent.get("tier_label") or agent["tier"]).lower())
            if ctx["workers"].get(agent["identity"]):
                return no("coordinator", n=ctx["workers"][agent["identity"]])
            kind = status
        elif has_agent:
            kind = "ended"
        elif ended:
            kind = "shell"
        else:
            kind = None
        try:
            info = self.reader.process_info(pane_id)
        except HerdrError:
            return no("processes_unknown")
        state, proc = process_state(info, self.procs, agent=kind in ("done", "idle"))
        if state == "unknown":
            return no("processes_unknown")
        if state == "busy":
            return no("job" if kind in ("done", "idle") else "program", name=proc)
        if kind is None:
            return no("plain_shell")
        cwd = pane.get("foreground_cwd") or pane.get("cwd")
        if cwd not in git_cache:
            git_cache[cwd] = self.git(cwd)
        git, count = git_cache[cwd]
        if git == "dirty":
            return no("dirty", n=count)
        if git == "unknown":
            return no("git_unknown")
        reason = REASONS[kind].format(name=(ended or {}).get("name") or "an agent")
        return dict(row, candidate=True, kind=kind, reason=reason)

    def _evaluate(self, panes: list[dict], ctx: dict) -> tuple[list[dict], list[dict], list[dict]]:
        scoped, off = [], {}
        for pane in panes:
            ws = pane.get("workspace_id")
            key = "never" if ws in NEVER_WORKSPACES else "unwatched" if ws not in ctx["watched"] else \
                "off" if ws not in ctx["allowed"] else None
            if key:
                off.setdefault(ws, {"workspace_id": ws, "label": ctx["labels"].get(ws, ws), "panes": 0,
                                    "reason": REASONS[key]})["panes"] += 1
            else:
                scoped.append(pane)
        git_cache: dict = {}
        # Folders first, in parallel and bounded; then every rule in order.
        cwds = {p.get("foreground_cwd") or p.get("cwd") for p in scoped if not p.get("focused")
                and p.get("pane_id") != ctx["caller"] and p.get("agent_status") not in ("working", "blocked")}
        with ThreadPoolExecutor(max_workers=4) as pool:
            for cwd, state in zip(cwds, pool.map(self.git, cwds)):
                git_cache[cwd] = state
        rows = [self._check(p, ctx, git_cache) for p in scoped]
        for row in rows:
            row["workspace_label"] = ctx["labels"].get(row["workspace_id"], row["workspace_id"])
        return [r for r in rows if r["candidate"]], [r for r in rows if not r["candidate"]], list(off.values())

    def _preview(self, job: dict, workspace: str | None, caller_pane: str | None) -> dict:
        ctx = self._context(caller_pane)
        candidates, excluded, off = self._evaluate(self._panes(workspace), ctx)
        now = self.clock()
        with self._lock:
            self._previews[job["id"]] = {
                "workspace": workspace, "caller": caller_pane, "expires_at": now + PREVIEW_SECONDS, "used": False,
                # Same terminal, same agent (or still none): a pane that was replaced is never closed.
                "candidates": {r["pane_id"]: (r["terminal_id"], r["identity"]) for r in candidates}}
            for old in sorted(self._previews)[:-10]:
                self._previews.pop(old, None)
        return {"preview_id": job["id"], "expires_at": now + PREVIEW_SECONDS, "candidates": candidates,
                "excluded": excluded, "workspaces_off": off}

    def _confirm(self, job: dict, preview: dict, panes: list[str]) -> dict:
        ctx = self._context(preview["caller"])
        live = {p["pane_id"]: p for p in self._panes(preview["workspace"])}
        candidates, excluded, _ = self._evaluate([live[p] for p in panes if p in live], ctx)
        why = {r["pane_id"]: r["reason"] for r in excluded}
        closed, skipped, failed = [], [], []
        now = self.clock()
        for pane_id in panes:
            row = next((r for r in candidates if r["pane_id"] == pane_id), None)
            if pane_id not in live:
                skipped.append({"pane_id": pane_id, "reason": REASONS["gone"]})
            elif row is None:
                skipped.append({"pane_id": pane_id, "reason": why.get(pane_id) or REASONS["changed"]})
            elif (row["terminal_id"], row["identity"]) != preview["candidates"][pane_id]:
                skipped.append({"pane_id": pane_id, "reason": REASONS["changed"]})
            else:
                try:
                    if self.closer is None:
                        raise RuntimeError("Closing panes is not available here.")
                    self.closer.close(pane_id)
                    closed.append(row)
                    self._log(now, job, row, "closed")
                except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                    failed.append(dict(row, error=str(exc)[:200]))
                    self._log(now, job, row, "failed", str(exc)[:200])
                continue
            self._log(now, job, {"pane_id": pane_id, "workspace_id": (live.get(pane_id) or {}).get("workspace_id"),
                                 "name": None, "reason": skipped[-1]["reason"]}, "skipped")
        return {"preview_id": job.get("preview_id"), "closed": closed, "skipped": skipped, "failed": failed}

    def _log(self, at, job, row, result, error=None):
        self.store._exec("INSERT INTO cleanup_log (at, who, preview_id, pane_id, workspace_id, name, reason, result, error)"
                         " VALUES (?,?,?,?,?,?,?,?,?)",
                         (at, job.get("who"), job.get("preview_id"), row.get("pane_id"), row.get("workspace_id"),
                          row.get("name"), row.get("reason"), result, error))
