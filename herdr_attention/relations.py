"""Declared relationships between agents: an append-only registry.

Herdr has no parent, reports-to or role fields, and pane metadata tokens are
pane-scoped hints (they survive an agent leaving the pane), so this registry
in the app's own database is the authority.

Everything is keyed by agent identity (Herdr session id), never pane id.
Every change is a row in ``relation_events``; the current state is the
replay of those rows, so history, handovers and revocations stay visible.

Kinds and where they may come from:

* ``reports_to``: who an agent reports to. Only ``registered`` (launch helper)
  or ``manual`` (you, in the CLI or UI) in this registry. A separate passive
  inference store supplies fallback edges without changing declarations.
* ``spawned_by``: who started it. ``registered`` (the helper knew the caller)
  or ``observed-spawn`` (a trace records it). Creation, not supervision: it
  never implies ``reports_to`` or a role.
* roles (``coordinator``, ``worker``, ...), ``team`` and ``task`` labels:
  declared only.

Policies: self-links are refused; cycles are allowed but flagged; several
coordinators per agent are allowed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

PROVENANCE = ("registered", "manual", "observed-spawn")
LINK_KINDS = ("reports_to", "spawned_by")
LABELS = ("role", "team", "task", "sandbox")
DECLARED_ONLY = ("registered", "manual")
_LABEL_VALUE = re.compile(r"^[^\x00-\x1f\x7f]{1,80}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS relation_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    at         REAL NOT NULL,
    action     TEXT NOT NULL,      -- link | unlink | set | clear
    subject    TEXT NOT NULL,      -- agent identity
    kind       TEXT NOT NULL,      -- reports_to | spawned_by | role | team | task
    object     TEXT,               -- other agent identity (links)
    value      TEXT,               -- label value (role/team/task)
    provenance TEXT NOT NULL,      -- registered | manual | observed-spawn
    actor      TEXT NOT NULL,      -- cli | ui | helper | trace
    note       TEXT
);
CREATE INDEX IF NOT EXISTS relation_events_subject ON relation_events (subject);
CREATE INDEX IF NOT EXISTS relation_events_object ON relation_events (object);
"""


class RelationError(ValueError):
    pass


@dataclass
class State:
    links: dict = field(default_factory=dict)    # (subject, kind, object) -> {since, provenance, actor}
    labels: dict = field(default_factory=dict)   # (subject, kind) -> {value -> {since, provenance, actor}}


