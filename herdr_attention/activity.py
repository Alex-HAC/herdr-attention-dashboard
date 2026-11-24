"""What an agent is busy with or waiting on, read from its own trace.

Fed one trace entry at a time by ``traces.ClaudeTrace`` / ``traces.CodexTrace``
(main thread only). It keeps:

* open tool calls: started, no result yet (a shell command, a sleep, a wait);
* background tasks (Claude ``run_in_background`` / ``Monitor``) until their
  task notification arrives;
* the latest wake-up a Claude agent scheduled for itself (``ScheduleWakeup``);
* the Herdr commands it ran against other agents (``herdr agent prompt`` and
  friends): which target, what kind, when.

Only short descriptions are kept: the agent's own description of a command,
or the program name and first argument. Never the full command or its output.
"""

from __future__ import annotations

import json
import re

SHELL_TOOLS = {"Bash", "BashOutput", "TaskOutput", "Monitor", "exec", "exec_command", "shell", "wait",
               "local_shell", "write_stdin", "unified_exec"}
SLEEP_COMMAND = re.compile(r"^\s*sleep\s+(\d+(?:\.\d+)?)\s*([smh]?)\s*$")
HERDR_COMMAND = re.compile(
    r"herdr\s+(?:--\S+\s+\S+\s+)*(agent|pane)\s+(prompt|send-text|run|read|wait|wait-output)\s+"
    r"((?:--?[a-z-]+(?:[ =]\d+)?\s+)*)[\"']?([A-Za-z0-9_:.@][A-Za-z0-9_:.@-]*)")
VERBS = {"prompt": "prompt", "send-text": "prompt", "run": "prompt", "read": "read", "wait": "wait", "wait-output": "wait"}
CODEX_CMD = re.compile(r"""cmd\s*:\s*(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)'|`([^`]*)`)""")
TASK_ID = re.compile(r"<task-id>([\w-]+)</task-id>")
TOOL_USE_ID = re.compile(r"<tool-use-id>([\w-]+)</tool-use-id>")
BACKGROUND_ID = re.compile(r"with ID: ([\w-]+)")
MAX_TARGETS = 40
BACKGROUND_DEFAULT_SECONDS = 3 * 3600


def short_command(command: str) -> str:
    """Program name and first argument of the first line: ``pytest -q``, never the rest."""
    line = (command or "").strip().splitlines()[0] if (command or "").strip() else ""
    line = re.sub(r"^(cd\s+\S+\s*(&&|;)\s*)+", "", line)
    words = line.split()
    return " ".join(words[:2])[:40]


def _sleep_seconds(command: str) -> float | None:
    m = SLEEP_COMMAND.match(command or "")
    if not m:
        return None
    return float(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600}[m.group(2)]


