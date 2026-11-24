"""SQLite persistence: agent identities, event history, settings, sessions.

Output text is never written here. Events hold metadata and evidence only.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS agents (
    identity   TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    at           REAL NOT NULL,
    identity     TEXT NOT NULL,
    workspace_id TEXT,
    pane_id      TEXT,
    type         TEXT NOT NULL,
    evidence     TEXT NOT NULL,
    confidence   TEXT NOT NULL,
    seen_late    INTEGER NOT NULL DEFAULT 0,
    resolved_at  REAL,
    acked_at     REAL,
    notify_state TEXT NOT NULL DEFAULT 'pending',
    notified_at  REAL,
    notify_error TEXT
);
CREATE INDEX IF NOT EXISTS events_open ON events (resolved_at, acked_at);
CREATE INDEX IF NOT EXISTS events_identity ON events (identity, type);
CREATE TABLE IF NOT EXISTS question_answers (question_id TEXT PRIMARY KEY, at REAL NOT NULL, reply_id INTEGER);
-- v0.9.6: Alex's answer per question item (asks.py); kept over a restart.
CREATE TABLE IF NOT EXISTS ask_answers (
    item_id  TEXT PRIMARY KEY,
    identity TEXT NOT NULL,
    text     TEXT NOT NULL,
    at       REAL NOT NULL,
    reply_id INTEGER
);
CREATE TABLE IF NOT EXISTS notification_text (
    event_id INTEGER PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL,
    source TEXT NOT NULL, at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    csrf       TEXT NOT NULL,
    expires_at REAL NOT NULL
);
-- What each agent did, as the collector saw it: status and title changes.
-- `live` = 1 when watched as it happened, 0 when found later (time unknown).
CREATE TABLE IF NOT EXISTS activity (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    at       REAL NOT NULL,
    identity TEXT NOT NULL,
    kind     TEXT NOT NULL,
    status   TEXT,
    title    TEXT,
    live     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS activity_identity ON activity (identity, at);
CREATE TABLE IF NOT EXISTS notifications (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    at   REAL NOT NULL,
    kind TEXT NOT NULL,
    ok   INTEGER NOT NULL
);
-- v0.9.9: seen/unseen for the global visuals hub, shared by phone and desktop.
CREATE TABLE IF NOT EXISTS visuals_seen (
    media_id TEXT PRIMARY KEY,
    seen_at  REAL NOT NULL
);
"""

EVENT_COLUMNS = (
    "id", "at", "identity", "workspace_id", "pane_id", "type", "evidence", "confidence",
    "seen_late", "resolved_at", "acked_at", "notify_state", "notified_at", "notify_error", "title",
)


class Rows:
    """A fully fetched result with the parts of the sqlite3.Cursor API callers use."""

    __slots__ = ("_rows", "_pos", "rowcount", "lastrowid", "description")

    def __init__(self, rows: list, rowcount: int, lastrowid: int | None, description):
        self._rows, self._pos = rows, 0
        self.rowcount, self.lastrowid, self.description = rowcount, lastrowid, description

    def fetchone(self):
        if self._pos >= len(self._rows):
            return None
        self._pos += 1
        return self._rows[self._pos - 1]

    def fetchall(self) -> list:
        rest, self._pos = self._rows[self._pos:], len(self._rows)
        return rest

    def fetchmany(self, size: int = 1) -> list:
        part = self._rows[self._pos:self._pos + size]
        self._pos += len(part)
        return part

    def __iter__(self):
        while (row := self.fetchone()) is not None:
            yield row