class Registry:
    def __init__(self, store):
        self.store = store
        with store._lock:
            store._db.executescript(SCHEMA)

    # writing ------------------------------------------------------------------

    def _append(self, *, at: float, action: str, subject: str, kind: str, object: str | None = None,
                value: str | None = None, provenance: str, actor: str, note: str | None = None) -> int:
        cur = self.store._exec(
            "INSERT INTO relation_events (at, action, subject, kind, object, value, provenance, actor, note)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (at, action, subject, kind, object, value, provenance, actor, note))
        return int(cur.lastrowid)

    def link(self, subject: str, kind: str, obj: str, *, at: float, provenance: str, actor: str,
             note: str | None = None) -> int | None:
        """Add a link. Returns the event id, or None if it already exists."""
        if kind not in LINK_KINDS:
            raise RelationError(f"unknown link kind {kind!r}")
        if provenance not in PROVENANCE:
            raise RelationError(f"unknown provenance {provenance!r}")
        if kind == "reports_to" and provenance not in DECLARED_ONLY:
            raise RelationError("reports_to can only be declared (registered or manual), never observed")
        if not subject or not obj:
            raise RelationError("both agents are needed")
        if subject == obj:
            raise RelationError("an agent cannot be linked to itself")
        if (subject, kind, obj) in self.state().links:
            return None
        return self._append(at=at, action="link", subject=subject, kind=kind, object=obj,
                            provenance=provenance, actor=actor, note=note)

    def unlink(self, subject: str, kind: str, obj: str, *, at: float, provenance: str, actor: str,
               note: str | None = None) -> int:
        if (subject, kind, obj) not in self.state().links:
            raise RelationError("no such link")
        return self._append(at=at, action="unlink", subject=subject, kind=kind, object=obj,
                            provenance=provenance, actor=actor, note=note)

    def handover(self, subject: str, new_coordinator: str, *, at: float, provenance: str, actor: str) -> list[int]:
        """Replace every reports_to of ``subject`` with one to ``new_coordinator``."""
        if subject == new_coordinator:
            raise RelationError("an agent cannot report to itself")
        ids = []
        old = [o for (s, k, o) in self.state().links if s == subject and k == "reports_to" and o != new_coordinator]
        for o in old:
            ids.append(self.unlink(subject, "reports_to", o, at=at, provenance=provenance, actor=actor,
                                   note=f"handover to {new_coordinator}"))
        added = self.link(subject, "reports_to", new_coordinator, at=at, provenance=provenance, actor=actor,
                          note="handover" if old else None)
        if added:
            ids.append(added)
        return ids

    def set_label(self, subject: str, kind: str, value: str, *, at: float, provenance: str, actor: str) -> int | None:
        if kind not in LABELS:
            raise RelationError(f"unknown label {kind!r}")
        if provenance not in DECLARED_ONLY:
            raise RelationError("roles, teams and tasks are declared, never inferred")
        value = (value or "").strip()
        if not _LABEL_VALUE.match(value):
            raise RelationError(f"{kind} must be 1-80 printable characters")
        current = self.state().labels.get((subject, kind), {})
        if value in current:
            return None
        # A team and a task are single-valued (a new one replaces the old); roles add up.
        if kind in ("team", "task"):
            for old in list(current):
                self._append(at=at, action="clear", subject=subject, kind=kind, value=old,
                             provenance=provenance, actor=actor, note=f"replaced by {value}")
        return self._append(at=at, action="set", subject=subject, kind=kind, value=value,
                            provenance=provenance, actor=actor)

    def clear_label(self, subject: str, kind: str, value: str | None, *, at: float, provenance: str, actor: str) -> list[int]:
        current = self.state().labels.get((subject, kind), {})
        targets = [value] if value else list(current)
        if value and value not in current:
            raise RelationError(f"{subject} has no {kind} {value!r}")
        return [self._append(at=at, action="clear", subject=subject, kind=kind, value=v, provenance=provenance,
                             actor=actor, note="revoked") for v in targets]

    def rename(self, old: str, new: str) -> None:
        """Herdr learned the session id of an agent first seen without one."""
        self.store._exec("UPDATE relation_events SET subject = ? WHERE subject = ?", (new, old))
        self.store._exec("UPDATE relation_events SET object = ? WHERE object = ?", (new, old))

    # reading ------------------------------------------------------------------

    def events(self, identity: str | None = None, limit: int = 200) -> list[dict]:
        sql = "SELECT id, at, action, subject, kind, object, value, provenance, actor, note FROM relation_events"
        args: tuple = ()
        if identity:
            sql += " WHERE subject = ? OR object = ?"
            args = (identity, identity)
        rows = self.store._exec(sql + " ORDER BY id DESC LIMIT ?", args + (limit,)).fetchall()
        keys = ("id", "at", "action", "subject", "kind", "object", "value", "provenance", "actor", "note")
        return [dict(zip(keys, row)) for row in rows]

    def state(self) -> State:
        state = State()
        rows = self.store._exec("SELECT at, action, subject, kind, object, value, provenance, actor "
                                "FROM relation_events ORDER BY id").fetchall()
        for at, action, subject, kind, obj, value, provenance, actor in rows:
            info = {"since": at, "provenance": provenance, "actor": actor}
            if kind in LINK_KINDS:
                key = (subject, kind, obj)
                if action == "link":
                    state.links[key] = info
                elif action == "unlink":
                    state.links.pop(key, None)
            else:
                labels = state.labels.setdefault((subject, kind), {})
                if action == "set":
                    labels[value] = info
                elif action == "clear":
                    labels.pop(value, None)
        return state

    def labels_for(self, identity: str, state: State | None = None) -> dict:
        state = state or self.state()
        out = {}
        for kind in LABELS:
            values = state.labels.get((identity, kind), {})
            out[kind] = [{"value": v, **info} for v, info in sorted(values.items(), key=lambda kv: kv[1]["since"])]
        return out


def cycles(edges: list[tuple[str, str]]) -> set[tuple[str, str]]:
    """Edges that are part of a cycle (allowed, but shown with a warning)."""
    graph: dict[str, set[str]] = {}
    for a, b in edges:
        graph.setdefault(a, set()).add(b)

    def reaches(start: str, goal: str) -> bool:
        seen, todo = set(), [start]
        while todo:
            node = todo.pop()
            if node == goal:
                return True
            if node in seen:
                continue
            seen.add(node)
            todo.extend(graph.get(node, ()))
        return False

    return {(a, b) for a, b in edges if reaches(b, a)}


def dumps(value) -> str:
    return json.dumps(value, sort_keys=True)