class Activity:
    def __init__(self):
        self.open: dict[str, dict] = {}
        self.background: dict[str, dict] = {}
        self.wakeup: dict | None = None
        self.sent: dict[tuple[str, str], dict] = {}

    # feeding -------------------------------------------------------------------

    def start(self, call_id: str, tool: str, at: float | None, args) -> None:
        """A tool call began. ``args`` is the tool input (dict) or a raw string (Codex exec code)."""
        if not call_id or at is None:
            return
        args = args if isinstance(args, (dict, str)) else {}
        if isinstance(args, str) and tool != "exec":
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        command = self._command_of(tool, args)
        call = {"tool": tool, "kind": "tool", "since": at, "what": ""}
        if tool == "ScheduleWakeup" and isinstance(args, dict):
            delay = _number(args.get("delaySeconds"))
            if delay:
                self.wakeup = {"due_at": at + delay, "set_at": at, "what": _text(args.get("reason"))}
            return
        if tool == "sleep" and isinstance(args, dict):        # Codex clock.sleep
            seconds = (_number(args.get("duration_ms")) or 0) / 1000
            call.update(kind="sleep", due_at=at + seconds if seconds else None, what="sleep")
        elif tool in SHELL_TOOLS:
            sleep = _sleep_seconds(command)
            targets = self._herdr(command, at, call_id)
            waits = [t for t, verb, waiting in targets if waiting]
            if waits:
                call.update(kind="agent_wait", targets=waits, what="herdr agent wait")
            elif sleep:
                call.update(kind="sleep", due_at=at + sleep, what="sleep")
            elif tool in ("wait", "write_stdin"):              # Codex: waiting on a running command
                call.update(kind="shell", what="command output")
            else:
                desc = _text(args.get("description")) if isinstance(args, dict) else ""
                call.update(kind="shell", what=desc or short_command(command) or tool)
            if isinstance(args, dict) and (args.get("run_in_background") or tool == "Monitor"):
                timeout = (_number(args.get("timeout_ms")) or _number(args.get("timeout")) or 0) / 1000
                self.background[call_id] = {"what": call["what"], "since": at, "task_id": None,
                                            "until": at + (timeout or BACKGROUND_DEFAULT_SECONDS)}
                return                                        # returns at once; the task runs on
        self.open[call_id] = call

    def finish(self, call_id: str, text: str = "") -> None:
        self.open.pop(call_id, None)
        task = self.background.get(call_id)
        if task and not task["task_id"]:
            m = BACKGROUND_ID.search(text or "")
            if m:
                task["task_id"] = m.group(1)
            elif "error" in (text or "").lower()[:80]:
                self.background.pop(call_id, None)           # never started

    def notification(self, text: str) -> None:
        """A Claude <task-notification>: that background task has ended."""
        use = TOOL_USE_ID.search(text or "")
        task = TASK_ID.search(text or "")
        for key, item in list(self.background.items()):
            if (use and key == use.group(1)) or (task and item["task_id"] == task.group(1)):
                del self.background[key]

    def stop_task(self, task_id: str) -> None:
        for key, item in list(self.background.items()):
            if item["task_id"] == task_id:
                del self.background[key]

    def turn_ended(self) -> None:
        """The turn is over: nothing it started can still be waiting for a result."""
        self.open.clear()

    def wakeup_fired(self) -> None:
        self.wakeup = None

    # reading -------------------------------------------------------------------

    def summary(self) -> dict:
        running = min(self.open.values(), key=lambda c: c["since"]) if self.open else None
        background = list(self.background.values())
        sent = sorted(self.sent.values(), key=lambda s: s["at"])[-MAX_TARGETS:]
        return {
            "running": dict(running) if running else None,
            "background": {"count": len(background), "what": background[-1]["what"],
                           "since": min(b["since"] for b in background),
                           "until": max(b["until"] for b in background)} if background else None,
            "wakeup": dict(self.wakeup) if self.wakeup else None,
            "sent": [{k: v for k, v in s.items() if k != "call"} | {"open": s["call"] in self.open} for s in sent],
        }

    # helpers -------------------------------------------------------------------

    @staticmethod
    def _command_of(tool: str, args) -> str:
        if isinstance(args, str):                            # Codex exec: JavaScript calling exec_command
            parts = [next(g for g in m.groups() if g is not None) for m in CODEX_CMD.finditer(args)]
            return "\n".join(p.encode().decode("unicode_escape", "ignore") if "\\" in p else p for p in parts) or args
        for key in ("command", "cmd"):
            value = args.get(key)
            if isinstance(value, list):
                return " ".join(str(v) for v in value)
            if isinstance(value, str):
                return value
        return ""

    def _herdr(self, command: str, at: float, call_id: str) -> list[tuple[str, str, bool]]:
        found = []
        for m in HERDR_COMMAND.finditer(command or ""):
            verb = VERBS[m.group(2)]
            target = m.group(4)
            rest = m.group(3) + command[m.end(): m.end() + 4000].split("\n", 1)[0]
            waiting = verb == "wait" or (verb == "prompt" and "--wait" in rest)
            self.sent[(target, verb)] = {"target": target, "verb": verb, "at": at, "call": call_id}
            found.append((target, verb, waiting))
        while len(self.sent) > MAX_TARGETS * 3:
            del self.sent[min(self.sent, key=lambda k: self.sent[k]["at"])]
        return found


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _text(value, limit: int = 80) -> str:
    if not isinstance(value, str):
        return ""
    line = value.strip().splitlines()[0] if value.strip() else ""
    return line[:limit]
