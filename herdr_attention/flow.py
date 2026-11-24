"""What each agent is waiting on, and the last action along each link of the graph.

Built from the agents' own traces (``activity.py``) and Herdr's statuses; no model calls.

Waiting states (``agent["waiting_for"]``), so a quiet agent is not read as stalled:

* ``shell``     a command has been running for a while, or (idle) a background task is still running
* ``scheduled`` a sleep is running, or (idle) the agent scheduled a wake-up for itself
* ``worker``    it waits on an agent with ``herdr agent wait`` / ``prompt --wait``, or it is an idle
                coordinator while one of its workers is busy

Last action of a link (``link["last_action"]``), between a worker and its coordinator:

* ``task``      the coordinator sent the worker a prompt
* ``report``    the worker sent the coordinator a prompt
* ``finished``  the worker's turn ended after the latest task
"""

from __future__ import annotations

from .sanitize import redact

SHELL_AFTER_SECONDS = 20      # a command running this long counts as waiting for its output
WAKEUP_GRACE_SECONDS = 120    # a wake-up that is a little late still counts
BOSS_KINDS = ("reports_to", "spawned_by")


def trace_activity(agent: dict, now: float, fresh_seconds: float) -> dict | None:
    """The agent's trace activity, only when the trace is read up to date."""
    trace = agent.get("trace") or {}
    if agent.get("session_collision") or not trace.get("available") or not trace.get("caught_up"):
        return None
    if trace.get("read_at") is None or now - trace["read_at"] > fresh_seconds:
        return None
    return trace.get("activity")


def quiet_reason(agent: dict, now: float, fresh_seconds: float) -> str | None:
    """Why a working agent's screen can stay the same without being stuck, or None."""
    running = (trace_activity(agent, now, fresh_seconds) or {}).get("running")
    if not running or running["kind"] not in ("shell", "sleep", "agent_wait"):
        return None
    return {"shell": "waiting for command output", "sleep": "sleeping",
            "agent_wait": "waiting for another agent"}[running["kind"]]


class Resolver:
    """Herdr command targets (pane id or agent name) to identities of present agents."""

    def __init__(self, agents: dict):
        self.by_key: dict[str, str] = {}
        for identity, agent in agents.items():
            if not agent.get("present"):
                continue
            for key in (agent.get("pane_id"), agent.get("name"), agent.get("terminal_id")):
                if key:
                    self.by_key.setdefault(str(key), identity)

    def __call__(self, target: str) -> str | None:
        return self.by_key.get(target)


def decorate(agents_out: list[dict], links: list[dict], agents: dict, now: float, *,
             fresh_seconds: float, patterns: list[str]) -> None:
    """Add ``waiting_for`` to each agent of the view and ``last_action`` / ``waiting`` to each link."""
    resolve = Resolver(agents)
    activity = {a["identity"]: trace_activity(agents.get(a["identity"]) or {}, now, fresh_seconds) or {}
                for a in agents_out}
    by_id = {a["identity"]: a for a in agents_out}
    sent: dict[tuple[str, str], dict] = {}           # (from, to) -> {verb: at}
    for identity, act in activity.items():
        for item in act.get("sent") or []:
            target = resolve(item["target"])
            if target and target != identity:
                slot = sent.setdefault((identity, target), {})
                slot[item["verb"]] = max(slot.get(item["verb"], 0), item["at"])

    workers: dict[str, list[str]] = {}
    for link in links:
        if link["kind"] in BOSS_KINDS and not link.get("hidden") and not link.get("from_hidden"):
            workers.setdefault(link["to"], []).append(link["from"])

    def clean(text: str) -> str:
        return redact(text or "", patterns)[0]

    def name_of(identity: str) -> str:
        a = by_id.get(identity) or {}
        return a.get("name") or a.get("title") or a.get("agent") or "agent"

    for agent in agents_out:
        agent["waiting_for"] = None
        # In-process subagents share their parent's transcript: what it waits on is the parent's.
        if not agent.get("present") or agent.get("in_process") or agent.get("tier") == "waiting_on_you":
            continue
        act, status = activity[agent["identity"]], agent.get("status")
        running = act.get("running")
        # Present Herdr panes only: a closed worker keeps its last status, and an in-process
        # subagent's "working" comes from hooks; both can be stale for days.
        busy = [w for w in workers.get(agent["identity"], [])
                if (by_id.get(w) or {}).get("status") == "working" and by_id[w].get("present") and not by_id[w].get("in_process")]
        found = None
        if status == "working" and running:
            age = now - running["since"]
            if running["kind"] == "agent_wait":
                targets = [t for t in (resolve(x) for x in running.get("targets") or []) if t]
                found = {"kind": "worker", "since": running["since"], "targets": targets,
                         "label": f"Waiting for {name_of(targets[0])}" if len(targets) == 1 else "Waiting for another agent"}
            elif running["kind"] == "sleep":
                found = {"kind": "scheduled", "since": running["since"], "due_at": running.get("due_at"),
                         "label": "Sleeping"}
            elif running["kind"] == "shell" and age >= SHELL_AFTER_SECONDS:
                found = {"kind": "shell", "since": running["since"], "label": f"Running: {clean(running['what'])}"}
        elif status in ("idle", "done"):
            wakeup, background = act.get("wakeup"), act.get("background")
            if busy:
                found = {"kind": "worker", "since": min((by_id[w].get("status_since") or now) for w in busy),
                         "targets": busy, "label": f"Waiting for {name_of(busy[0])}" if len(busy) == 1
                         else f"Waiting for {len(busy)} workers"}
            elif wakeup and wakeup["due_at"] > now - WAKEUP_GRACE_SECONDS:
                found = {"kind": "scheduled", "since": wakeup["set_at"], "due_at": wakeup["due_at"],
                         "label": f"Wake-up scheduled: {clean(wakeup['what'])}" if wakeup.get("what") else "Wake-up scheduled"}
            elif background and background["until"] > now:
                more = f" (+{background['count'] - 1} more)" if background["count"] > 1 else ""
                found = {"kind": "shell", "since": background["since"],
                         "label": f"Background task: {clean(background['what'])}{more}"}
        agent["waiting_for"] = found

    for link in links:
        link["last_action"] = None
        link["waiting"] = False
        if link["kind"] not in BOSS_KINDS or link.get("hidden") or link.get("from_hidden"):
            continue
        worker, boss = link["from"], link["to"]
        down, up = sent.get((boss, worker), {}), sent.get((worker, boss), {})
        options = []
        if down.get("prompt"):
            options.append({"kind": "task", "from": boss, "to": worker, "at": down["prompt"]})
        if up.get("prompt"):
            options.append({"kind": "report", "from": worker, "to": boss, "at": up["prompt"]})
        w = by_id.get(worker) or {}
        if (down.get("prompt") and w.get("status") in ("idle", "done") and w.get("since_known")
                and (w.get("status_since") or 0) > down["prompt"]):
            options.append({"kind": "finished", "from": worker, "to": boss, "at": w["status_since"]})
        if options:
            link["last_action"] = dict(max(options, key=lambda o: o["at"]), checked_at=down.get("read"))
        boss_wait = (by_id.get(boss) or {}).get("waiting_for") or {}
        link["waiting"] = boss_wait.get("kind") == "worker" and worker in (boss_wait.get("targets") or [])
