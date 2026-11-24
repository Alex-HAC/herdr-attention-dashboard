"""Send a confirmed reply to a Herdr agent, never automatically.

The read-only Herdr client stays read-only. This module talks to
``agent.prompt`` on its own, and only after an explicit Send/quick-answer action (or Steer confirmation).
A working agent is not interrupted unless the caller opts in; the default
is to queue the text until the agent is idle or done (states where Herdr
will accept a prompt). A blocked approval screen cannot take a prompt —
Herdr rejects that with agent_blocked — so those stay queued or are
refused, never forced.
"""

from __future__ import annotations
from .content import agent_name

import json
import os
import socket
import threading
import time
import uuid
from typing import Callable

from .herdr import HerdrError, default_socket_path, _read_line

MAX_TEXT = 8000


def payload(text: str, quote: str | None = None) -> str:
    """The exact text sent to the pane: quote lines (if any), a blank line, the reply.

    The preview shows this and delivery sends this, so they can never differ.
    """
    text = (text or "").strip()
    quote = (quote or "").strip()
    if not quote:
        return text
    quoted = "\n".join("> " + line if line.strip() else ">" for line in quote.splitlines())
    return f"{quoted}\n\n{text}"
WAITING_STATUSES = ("idle", "done")
SCHEMA = """
CREATE TABLE IF NOT EXISTS replies (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          REAL NOT NULL,
    identity    TEXT NOT NULL,
    pane_id     TEXT,
    agent_name  TEXT,
    text        TEXT NOT NULL,
    quote       TEXT,
    when_policy TEXT NOT NULL,
    interrupt   INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL,
    sent_at     REAL,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS replies_identity ON replies (identity, at);
"""

REPLY_COLUMNS = (
    "id", "at", "identity", "pane_id", "agent_name", "text", "quote",
    "when_policy", "interrupt", "status", "sent_at", "error", "about",
)


class HerdrPrompt:
    """Explicit reply writes; kept separate from the read-only client."""

    def __init__(self, socket_path: str | None = None, timeout: float = 15.0):
        self.socket_path = socket_path or default_socket_path()
        self.timeout = timeout

    def prompt(self, target: str, text: str) -> dict:
        return self._request("agent.prompt", {"target": target, "text": text})

    def send_keys(self, target: str, keys: list[str]) -> dict:
        return self._request("agent.send_keys", {"target": target, "keys": keys})

    def send_text(self, pane_id: str, text: str) -> dict:
        return self._request("pane.send_text", {"pane_id": pane_id, "text": text})

    def _request(self, method, params):
        request = {
            "id": uuid.uuid4().hex[:12],
            "method": method,
            "params": params,
        }
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(os.path.expanduser(self.socket_path))
            sock.sendall((json.dumps(request) + "\n").encode())
            reply = _read_line(sock)
        except (OSError, socket.timeout) as exc:
            raise HerdrError(f"cannot reach Herdr at {self.socket_path}: {exc}") from exc
        finally:
            sock.close()
        if reply is None:
            raise HerdrError(f"{method}: Herdr closed the connection without a reply")
        message = json.loads(reply)
        if "error" in message:
            err = message["error"] or {}
            raise HerdrError(f"{method}: {err.get('code')}: {err.get('message')}",
                             code=err.get("code"))
        return message.get("result", {}) or {}


def ensure_schema(store) -> None:
    with store._lock:
        store._db.executescript(SCHEMA)
        # v0.9.6: the card a reply was sent from (the recipient may be its lead). NULL on older rows.
        if "about" not in {row[1] for row in store._db.execute("PRAGMA table_info(replies)")}:
            store._db.execute("ALTER TABLE replies ADD COLUMN about TEXT")


def _row(row) -> dict:
    item = dict(zip(REPLY_COLUMNS, row))
    item["interrupt"] = bool(item["interrupt"])
    return item