class Store:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL" if self.path != ":memory:" else "PRAGMA journal_mode=MEMORY")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._lock = threading.RLock()
        with self._lock:
            self._db.executescript(SCHEMA)
            self._migrate()
            self._db.execute("INSERT OR IGNORE INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))

    def _migrate(self) -> None:
        """Bring an older database up to date (event title, visuals seen)."""
        columns = {row[1] for row in self._db.execute("PRAGMA table_info(events)")}
        if "title" not in columns:
            self._db.execute("ALTER TABLE events ADD COLUMN title TEXT")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS visuals_seen (media_id TEXT PRIMARY KEY, seen_at REAL NOT NULL)")
        self._db.execute("INSERT INTO meta VALUES ('schema_version', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                         (str(SCHEMA_VERSION),))

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _exec(self, sql: str, args: Iterable[Any] = ()) -> "Rows":
        # The connection is shared by every thread. Rows are stepped inside the
        # lock: a cursor fetched later could interleave with another thread's
        # statement and return empty or mixed rows.
        with self._lock:
            cur = self._db.execute(sql, tuple(args))
            return Rows(cur.fetchall(), cur.rowcount, cur.lastrowid, cur.description)

    # meta -----------------------------------------------------------------

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self._exec("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key: str, value: str) -> None:
        self._exec("INSERT INTO meta VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

    # visuals seen (v0.9.9) -------------------------------------------------

    def visuals_seen_state(self) -> tuple[set[str], float]:
        """Known seen media ids and the 'mark all' watermark (items at or before it are seen)."""
        ids = {row[0] for row in self._exec("SELECT media_id FROM visuals_seen").fetchall()}
        try:
            watermark = float(self.get_meta("visuals_seen_all_at", "0") or 0)
        except (TypeError, ValueError):
            watermark = 0.0
        return ids, watermark

    def mark_visuals_seen(self, media_ids: list[str], at: float) -> int:
        """Record these media ids as seen. Returns how many valid ids were stored."""
        count = 0
        for media_id in media_ids:
            if not isinstance(media_id, str) or len(media_id) != 32 or any(c not in "0123456789abcdef" for c in media_id):
                continue
            self._exec(
                "INSERT INTO visuals_seen (media_id, seen_at) VALUES (?, ?) "
                "ON CONFLICT(media_id) DO UPDATE SET seen_at = excluded.seen_at",
                (media_id, at))
            count += 1
        return count

    def mark_visuals_seen_all(self, at: float, media_ids: list[str] | None = None) -> int:
        """Everything at or before ``at`` is seen; current ids are recorded too."""
        self.set_meta("visuals_seen_all_at", str(at))
        return self.mark_visuals_seen(list(media_ids or []), at)

    # agents -----------------------------------------------------------------

    def load_agents(self) -> dict[str, dict]:
        rows = self._exec("SELECT identity, data FROM agents").fetchall()
        return {identity: json.loads(data) for identity, data in rows}

    def save_agent(self, identity: str, data: dict, at: float) -> None:
        self._exec(
            "INSERT INTO agents VALUES (?, ?, ?) ON CONFLICT(identity) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
            (identity, json.dumps(data, sort_keys=True), at),
        )

    def rename_identity(self, old: str, new: str) -> None:
        with self._lock:
            self._db.execute("UPDATE events SET identity = ? WHERE identity = ?", (new, old))
            self._db.execute("UPDATE activity SET identity = ? WHERE identity = ?", (new, old))
            try:
                self._db.execute("UPDATE relation_events SET subject = ? WHERE subject = ?", (new, old))
                self._db.execute("UPDATE relation_events SET object = ? WHERE object = ?", (new, old))
            except sqlite3.OperationalError:
                pass  # no relationship registry in this database
            try:
                self._db.execute("UPDATE replies SET identity = ? WHERE identity = ?", (new, old))
            except sqlite3.OperationalError:
                pass
            try:
                self._db.execute("UPDATE OR IGNORE inferred_links SET subject = ? WHERE subject = ?", (new, old))
                self._db.execute("UPDATE inferred_links SET object = ? WHERE object = ?", (new, old))
                self._db.execute("UPDATE OR IGNORE inference_suppressions SET subject = ? WHERE subject = ?", (new, old))
            except sqlite3.OperationalError:
                pass  # inference tables are optional for standalone Store users
            self._db.execute("DELETE FROM agents WHERE identity = ?", (old,))

    def delete_agents_before(self, cutoff: float) -> int:
        return self._exec("DELETE FROM agents WHERE updated_at < ?", (cutoff,)).rowcount

    # events -----------------------------------------------------------------

    def add_event(self, *, at: float, identity: str, workspace_id: str | None, pane_id: str | None,
                  type: str, evidence: dict, confidence: str, seen_late: bool, notify_state: str,
                  title: str | None = None) -> int:
        cur = self._exec(
            "INSERT INTO events (at, identity, workspace_id, pane_id, type, evidence, confidence, seen_late, notify_state, title)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (at, identity, workspace_id, pane_id, type, json.dumps(evidence, sort_keys=True), confidence,
             int(seen_late), notify_state, title),
        )
        return int(cur.lastrowid)

    def _rows(self, sql: str, args: Iterable[Any] = ()) -> list[dict]:
        rows = self._exec(sql, args).fetchall()
        out = []
        for row in rows:
            item = dict(zip(EVENT_COLUMNS, row))
            item["evidence"] = json.loads(item["evidence"]) if item["evidence"] else {}
            item["seen_late"] = bool(item["seen_late"])
            out.append(item)
        return out

    def event(self, event_id: int) -> dict | None:
        rows = self._rows(f"SELECT {', '.join(EVENT_COLUMNS)} FROM events WHERE id = ?", (event_id,))
        return rows[0] if rows else None

    def open_events(self, identity: str | None = None) -> list[dict]:
        sql = f"SELECT {', '.join(EVENT_COLUMNS)} FROM events WHERE resolved_at IS NULL"
        args: tuple = ()
        if identity is not None:
            sql += " AND identity = ?"
            args = (identity,)
        return self._rows(sql + " ORDER BY at", args)

    def recent_events(self, limit: int = 100, workspaces: Iterable[str] | None = None) -> list[dict]:
        sql = f"SELECT {', '.join(EVENT_COLUMNS)} FROM events"
        args: list[Any] = []
        if workspaces is not None:
            ws = list(workspaces)
            if not ws:
                return []
            sql += f" WHERE workspace_id IN ({', '.join('?' * len(ws))})"
            args.extend(ws)
        sql += " ORDER BY at DESC, id DESC LIMIT ?"
        args.append(limit)
        return self._rows(sql, args)

    def events_by_notify_state(self, states: Iterable[str]) -> list[dict]:
        states = list(states)
        return self._rows(
            f"SELECT {', '.join(EVENT_COLUMNS)} FROM events WHERE notify_state IN ({', '.join('?' * len(states))}) ORDER BY at",
            states,
        )

    def save_notification_text(self, event_ids, title, body, source, at):
        for event_id in event_ids:
            self._exec("INSERT OR IGNORE INTO notification_text VALUES (?, ?, ?, ?, ?)",
                       (event_id, title, body, source, at))

    def notification_text(self, event_id):
        row = self._exec("SELECT title, body, source FROM notification_text WHERE event_id=?", (event_id,)).fetchone()
        return dict(zip(("title", "body", "source"), row)) if row else None

    def notification_events(self, states: Iterable[str], workspaces: Iterable[str], limit: int = 50,
                            unread_only: bool = False) -> list[dict]:
        """Events that were notifications (by notify_state), newest first."""
        states, ws = list(states), list(workspaces)
        if not states or not ws:
            return []
        sql = (f"SELECT {', '.join(EVENT_COLUMNS)} FROM events WHERE notify_state IN ({', '.join('?' * len(states))})"
               f" AND workspace_id IN ({', '.join('?' * len(ws))})")
        if unread_only:
            sql += " AND acked_at IS NULL"
        return self._rows(sql + " ORDER BY at DESC, id DESC LIMIT ?", [*states, *ws, limit])

    def set_final_tier(self, event_id: int, tier: str) -> bool:
        """Store the tier an event was sent with, once (evidence.final_tier)."""
        event = self.event(event_id)
        if not event or event["evidence"].get("final_tier"):
            return False
        evidence = dict(event["evidence"], final_tier=tier)
        self._exec("UPDATE events SET evidence = ? WHERE id = ?", (json.dumps(evidence, sort_keys=True), event_id))
        return True

    def resolve_event(self, event_id: int, at: float) -> None:
        self._exec("UPDATE events SET resolved_at = ? WHERE id = ? AND resolved_at IS NULL", (at, event_id))

    def ack_event(self, event_id: int, at: float) -> bool:
        return self._exec("UPDATE events SET acked_at = ? WHERE id = ? AND acked_at IS NULL", (at, event_id)).rowcount > 0

    def set_notify_state(self, event_ids: Iterable[int], state: str, at: float | None = None,
                         error: str | None = None) -> None:
        for event_id in event_ids:
            self._exec(
                "UPDATE events SET notify_state = ?, notified_at = COALESCE(?, notified_at), notify_error = ? WHERE id = ?",
                (state, at, error, event_id),
            )

    def events_for(self, identity: str, limit: int = 20) -> list[dict]:
        return self._rows(f"SELECT {', '.join(EVENT_COLUMNS)} FROM events WHERE identity = ? ORDER BY at DESC, id DESC LIMIT ?",
                          (identity, limit))

    def latest_event(self, identity: str, type: str) -> dict | None:
        rows = self._rows(f"SELECT {', '.join(EVENT_COLUMNS)} FROM events WHERE identity = ? AND type = ? "
                          "ORDER BY at DESC, id DESC LIMIT 1", (identity, type))
        return rows[0] if rows else None

    # activity -----------------------------------------------------------------

    def add_activity(self, *, at: float, identity: str, kind: str, status: str | None, title: str | None,
                     live: bool) -> None:
        self._exec("INSERT INTO activity (at, identity, kind, status, title, live) VALUES (?, ?, ?, ?, ?, ?)",
                   (at, identity, kind, status, title, int(live)))

    def activity(self, identity: str, limit: int = 50) -> list[dict]:
        rows = self._exec("SELECT at, kind, status, title, live FROM activity WHERE identity = ? "
                          "ORDER BY at DESC, id DESC LIMIT ?", (identity, limit)).fetchall()
        return [{"at": r[0], "kind": r[1], "status": r[2], "title": r[3], "live": bool(r[4])} for r in rows]

    def prune_activity(self, cutoff: float) -> int:
        return self._exec("DELETE FROM activity WHERE at < ?", (cutoff,)).rowcount

    def prune_events(self, cutoff: float) -> int:
        return self._exec("DELETE FROM events WHERE at < ? AND (resolved_at IS NOT NULL OR acked_at IS NOT NULL)",
                          (cutoff,)).rowcount

    # notifications log (counts only, for rate limiting) -----------------------

    def log_notification(self, at: float, kind: str, ok: bool) -> None:
        self._exec("INSERT INTO notifications (at, kind, ok) VALUES (?, ?, ?)", (at, kind, int(ok)))

    def notifications_since(self, since: float) -> int:
        # Phone/desktop pushes only: dashboard listings never push, so they do not use up the
        # rate limit (before 0.9.8.9 they did).
        return self._exec("SELECT COUNT(*) FROM notifications WHERE at >= ? AND ok = 1 AND kind <> 'dashboard'",
                          (since,)).fetchone()[0]

    # settings -----------------------------------------------------------------

    def load_settings(self) -> dict[str, Any]:
        return {k: json.loads(v) for k, v in self._exec("SELECT key, value FROM settings").fetchall()}

    def save_setting(self, key: str, value: Any) -> None:
        self._exec("INSERT INTO settings VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                   (key, json.dumps(value)))

    def clear_settings(self) -> None:
        self._exec("DELETE FROM settings")

    # phone sessions -------------------------------------------------------------

    def add_session(self, token_hash: str, csrf: str, expires_at: float) -> None:
        self._exec("INSERT INTO sessions VALUES (?, ?, ?)", (token_hash, csrf, expires_at))

    def session(self, token_hash: str, at: float) -> dict | None:
        row = self._exec("SELECT csrf, expires_at FROM sessions WHERE token_hash = ? AND expires_at > ?",
                         (token_hash, at)).fetchone()
        return {"csrf": row[0], "expires_at": row[1]} if row else None

    def delete_session(self, token_hash: str) -> None:
        self._exec("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    def prune_sessions(self, at: float) -> None:
        self._exec("DELETE FROM sessions WHERE expires_at <= ?", (at,))
