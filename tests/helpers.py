"""Test doubles: a clock you can move, a scripted Herdr, a keyring and a sender."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from herdr_attention.config import Settings
from herdr_attention.herdr import HerdrError

FIXTURES = Path(__file__).parent / "fixtures"


class Clock:
    def __init__(self, start: float = 1_800_000_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


_seq = [1000]


def agent(pane_id: str = "w1:p1", status: str = "idle", workspace: str | None = None, session: str | None = "s1",
          name: str | None = "tester", kind: str = "claude", seq: int | None = None, title: str = "Task title",
          terminal_id: str | None = None) -> dict:
    """An agent record shaped like Herdr's agent.list / agent.get output."""
    if seq is None:
        _seq[0] += 1
        seq = _seq[0]
    workspace = workspace or pane_id.split(":")[0]
    info = {
        "agent": kind, "agent_status": status, "name": name, "pane_id": pane_id,
        "workspace_id": workspace, "tab_id": f"{workspace}:t1", "terminal_id": terminal_id or f"term_{pane_id}",
        "terminal_title": title, "terminal_title_stripped": title, "cwd": "/tmp/x", "foreground_cwd": "/tmp/x",
        "focused": False, "interactive_ready": True, "revision": 2, "state_change_seq": seq,
    }
    if session:
        info["agent_session"] = {"source": f"herdr:{kind}", "agent": kind, "kind": "id", "value": session}
    return info


def workspaces(*ids: str) -> list[dict]:
    return [{"workspace_id": w, "label": f"label-{w}", "number": i + 1} for i, w in enumerate(ids)]


def settings(**sections) -> Settings:
    base = {
        "watch": {"enabled_workspaces": ["w1"]},
        # Isolated by default so tests never read the machine's live records.
        "quotas": {"usage_dir": str(FIXTURES / "quotas" / "absent")},
        # The 0.9.8.7 finish guard is off by default here; tests/test_finish_alerts.py checks it.
        # The 0.9.8.9 rate limit and default quiet hours too (tests/test_phone_prefs.py checks them),
        # so a test never depends on the time of day it runs at.
        "notify": {"finish_settle_seconds": 0, "finish_repeat_minutes": 0, "rate_count": 0},
        "notify_tiers": {"quiet_hours": False},
    }
    for key, value in sections.items():
        base.setdefault(key, {}).update(value)
    return Settings(base)


class FakeHerdr:
    """Answers the read-only client calls from a dict of current agents."""

    def __init__(self, agents: list[dict] | None = None, ws: list[dict] | None = None):
        self.agents_by_pane = {a["pane_id"]: copy.deepcopy(a) for a in agents or []}
        self.ws = ws or workspaces("w1", "w2")
        self.explain_rules: dict[str, str] = {}
        self.outputs: dict[str, str] = {}
        self.screens: dict[str, str] = {}     # visible screen, when it differs from recent output
        self.busy: set[str] = set()           # panes where "recent" is refused, like Herdr 0.9.1
        self.broken: set[str] = set()         # panes where every read fails
        self.calls: list[tuple] = []
        self.shell_panes: list[dict] = []
        self.process_infos: dict[str, dict | Exception] = {}  # pane -> pane.process_info result

    def set(self, info: dict) -> None:
        self.agents_by_pane[info["pane_id"]] = copy.deepcopy(info)

    def remove(self, pane_id: str) -> None:
        self.agents_by_pane.pop(pane_id, None)

    def ping(self) -> dict:
        return {"type": "pong", "version": "0.9.1", "protocol": 22}

    def agents(self) -> list[dict]:
        self.calls.append(("agents",))
        return [copy.deepcopy(a) for a in self.agents_by_pane.values()]

    def agent(self, target: str) -> dict | None:
        self.calls.append(("agent", target))
        found = self.agents_by_pane.get(target)
        return copy.deepcopy(found) if found else None

    def workspaces(self) -> list[dict]:
        return copy.deepcopy(self.ws)

    def tabs(self) -> list[dict]:
        seen = {}
        for info in self.agents_by_pane.values():
            tab = info.get("tab_id")
            if tab and tab not in seen:
                seen[tab] = {"tab_id": tab, "workspace_id": info["workspace_id"], "label": f"tab {tab}", "number": len(seen) + 1}
        return list(seen.values())

    def panes(self):
        return [copy.deepcopy(a) for a in self.agents_by_pane.values()] + copy.deepcopy(self.shell_panes)

    def process_info(self, pane_id: str) -> dict:
        """Read-only process info. Default: an agent (or the shell) in the foreground, no children."""
        self.calls.append(("process_info", pane_id))
        info = self.process_infos.get(pane_id)
        if isinstance(info, Exception):
            raise info
        if info is None:
            shell = 1000 + len(self.calls)
            agent = pane_id in self.agents_by_pane
            info = {"pane_id": pane_id, "shell_pid": shell, "foreground_process_group_id": shell + 1 if agent else shell,
                    "foreground_processes": [{"pid": shell + 1 if agent else shell, "name": "claude" if agent else "bash"}]}
        return copy.deepcopy(info)

    def explain(self, target: str) -> dict:
        self.calls.append(("explain", target))
        rule = self.explain_rules.get(target)
        if rule is None:
            raise HerdrError("explain unavailable")
        return {"state": "blocked", "matched_rule": {"id": rule, "state": "blocked"}}

    def read_output(self, target: str, lines: int = 40, source: str = "recent") -> str:
        self.calls.append(("read", target, source))
        if target in self.broken:
            raise HerdrError("agent.read: io: failed", code="io")
        if source == "recent" and target in self.busy:
            raise HerdrError("agent.read: agent_not_idle: use --source visible", code="agent_not_idle")
        if source == "visible":
            return self.screens.get(target, self.outputs.get(target, ""))
        return self.outputs.get(target, "")

    def read_ansi(self, target: str, lines: int = 200) -> str:
        self.calls.append(("read_ansi", target, "visible"))
        if target in self.broken:
            raise HerdrError("agent.read: io: failed", code="io")
        return self.screens.get(target, self.outputs.get(target, ""))

    def read_screen(self, target: str, lines: int = 40) -> tuple[str, str]:
        try:
            return self.read_output(target, lines, "recent"), "recent"
        except HerdrError as exc:
            if exc.code != "agent_not_idle":
                raise
        return self.read_output(target, lines, "visible"), "visible"


class FakeKeyring:
    def __init__(self, values: dict | None = None):
        self.values = dict(values or {})
        self.service = "test"

    def get(self, key: str):
        return self.values.get(key)

    def has(self, key: str) -> bool:
        return key in self.values

    def set(self, key: str, value: str) -> None:
        self.values[key] = value


class RecordingSender:
    name = "fake"

    def __init__(self, ready: bool = True, fail: bool = False):
        self._ready = ready
        self.fail = fail
        self.sent: list[dict] = []

    def ready(self):
        return (self._ready, "ready" if self._ready else "not configured")

    def send(self, **message):
        if self.fail:
            raise RuntimeError("server down")
        self.sent.append(message)


def load_fixture(name: str) -> list[dict]:
    with open(FIXTURES / name) as fh:
        return [json.loads(line) for line in fh if line.strip()]