class ReplyService:
    def __init__(self, store, settings, clock: Callable[[], float] = time.time,
                 sender: Callable[[str, str], dict] | None = None,
                 socket_path: str | None = None):
        self.store = store
        self.settings = settings
        self.clock = clock
        self._sender = sender
        self._socket_path = socket_path
        self._lock = threading.Lock()
        ensure_schema(store)
        # A crash during transport is ambiguous. Surface it; never auto-resend.
        store._exec("UPDATE replies SET status='failed',error='Delivery interrupted; check the recipient before retrying.' WHERE status='sending'")

    def sender(self) -> Callable[[str, str], dict]:
        if self._sender is not None:
            return self._sender
        client = HerdrPrompt(self._socket_path or self.settings.get("herdr.socket") or None)
        return client.prompt

    def get(self, reply_id: int) -> dict | None:
        row = self.store._exec(
            f"SELECT {', '.join(REPLY_COLUMNS)} FROM replies WHERE id = ?", (reply_id,)
        ).fetchone()
        return _row(row) if row else None

    def for_agent(self, identity: str, limit: int = 20) -> list[dict]:
        rows = self.store._exec(
            f"SELECT {', '.join(REPLY_COLUMNS)} FROM replies WHERE identity = ? "
            "ORDER BY id DESC LIMIT ?", (identity, limit)
        ).fetchall()
        return [_row(r) for r in rows]

    def queued(self) -> list[dict]:
        rows = self.store._exec(
            f"SELECT {', '.join(REPLY_COLUMNS)} FROM replies WHERE status = 'queued' ORDER BY id"
        ).fetchall()
        return [_row(r) for r in rows]

    def preview(self, agent: dict, text: str, quote: str | None = None) -> dict:
        text = (text or "").strip()
        quote = (quote or "").strip() or None
        if not text:
            raise ValueError("the reply is empty")
        full = payload(text, quote)
        status = agent.get("status") or "unknown"
        present = bool(agent.get("present"))
        pane = agent.get("pane_id")
        name = agent.get("name") or agent.get("label") or agent.get("identity")
        warning = None
        default_when = "now"
        can_send_now = True
        if not present or not pane:
            warning = "This agent is no longer running, so a reply cannot be sent."
            can_send_now = False
            default_when = "when_waiting"
        elif status == "working":
            warning = ("This agent is working. Sending now can interrupt the current turn. "
                       "The default is to send when it is next waiting (idle or finished).")
            default_when = "when_waiting"
        elif status == "blocked":
            warning = ("Answer this in the terminal. A reply from here is only delivered "
                       "after the agent leaves that screen.")
            default_when = "when_waiting"
            can_send_now = False
        return {
            "preview": True,
            "identity": agent.get("identity"),
            "name": name,
            "pane_id": pane,
            "status": status,
            "present": present,
            "text": text,
            "quote": quote,
            "warning": warning,
            "default_when": default_when,
            "can_send_now": can_send_now,
            "payload": full,
            "length": len(full),
            "max_text": MAX_TEXT,
            "too_long": len(full) > MAX_TEXT,
            "waiting_statuses": list(WAITING_STATUSES),
        }

    def submit(self, agent: dict, *, text: str, quote: str | None, when: str,
               interrupt: bool, confirm: bool, about: str | None = None) -> dict:
        if not confirm:
            raise ValueError("confirm is required; nothing was sent")
        preview = self.preview(agent, text, quote)
        if preview["too_long"]:
            raise ValueError(f"the reply is {preview['length']} characters with the quote; "
                             f"the limit is {MAX_TEXT}. Nothing was sent.")
        when = when or preview["default_when"]
        if when not in ("now", "when_waiting"):
            raise ValueError("when must be now or when_waiting")
        if when == "now" and not preview["can_send_now"]:
            raise ValueError(preview["warning"] or "cannot send now")
        if when == "now" and preview["status"] == "working" and not interrupt:
            raise ValueError("this agent is working; pass interrupt=true to send now, "
                             "or when=when_waiting to queue the reply")
        if not preview["pane_id"] or not preview["present"]:
            raise ValueError(preview["warning"] or "no pane to send to")
        now = self.clock()
        cur = self.store._exec(
            "INSERT INTO replies (at, identity, pane_id, agent_name, text, quote, when_policy, "
            "interrupt, status, about) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (now, preview["identity"], preview["pane_id"], preview["name"], preview["text"],
             preview["quote"], when, int(bool(interrupt) or when == "now"),
             "queued", about or preview["identity"]),
        )
        item = self.get(int(cur.lastrowid))
        if when == "now":
            return self._deliver(item, agent)
        return item

    def answer_choice(self, agent, option, keys, confirm, literal=None):
        if not confirm or agent.get('status') != 'blocked' or not agent.get('present'):
            raise ValueError('Only an explicit answer to the current blocked dialog can send keys.')
        cur = self.store._exec("INSERT INTO replies (at,identity,pane_id,agent_name,text,when_policy,interrupt,status,about) VALUES (?,?,?,?,?,'choice',0,'sending',?)",
                               (self.clock(),agent['identity'],agent['pane_id'],agent_name(agent),option,agent['identity']))
        item = self.get(cur.lastrowid)
        try:
            client = HerdrPrompt(self._socket_path or self.settings.get('herdr.socket') or None)
            sender = getattr(self, '_key_sender', None) or client.send_keys
            if literal is not None:
                text_sender = getattr(self, '_text_sender', None) or client.send_text
                text_sender(agent['pane_id'], literal)
            sender(agent['pane_id'], keys)
        except (HerdrError, OSError, ValueError) as exc:
            self._log_activity(item, sent=False, error=str(exc)[:200])
            return self._fail(item['id'], str(exc)[:300])
        self.store._exec("UPDATE replies SET status='sent',sent_at=? WHERE id=?",(self.clock(),item['id']))
        self._log_activity(item, sent=True)
        return self.get(item['id'])

    def cancel(self, reply_id: int, identity: str | None = None) -> dict | None:
        item = self.get(reply_id)
        if not item or item["status"] != "queued":
            return None
        if identity and item["identity"] != identity:
            return None
        self.store._exec("UPDATE replies SET status = 'cancelled' WHERE id = ? AND status = 'queued'",
                         (reply_id,))
        return self.get(reply_id)

    def tick(self, lookup: Callable[[str], dict | None]) -> list[dict]:
        """Send queued 'when_waiting' replies whose agents are now idle or done."""
        sent = []
        for item in self.queued():
            if item["when_policy"] != "when_waiting":
                # A leftover 'now' row (e.g. a crash mid-send) is retried only if still sendable.
                agent = lookup(item["identity"])
                if not agent:
                    self._fail(item["id"], "the agent is no longer in a watched workspace")
                    continue
                if agent.get("status") == "working" and not item["interrupt"]:
                    continue
                sent.append(self._deliver(item, agent))
                continue
            agent = lookup(item["identity"])
            if not agent or not agent.get("present"):
                self._fail(item["id"], "the agent is no longer running")
                continue
            if agent.get("status") in WAITING_STATUSES:
                sent.append(self._deliver(item, agent))
        return sent

    def _fail(self, reply_id: int, error: str) -> dict:
        self.store._exec("UPDATE replies SET status = 'failed', error = ? WHERE id = ?",
                         (error, reply_id))
        return self.get(reply_id)

    def _deliver(self, item: dict, agent: dict) -> dict:
        claimed = self.store._exec("UPDATE replies SET status='sending' WHERE id=? AND status='queued'", (item['id'],))
        if claimed.rowcount != 1:
            return self.get(item['id'])
        pane = agent.get("pane_id") or item["pane_id"]
        # Same function as the preview; never truncated (submit refuses over-long text).
        text = payload(item["text"], item.get("quote"))
        try:
            self.sender()(pane, text)
        except HerdrError as exc:
            code = getattr(exc, "code", None)
            if code == "agent_blocked":
                # Stay queued: the approval screen is still up.
                self.store._exec(
                    "UPDATE replies SET status = 'queued', when_policy = 'when_waiting', "
                    "error = ? WHERE id = ?",
                    ("Herdr rejected the prompt because the agent is at a blocked screen; "
                     "it will be sent when the agent is idle or finished.", item["id"]),
                )
                return self.get(item["id"])
            self._fail(item["id"], str(exc)[:300])
            self._log_activity(item, sent=False, error=str(exc)[:200])
            return self.get(item["id"])
        except (OSError, TypeError, ValueError) as exc:
            self._fail(item["id"], str(exc)[:300])
            self._log_activity(item, sent=False, error=str(exc)[:200])
            return self.get(item["id"])
        self.store._exec(
            "UPDATE replies SET status = 'sent', sent_at = ?, error = NULL WHERE id = ?",
            (self.clock(), item["id"]),
        )
        self._log_activity(item, sent=True)
        return self.get(item["id"])

    def _log_activity(self, item: dict, *, sent: bool, error: str | None = None) -> None:
        title = item["text"].splitlines()[0][:160] if item.get("text") else ""
        if not sent and error:
            title = f"(not sent) {title}"
        try:
            self.store.add_activity(at=self.clock(), identity=item["identity"], kind="reply",
                                    status="sent" if sent else "failed", title=title, live=True)
        except (TypeError, ValueError):
            pass
