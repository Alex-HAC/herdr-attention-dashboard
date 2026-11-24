#!/usr/bin/env python3
"""Turn a live Herdr recording into a shareable test fixture.

Keeps what the tests need (pane/workspace ids, statuses, sequence numbers,
event order and timing) and replaces everything that describes your work:
agent names, terminal titles, paths, workspace labels, session ids.

Usage: anonymize_fixture.py OUT.jsonl RECORDING.jsonl [RECORDING.jsonl ...]

Accepts the `herdr-attention record` format and the phase-0 probe format.
"""

from __future__ import annotations

import json
import sys
import uuid

NAMESPACE = uuid.UUID("6f1c2a52-6d2e-4f0e-9c55-1b0c7c3f0a11")


class Scrubber:
    def __init__(self):
        self.names: dict[str, str] = {}

    def name(self, value):
        if not value:
            return value
        return self.names.setdefault(value, f"agent-{len(self.names) + 1}")

    def agent(self, info: dict) -> dict:
        info = dict(info)
        pane = info.get("pane_id", "?")
        info["name"] = self.name(info.get("name"))
        for key in ("terminal_title", "terminal_title_stripped"):
            if info.get(key):
                info[key] = f"task in {pane}"
        for key in ("cwd", "foreground_cwd"):
            if info.get(key):
                info[key] = "/work/project"
        session = info.get("agent_session")
        if session and session.get("value"):
            info["agent_session"] = {**session, "value": str(uuid.uuid5(NAMESPACE, session["value"]))}
        return info

    def workspace(self, ws: dict) -> dict:
        return {**ws, "label": f"workspace {ws.get('workspace_id')}"}

    def envelope(self, env: dict) -> dict:
        data = dict(env.get("data") or {})
        if data.get("title"):
            data["title"] = "scrubbed"
        return {**env, "data": data}


def read(path: str):
    with open(path) as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def main(out: str, inputs: list[str]) -> int:
    s = Scrubber()
    rows = []
    snapshot_written = False
    for path in inputs:
        for row in read(path):
            if row.get("kind") == "snapshot" or row.get("_meta") == "initial_agents":
                if snapshot_written:
                    continue  # one starting snapshot per fixture
                rows.append({"kind": "snapshot", "at": row["at"],
                             "agents": [s.agent(a) for a in row["agents"]],
                             "workspaces": [s.workspace(w) for w in row.get("workspaces", [])]})
                snapshot_written = True
            elif row.get("kind") == "event":
                rows.append({"kind": "event", "at": row["at"], "envelope": s.envelope(row["envelope"])})
            elif "msg" in row and row["msg"].get("event"):
                rows.append({"kind": "event", "at": row["at"], "envelope": s.envelope(row["msg"])})
    snapshot = [r for r in rows if r["kind"] == "snapshot"]
    events = sorted((r for r in rows if r["kind"] == "event"), key=lambda r: r["at"])
    with open(out, "w") as fh:
        for row in snapshot + events:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    print(f"wrote {len(events)} events to {out}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1], sys.argv[2:]))
