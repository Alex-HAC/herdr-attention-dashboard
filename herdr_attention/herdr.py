"""Read-only client for the Herdr socket API.

Verified against Herdr 0.9.1 / protocol 22 (see docs/phase0-runtime.md):

* One JSON request per connection, newline terminated; one JSON reply line.
* ``events.subscribe`` keeps the connection open and streams one JSON
  envelope per line: ``{"event": "...", "data": {...}}``.
* ``pane.agent_status_changed`` subscriptions need an explicit ``pane_id``,
  so the watcher subscribes per agent pane and re-subscribes when panes
  appear or disappear.

Only read methods are allowed here. Steering methods (prompt, send keys,
focus, ...) are deliberately refused so v1 can never drive an agent.
"""

from __future__ import annotations

import json
import os
import socket
import threading
from typing import Callable, Iterable

READ_METHODS = frozenset({
    "ping",
    "agent.list",
    "agent.get",
    "agent.read",
    "agent.explain",
    "pane.list",
    "pane.get",
    "workspace.list",
    "tab.list",
    "events.subscribe",
})

# Lifecycle events that change which panes exist or which agent is in them.
TOPOLOGY_EVENTS = (
    "pane.created",
    "pane.closed",
    "pane.exited",
    "pane.updated",
    "pane.moved",
    "pane.agent_detected",
    "workspace.created",
    "workspace.closed",
    "workspace.renamed",
    "tab.created",
    "tab.closed",
    "tab.renamed",
    "tab.moved",
)


class HerdrError(RuntimeError):
    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


def default_socket_path() -> str:
    return os.environ.get("HERDR_SOCKET_PATH") or os.path.expanduser("~/.config/herdr/herdr.sock")


class HerdrClient:
    ALLOWED_METHODS = READ_METHODS

    def __init__(self, socket_path: str | None = None, timeout: float = 5.0):
        self.socket_path = socket_path or default_socket_path()
        self.timeout = timeout
        self._ids = 0
        self._lock = threading.Lock()

    def _next_id(self) -> str:
        with self._lock:
            self._ids += 1
            return f"hat-{self._ids}"

    def _connect(self, timeout: float | None) -> socket.socket:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(self.socket_path)
        except OSError as exc:
            sock.close()
            raise HerdrError(f"cannot reach Herdr at {self.socket_path}: {exc}") from exc
        return sock

    def call(self, method: str, params: dict | None = None):
        if method not in self.ALLOWED_METHODS or method == "events.subscribe":
            raise HerdrError(f"method {method!r} is not allowed (read-only client)")
        request = {"id": self._next_id(), "method": method, "params": params or {}}
        sock = self._connect(self.timeout)
        try:
            sock.sendall((json.dumps(request) + "\n").encode())
            reply = _read_line(sock)
        finally:
            sock.close()
        if reply is None:
            raise HerdrError(f"{method}: Herdr closed the connection without a reply")
        message = json.loads(reply)
        if "error" in message:
            err = message["error"] or {}
            raise HerdrError(f"{method}: {err.get('code')}: {err.get('message')}", code=err.get("code"))
        return message.get("result", {})

    # Convenience wrappers -------------------------------------------------

    def ping(self) -> dict:
        return self.call("ping")

    def agents(self) -> list[dict]:
        return self.call("agent.list").get("agents", [])

    def agent(self, target: str) -> dict | None:
        try:
            result = self.call("agent.get", {"target": target})
        except HerdrError:
            return None
        return result.get("agent", result)

    def workspaces(self) -> list[dict]:
        return self.call("workspace.list").get("workspaces", [])

    def tabs(self) -> list[dict]:
        return self.call("tab.list").get("tabs", [])

    def panes(self) -> list[dict]:
        return self.call('pane.list').get('panes', [])

    def read_output(self, target: str, lines: int = 40, source: str = "recent") -> str:
        result = self.call("agent.read", {"target": target, "source": source, "lines": lines, "strip_ansi": True})
        read = result.get("read", result)
        return read.get("text", "") if isinstance(read, dict) else ""

    def read_ansi(self, target: str, lines: int = 200) -> str:
        """v0.9.8: the visible screen with its SGR colour codes (Herdr ``format: "ansi"``).
        A passive read of Herdr's screen copy; safe while the agent is busy."""
        result = self.call("agent.read", {"target": target, "source": "visible", "lines": lines, "format": "ansi"})
        read = result.get("read", result)
        return read.get("text", "") if isinstance(read, dict) else ""

    def read_screen(self, target: str, lines: int = 40) -> tuple[str, str]:
        """Recent history, or the visible screen when the agent is busy.

        Herdr 0.9.1 refuses ``recent`` for a working full-screen agent
        (``agent_not_idle``: its history could only be captured by scrolling,
        which we never do). ``visible`` always works and changes nothing.
        Returns (text, source).
        """
        try:
            return self.read_output(target, lines, "recent"), "recent"
        except HerdrError as exc:
            if exc.code != "agent_not_idle":
                raise
        return self.read_output(target, lines, "visible"), "visible"

    def explain(self, target: str) -> dict:
        """Which detection rule matched. The socket API nests the details
        under "explain" (the CLI's --json output does not)."""
        result = self.call("agent.explain", {"target": target})
        return result.get("explain", result)

    # Event stream ----------------------------------------------------------

    def subscribe(self, pane_ids: Iterable[str], stop: threading.Event,
                  on_event: Callable[[dict], None], on_started: Callable[[], None] | None = None,
                  idle_timeout: float = 1.0) -> None:
        """Stream events until ``stop`` is set or the connection drops.

        Raises HerdrError when the subscription cannot start; returns normally
        when Herdr closes the stream (the caller decides whether to reconnect).
        """
        subs = [{"type": t} for t in TOPOLOGY_EVENTS]
        subs += [{"type": "pane.agent_status_changed", "pane_id": p} for p in sorted(set(pane_ids))]
        request = {"id": self._next_id(), "method": "events.subscribe", "params": {"subscriptions": subs}}
        sock = self._connect(self.timeout)
        try:
            sock.sendall((json.dumps(request) + "\n").encode())
            first = _read_line(sock)
            if first is None:
                raise HerdrError("events.subscribe: connection closed before confirmation")
            ack = json.loads(first)
            if "error" in ack:
                raise HerdrError(f"events.subscribe: {ack['error']}")
            if on_started:
                on_started()
            sock.settimeout(idle_timeout)
            buf = b""
            while not stop.is_set():
                try:
                    chunk = sock.recv(65536)
                except socket.timeout:
                    continue
                if not chunk:
                    return
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if line.strip():
                        on_event(json.loads(line))
        finally:
            sock.close()


def _read_line(sock: socket.socket, limit: int = 32 * 1024 * 1024) -> str | None:
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = sock.recv(1 << 20)
        if not chunk:
            break
        buf += chunk
        if len(buf) > limit:
            raise HerdrError("reply too large")
    return buf.decode() if buf else None


def wait_for_herdr(client: HerdrClient, stop: threading.Event, max_delay: float = 30.0) -> bool:
    """Back off until Herdr answers a ping. Returns False if stopped first."""
    delay = 1.0
    while not stop.is_set():
        try:
            client.ping()
            return True
        except (HerdrError, OSError):
            stop.wait(delay)
            delay = min(max_delay, delay * 2)
    return False
