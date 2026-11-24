"""HTTP API for the desktop plugin, the CLI and the phone page.

Three listeners share one handler (Unix, phone, desktop):

* Local: a Unix socket in $XDG_RUNTIME_DIR (directory 0700, socket 0600).
  Only your user can connect, so no login. Has a few local-only routes:
  presence reports, the full workspace list (to switch workspaces on), and
  the live update stream.
* Phone: TCP, off by default. Every route except the login page needs a
  session cookie; every change also needs the CSRF token and an allowed
  Origin. Unwatched workspaces are never visible here.

There are no routes that type into, prompt or steer an agent.
"""

from __future__ import annotations

import gzip
import hashlib
import hmac
import json
import os
import secrets as pysecrets
import socket
import socketserver
import threading
import time
import urllib.parse
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Protocol

from . import __version__, herdr_theme
from .ui_contract import API, FEATURES, CONTRACT, hash_path, navigation_hash, route_id

WEB_DIR = Path(__file__).parent / "web"
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/ui-contract.js": ("ui-contract.js", "text/javascript; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/graph.js": ("graph.js", "text/javascript; charset=utf-8"),
          "/terminal.js": ("terminal.js", "text/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8"),
          "/InterVariable.woff2": ("InterVariable.woff2", "font/woff2"),
          "/Inter-LICENSE.txt": ("Inter-LICENSE.txt", "text/plain; charset=utf-8")}
# v0.9.8.7 (load speed): static files are served with ?v=<content hash> URLs written into
# index.html and style.css, cached for a year (immutable); index.html, theme.css and the API stay
# no-store, so a new version still shows at once and the 0.9.7.5 auto-reload keeps working.
IMMUTABLE = "public, max-age=31536000, immutable"
# Text answers larger than this go gzipped when the browser accepts it.
GZIP_MIN = 1024
COMPRESSIBLE = ("text/", "application/json", "application/javascript", "image/svg+xml")
# Agent card fields no client reads (web, bar panel, CLI); the server keeps them for its own rules.
# last_assistant alone was about 1 MB of a 3.9 MB view with 700 subagent cards.
VIEW_DROP = ("last_assistant", "session_identity", "report_output_revision")
_ASSETS: dict[str, tuple] = {}
_VIEW_BYTES: dict[tuple, tuple] = {}
_VIEW_BYTES_LOCK = threading.Lock()


def _asset(name: str) -> tuple[bytes, bytes, str]:
    """(body, gzipped body, short content hash) of one web file, re-read when it changes on disk."""
    path = WEB_DIR / name
    stamp = path.stat().st_mtime_ns
    cached = _ASSETS.get(name)
    if cached and cached[0] == stamp:
        return cached[1:]
    body = path.read_bytes()
    if name == "style.css":   # the font URL inside the stylesheet is versioned too
        body = body.replace(b'url("InterVariable.woff2")', f'url("InterVariable.woff2?v={_asset("InterVariable.woff2")[2]}")'.encode())
    elif name == "index.html":
        for other in ("style.css", "ui-contract.js", "graph.js", "terminal.js", "app.js", "InterVariable.woff2"):
            version = _asset(other)[2]
            body = body.replace(f'"{other}"'.encode(), f'"{other}?v={version}"'.encode())
    gz = gzip.compress(body, 9, mtime=0) if not name.endswith(".woff2") else b""
    digest = hashlib.sha256(body).hexdigest()[:12]
    _ASSETS[name] = (stamp, body, gz, digest)
    return body, gz, digest


# The live Omarchy theme colours (herdr_theme.py); no login needed, so the login page is themed too.
THEME_CSS = "/theme.css"
# Sole registration point for routes restricted to the desktop server object.
DESKTOP_ROUTES = {API["focus"]: "_focus", API["desktop_navigation"]: "_navigate"}
COOKIE = "hat_session"

def _content_disposition(filename, download=False) -> str:
    """inline for the viewer; attachment with a safe filename for Download (Android Chrome)."""
    import re
    from urllib.parse import quote
    kind = "attachment" if download else "inline"
    if not filename:
        return kind
    name = Path(str(filename)).name.replace("\\", "_").replace('"', "").replace("\r", "").replace("\n", "")
    if not name or name in (".", ".."):
        return kind
    ascii_name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "visual"
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name, safe='')}"


SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cache-Control": "no-store",
}


class App(Protocol):
    """What the API needs from the collector."""

    def view(self, *, local: bool, cached: bool = False) -> dict: ...
    def focus_agent(self, identity: str) -> dict: ...
    def history(self, limit: int) -> list[dict]: ...
    def agent_detail(self, identity: str) -> dict | None: ...
    def chooser(self) -> list[dict]: ...
    def refresh_workspaces(self) -> dict: ...
    def conversation(self, identity: str, limit: int, before: int | None) -> dict | None: ...
    def relation_action(self, body: dict, *, local: bool) -> dict: ...
    def relations_overview(self) -> dict: ...
    def inference_check(self, ignore_declared=()) -> dict: ...
    def request_summary(self, identity: str, mode: str, question: str | None = None,
                        context: str | None = None) -> dict: ...
    def summary_job(self, job_id: int) -> dict | None: ...
    def preview_reply(self, identity: str, text: str, quote: str | None = None) -> dict: ...
    def submit_reply(self, identity: str, *, text: str, quote: str | None, when: str,
                     interrupt: bool, confirm: bool, event_id: int | None = None) -> dict: ...
    def cancel_reply(self, reply_id: int, identity: str) -> dict | None: ...
    def answer_ask(self, item: str, text: str) -> dict: ...
    def replies_for(self, identity: str) -> dict | None: ...
    def summary_audit(self) -> dict: ...
    def read_output(self, identity: str) -> dict | None: ...
    def terminal(self, identity: str, since: str | None = None, history: bool = False) -> dict | None: ...
    def ack(self, event_id: int) -> bool: ...
    def notifications(self, limit: int = 50) -> dict: ...
    def notification(self, event_id: int) -> dict | None: ...
    def notifications_read_all(self) -> int: ...
    def latest_visuals(self, identity=None, workspace=None, team=False, refresh=False,
                       before=None, limit=None, hub=False): ...
    def visual_file(self, media_id, thumbnail=False): ...
    def visual_filename(self, media_id): ...
    def visual_thumbnail_etag(self, media_id): ...
    def mark_visuals_seen(self, media_ids=None, all=False): ...
    def settings_view(self, *, local: bool) -> dict: ...
    def update_settings(self, changes: dict, *, local: bool) -> dict: ...
    def report_presence(self, state: str, idle_seconds: float) -> None: ...
    def reset_settings(self) -> dict: ...
    def notify_setup(self) -> dict: ...
    def notify_test(self) -> dict: ...
    def explain(self, identity: str) -> dict: ...
    def wait_for_change(self, since: int, timeout: float) -> int: ...
    def check_passphrase(self, candidate: str) -> bool | None: ...
    def account_icon_file(self, account: str): ...
    def account_icon_upload(self, account: str, data: bytes) -> dict: ...
    def account_icon_delete(self, account: str) -> dict: ...
    store: Any
    settings: Any


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class Handler(BaseHTTPRequestHandler):
    server_version = f"herdr-attention/{__version__}"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    app: App
    local: bool
    desktop: bool = False

    # plumbing ------------------------------------------------------------------

    def log_message(self, fmt: str, *args: Any) -> None:  # keep request logs out of the journal
        pass

    def address_string(self) -> str:
        return "local" if self.local else str(self.client_address[0])

    def _gzip_ok(self) -> bool:
        return "gzip" in (self.headers.get("Accept-Encoding") or "").lower()

    def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None,
              gzipped: bytes | None = None) -> None:
        extra = dict(extra or {})
        compressible = content_type.startswith(COMPRESSIBLE)
        if compressible and len(body) >= GZIP_MIN and self._gzip_ok():
            body = gzipped or gzip.compress(body, 5, mtime=0)
            extra["Content-Encoding"] = "gzip"
        if compressible:
            extra["Vary"] = "Accept-Encoding"
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in SECURITY_HEADERS.items():
            if key not in extra:
                self.send_header(key, value)
        for key, value in extra.items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: Any, extra: dict | None = None) -> None:
        self._send(status, json.dumps(payload).encode(), "application/json", extra)

    def _read_raw_body(self) -> None:
        """Always drain the body first, so an early error cannot leave it in
        the connection to be misread as the next request."""
        self._raw_body = b""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > 64 * 1024:
            self.close_connection = True
            raise ApiError(413, "request too large")
        if length:
            self._raw_body = self.rfile.read(length)

    def _body(self) -> dict:
        if not self._raw_body:
            return {}
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            raise ApiError(415, "send JSON")
        try:
            data = json.loads(self._raw_body)
        except json.JSONDecodeError:
            raise ApiError(400, "invalid JSON") from None
        if not isinstance(data, dict):
            raise ApiError(400, "expected a JSON object")
        return data

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_HEAD(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def _view(self) -> dict:
        return self._view_from(self.app.view(local=self.local, cached=True))

    def _view_from(self, shared: dict) -> dict:
        # A copy: the collector may hand the same cached view to several listeners (v0.9.8.7).
        view = dict(shared)
        view["agents"] = [{k: v for k, v in a.items() if k not in VIEW_DROP} for a in view.get("agents", [])]
        # Capabilities belong to the bound server, never to the requesting peer.
        view["features"] = dict(view.get("features", {}))
        view["features"][FEATURES["desktop_focus"]] = self.desktop
        return view

    def _view_response(self) -> None:
        """/api/view: the encoded (and gzipped) bytes are kept with the shared view they came from,
        so several pollers of one build pay for json and gzip once (v0.9.8.7)."""
        shared = self.app.view(local=self.local, cached=True)
        key = (self.local, self.desktop)
        with _VIEW_BYTES_LOCK:
            hit = _VIEW_BYTES.get(key)
            if not hit or hit[0] is not shared:
                body = json.dumps(self._view_from(shared)).encode()
                hit = _VIEW_BYTES[key] = (shared, body, gzip.compress(body, 5, mtime=0))
        self._send(200, hit[1], "application/json", gzipped=hit[2])

    def _desktop_route(self, method: str, path: str) -> bool:
        endpoint = DESKTOP_ROUTES.get(path)
        if endpoint is None:
            return False
        if not self.desktop:
            raise ApiError(404, "not found")
        getattr(self, endpoint)(method)
        return True

    def _navigate(self, method: str) -> None:
        if method == "POST":
            target = navigation_hash(self._body().get("hash", ""))
            if target is None:
                raise ApiError(400, "invalid desktop route")
            with self.server.navigation_lock:
                self.server.navigation = {"hash": target, "sequence": self.server.navigation["sequence"] + 1}
        self._json(200, self.server.navigation)

    def _focus(self, method: str) -> None:
        if method != "POST":
            raise ApiError(404, "not found")
        body = self._body()
        identity = body.get("identity")
        if not isinstance(identity, str) or not identity:
            raise ApiError(400, "identity is required")
        try:
            result = self.app.focus_agent(identity)
        except LookupError:
            raise ApiError(404, "no such live agent in a watched workspace") from None
        except (RuntimeError, OSError):
            raise ApiError(502, "could not focus Herdr") from None
        self._json(200, result)

    def _tailnet_gate(self) -> bool:
        """v0.9.7.4 allow list (tailnet_access.py): False = refused with a bare 403, before the
        login page, static files or any data. Only the phone listener is gated."""
        self._tailnet_ip = None
        access = getattr(self.app, "tailnet", None)
        if self.local or access is None:
            return True
        self._tailnet_ip = access.client_ip(str(self.client_address[0]), self.headers.get("X-Forwarded-For"))
        if access.allowed(self._tailnet_ip):
            return True
        self.close_connection = True
        self._send(403, b"forbidden\n", "text/plain; charset=utf-8")
        return False

    def _tailnet_access(self, method: str) -> None:
        """The allow list and the peer list: on the laptop only (Unix socket, desktop listener)."""
        access = getattr(self.app, "tailnet", None)
        if not self.local or access is None:
            raise ApiError(403, "only on the laptop")
        if method == "POST":
            try:
                body = self._body()
                access.set_entries(body.get("entries"), body.get("enabled"))
            except ValueError as exc:
                raise ApiError(400, str(exc)) from None
        elif method != "GET":
            raise ApiError(404, "not found")
        self._json(200, access.overview())

    def _dispatch(self, method: str) -> None:
        url = urllib.parse.urlsplit(self.path)
        query = dict(urllib.parse.parse_qsl(url.query))
        self._raw_body = b""
        try:
            self._read_raw_body()
            if not self._tailnet_gate():
                return
            if self.desktop:
                # Also prevent DNS rebinding: do not accept attacker-controlled Host.
                host, port = self.server.server_address
                if self.headers.get("Host") != f"{host}:{port}":
                    raise ApiError(403, "desktop host refused")
            if not self.desktop and url.path in DESKTOP_ROUTES:
                self._desktop_route(method, url.path)
            if (not self.local or self.desktop) and url.path in STATIC and method == "GET":
                return self._static(url.path, query)
            if (not self.local or self.desktop) and url.path == THEME_CSS and method == "GET":
                return self._send(200, herdr_theme.css(herdr_theme.current()).encode(), "text/css; charset=utf-8")
            if not self.local and url.path == "/login" and method == "POST":
                return self._login()
            if not self.local and url.path == "/logout" and method == "POST":
                return self._logout()
            if not url.path.startswith("/api/"):
                raise ApiError(404, "not found")
            if url.path == API["tailnet_access"] and not self.local:
                raise ApiError(403, "only on the laptop")
            session = ({"csrf": self.server.csrf} if self.desktop else
                       None if self.local else self._require_session())
            if not self.local and getattr(self.app, "tailnet", None) is not None:
                self.app.tailnet.seen(self._tailnet_ip)
            if method in ("POST", "DELETE") and (not self.local or self.desktop):
                self._require_csrf(session)
            if url.path == API["tailnet_access"]:
                return self._tailnet_access(method)
            if self._desktop_route(method, url.path):
                return
            try:
                self._route(method, url.path, query, session)
            finally:
                if method != "GET":   # a change from here shows on the next view at once
                    getattr(self.app, "invalidate_view", lambda: None)()
        except ApiError as exc:
            self._json(exc.status, {"error": exc.message})
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _visual(self, media_id, thumbnail, download=False):
        try:file,mime,size=self.app.visual_file(media_id,thumbnail)
        except (ValueError,OSError):raise ApiError(404,"visual unavailable")
        cache=None
        if thumbnail and not download:
            etag=None
            if hasattr(self.app,"visual_thumbnail_etag"):
                try:etag=self.app.visual_thumbnail_etag(media_id)
                except Exception:etag=None
            if etag and self.headers.get("If-None-Match")==etag:
                file.close()
                self.send_response(304)
                headers={**SECURITY_HEADERS,"ETag":etag,"Cache-Control":"private, max-age=31536000, immutable"}
                for key,value in headers.items():self.send_header(key,value)
                self.end_headers()
                return
            cache={"Cache-Control":"private, max-age=31536000, immutable"}
            if etag:cache["ETag"]=etag
        name=self.app.visual_filename(media_id) if download else None
        self._media(file,mime,size,download=download,filename=name,cache=cache)

    def _question_image(self, token, thumbnail):
        # Only an image of an open question, checked again on the served descriptor (question_images.py).
        try:file,mime,size=self.app.question_image(token,thumbnail)
        except (ValueError,OSError):raise ApiError(404,"image unavailable")
        self._media(file,mime,size)

    def _account_icon(self, method: str, account: str) -> None:
        """Alex's own image for one account (v0.9.7). The account comes from the quota records,
        the file name from a hash of it; the content must be PNG, JPEG or WebP (never SVG) and is
        served with that fixed type, nosniff and a sandbox CSP. Writes need login and CSRF."""
        try:
            if method == "GET":
                file, mime, size = self.app.account_icon_file(account)
                return self._media(file, mime, size)
            if method == "POST":
                return self._json(200, self.app.account_icon_upload(account, self._raw_body))
            if method == "DELETE":
                return self._json(200, self.app.account_icon_delete(account))
        except LookupError:
            raise ApiError(404, "no such account image") from None
        except ValueError as exc:
            # A stored file that is no longer an image is simply not there.
            raise (ApiError(404, "no such account image") if method == "GET" else ApiError(400, str(exc))) from None
        except OSError:
            raise ApiError(404, "no such account image") from None
        raise ApiError(404, "not found")

    def _media(self, file, mime, size, download=False, filename=None, cache=None):
        with file:
            start,end=0,size-1
            requested=self.headers.get("Range")
            if requested:
                import re
                match=re.fullmatch(r"bytes=(\d*)-(\d*)",requested)
                if not match or (not match[1] and not match[2]):raise ApiError(416,"invalid range")
                if match[1]:
                    start=int(match[1]);end=min(end,int(match[2])) if match[2] else end
                else:start=max(0,size-int(match[2]))
                if start>end or start>=size:raise ApiError(416,"invalid range")
            length=max(0,end-start+1)
            self.send_response(206 if requested else 200)
            headers={**SECURITY_HEADERS,"Content-Type":mime,"Content-Length":str(length),"Accept-Ranges":"bytes",
                "Content-Disposition":_content_disposition(filename, download),
                "Content-Security-Policy":"sandbox; default-src 'none'; style-src 'unsafe-inline'"}
            if cache:headers.update(cache)
            if requested:headers["Content-Range"]=f"bytes {start}-{end}/{size}"
            for key,value in headers.items():self.send_header(key,value)
            self.end_headers()
            if self.command=="HEAD":return
            file.seek(start)
            while length:
                chunk=file.read(min(length,128*1024))
                if not chunk:break
                self.wfile.write(chunk);length-=len(chunk)

    def _cleanup(self, method: str, path: str, query: dict) -> None:
        """Clean up: GET a job or the log; POST a preview; POST a confirm of panes
        from a preview this server made. Writes pass the same session/CSRF checks."""
        cleanup = self.app.cleanup
        who = "local" if self.local and not self.desktop else "desktop" if self.desktop else "phone"
        if method == "GET" and path == API["cleanup"]:
            if not query.get("id"):
                return self._json(200, cleanup.overview())
            try:
                job = cleanup.job(int(query["id"]))
            except ValueError:
                raise ApiError(400, "id must be a number") from None
            if job is None:
                raise ApiError(404, "no such clean up job")
            return self._json(200, job)
        if method != "POST" or path == API["cleanup"]:
            raise ApiError(404, "not found")
        body = self._body()
        if path == API["cleanup_preview"]:
            workspace = body.get("workspace")
            caller = body.get("caller_pane") if self.local and not self.desktop else None
            if any(v is not None and (not isinstance(v, str) or not v or len(v) > 64) for v in (workspace, caller)):
                raise ApiError(400, "workspace and caller_pane must be short ids")
            return self._json(200, cleanup.start_preview(workspace, who=who, caller_pane=caller))
        panes, preview_id = body.get("panes"), body.get("preview_id")
        if body.get("confirm") is not True:
            raise ApiError(400, "an explicit confirm is required")
        if (not isinstance(preview_id, int) or isinstance(preview_id, bool) or not isinstance(panes, list) or not panes or len(panes) > 200
                or not all(isinstance(p, str) and 0 < len(p) <= 64 for p in panes)):
            raise ApiError(400, "send preview_id and a list of pane ids from that preview")
        try:
            return self._json(200, cleanup.start_confirm(preview_id, panes, who=who))
        except ValueError as exc:
            raise ApiError(409, str(exc)) from None

    # phone auth ------------------------------------------------------------------

    def _token_hash(self) -> str | None:
        cookie = SimpleCookie(self.headers.get("Cookie") or "")
        morsel = cookie.get(COOKIE)
        if not morsel or not morsel.value:
            return None
        return hashlib.sha256(morsel.value.encode()).hexdigest()

    def _require_session(self) -> dict:
        token_hash = self._token_hash()
        session = self.app.store.session(token_hash, time.time()) if token_hash else None
        if not session:
            raise ApiError(401, "login required")
        return session

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            referer = self.headers.get("Referer")
            if not referer:
                return False
            parts = urllib.parse.urlsplit(referer)
            origin = f"{parts.scheme}://{parts.netloc}"
        if self.desktop:
            host, port = self.server.server_address
            return origin == f"http://{host}:{port}"
        allowed = list(self.app.settings.get("phone.allowed_origins", []) or [])
        host = self.headers.get("Host")
        if host:
            allowed += [f"http://{host}", f"https://{host}"]
        return origin in allowed

    def _require_csrf(self, session: dict) -> None:
        if not self._origin_ok():
            raise ApiError(403, "cross-site request refused")
        token = self.headers.get("X-CSRF-Token") or ""
        if not hmac.compare_digest(token, session["csrf"]):
            raise ApiError(403, "missing or wrong CSRF token")

    def _login(self) -> None:
        if not self._origin_ok():
            raise ApiError(403, "cross-site request refused")
        limiter: LoginLimiter = self.server.login_limiter  # type: ignore[attr-defined]
        client = self.address_string()
        if not limiter.allowed(client):
            raise ApiError(429, "too many attempts, wait a few minutes")
        body = self._body()
        result = self.app.check_passphrase(str(body.get("passphrase") or ""))
        if result is None:
            raise ApiError(503, "no dashboard passphrase set (herdr-attention secret set dashboard-passphrase)")
        if not result:
            limiter.failed(client)
            raise ApiError(401, "wrong passphrase")
        limiter.reset(client)
        token = pysecrets.token_urlsafe(32)
        csrf = pysecrets.token_urlsafe(24)
        hours = float(self.app.settings.get("phone.session_hours", 72))
        self.app.store.add_session(hashlib.sha256(token.encode()).hexdigest(), csrf, time.time() + hours * 3600)
        flags = f"HttpOnly; SameSite=Strict; Path=/; Max-Age={int(hours * 3600)}"
        if self.app.settings.get("phone.cookie_secure", True):
            flags += "; Secure"
        self._json(200, {"ok": True, "csrf": csrf}, {"Set-Cookie": f"{COOKIE}={token}; {flags}"})

    def _logout(self) -> None:
        session = self._require_session()
        self._require_csrf(session)
        token_hash = self._token_hash()
        if token_hash:
            self.app.store.delete_session(token_hash)
        self._json(200, {"ok": True}, {"Set-Cookie": f"{COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"})

    def _static(self, path: str, query: dict | None = None) -> None:
        name, content_type = STATIC[path]
        body, gz, digest = _asset(name)
        if self.desktop and name == "index.html":
            import re
            body = re.sub(rb"<title>.*?</title>",
                          ("<title>" + CONTRACT["desktop"]["title"] + "</title>").encode(), body)
            gz = None
        # Only a URL naming the current content may be kept; index.html always asks again.
        extra = {"Cache-Control": IMMUTABLE} if name != "index.html" and (query or {}).get("v") == digest else {}
        self._send(200, body, content_type, extra, gzipped=gz or None)

    # routes ----------------------------------------------------------------------

    def _route(self, method: str, path: str, query: dict, session: dict | None) -> None:
        app = self.app
        if method == "GET" and path == API["visuals"]:
            try:
                limit=query.get("limit")
                before=query.get("before")
                data=app.latest_visuals(query.get("identity"),query.get("workspace"),query.get("team")=="1",
                                        query.get("refresh")=="1", before=before, limit=limit,
                                        hub=query.get("hub")=="1")
            except ValueError as exc:
                message=str(exc)
                raise ApiError(400 if "cursor" in message or "limit" in message or "ids must" in message else 404, message)
            # Jumping to Herdr from a visual uses the shared desktop-only focus route.
            return self._json(200,data)
        if method == "POST" and path == API["visuals_seen"]:
            body=self._body()
            try:
                if body.get("all") is True:
                    return self._json(200, app.mark_visuals_seen(all=True))
                ids=body.get("ids", [])
                return self._json(200, app.mark_visuals_seen(ids, all=False))
            except ValueError as exc:
                raise ApiError(400, str(exc)) from None
        for kind, download in (("visual_file", False), ("visual_thumbnail", False), ("visual_download", True)):
            media_id=route_id(kind,path)
            if media_id is None:
                continue
            if method!="GET" or len(media_id)!=32 or any(c not in "0123456789abcdef" for c in media_id):
                raise ApiError(404,"no such visual")
            if download and query.get("path"):
                raise ApiError(404,"no such visual")
            return self._visual(media_id, thumbnail=(kind=="visual_thumbnail"), download=download)
        account = route_id("account_icon", path)
        if account is not None:
            return self._account_icon(method, urllib.parse.unquote(account))
        for kind in ("question_image","question_thumbnail"):
            token=route_id(kind,path)
            if token is None:
                continue
            if method!="GET" or len(token)!=32 or any(c not in "0123456789abcdef" for c in token):
                raise ApiError(404,"no such image")
            return self._question_image(token,kind=="question_thumbnail")
        if method == "GET" and path == API['health']:
            view = self._view()
            return self._json(200, {"ok": True, "version": __version__, "collector": view["collector"],
                                    "presence": view.get("presence"), "notify": view.get("notify")})
        if method == "GET" and path == API['session'] and session is not None:
            return self._json(200, {"csrf": session["csrf"]})
        if method == "GET" and path == API['view']:
            return self._view_response()
        if method == 'GET' and path == API['layout']:
            view = self._view()
            workspace = query.get('workspace')
            if workspace and workspace not in {w['id'] for w in view['workspaces']}:
                raise ApiError(404, 'no such watched workspace')
            return self._json(200, {'findings': [f for f in view['layout']['findings']
                                                if not workspace or f['workspace_id'] == workspace]})
        if method == "GET" and path == API['history']:
            limit = max(1, min(500, int(query.get("limit") or 100)))
            return self._json(200, {"events": app.history(limit)})
        if method == "GET" and path == API['agent']:
            detail = app.agent_detail(query.get("identity") or "")
            if detail is None:
                raise ApiError(404, "no such agent in a watched workspace")
            return self._json(200, detail)
        if method == "GET" and path == API['chooser']:
            # Every workspace by id and label only, so it can be switched on.
            return self._json(200, {"workspaces": app.chooser()})
        if method == "GET" and path == API['conversation']:
            # On request only; reads the agent's trace file, never prompts the agent.
            try:
                limit = int(query.get("limit") or 10)
                before = int(query["before"]) if query.get("before") else None
            except ValueError:
                raise ApiError(400, "limit and before must be numbers") from None
            result = app.conversation(query.get("identity") or "", limit, before)
            if result is None:
                raise ApiError(404, "no such agent in a watched workspace")
            return self._json(200, result)
        if method == "GET" and path == API['terminal']:
            # v0.9.8 live terminal: read-only, behind the same login and tailnet allow list as all agent data.
            since = query.get("since") or None
            result = app.terminal(query.get("identity") or "", since if since and len(since) <= 64 else None,
                                  history=query.get("history") == "1")
            if result is None:
                raise ApiError(404, "no such agent in a watched workspace")
            return self._json(200, result)
        if path == API.get("file"):
            # v0.9.8.8 file viewer: GET only, read-only, behind the login and tailnet allow list above.
            if method != "GET":
                raise ApiError(405, "read-only")
            from .files import FileError
            try:
                result = app.read_file(query.get("identity") or "", query.get("path") or "", query.get("against") or None)
            except FileError as exc:
                raise ApiError(exc.status, exc.message) from None
            return self._json(200, result, {"Cache-Control": "no-store"})
        if method == "GET" and path == API['output']:
            result = app.read_output(query.get("identity") or "", raw=query.get("raw") == "1")
            if result is None:
                raise ApiError(404, "no such agent in a watched workspace")
            return self._json(200, result)
        if method == "POST" and (target_id := route_id("event_ack", path)) is not None:
            try:
                event_id = int(target_id)
            except (IndexError, ValueError):
                raise ApiError(400, "bad event id") from None
            if not app.settings.get("ui.acknowledge", True):
                raise ApiError(403, "acknowledging is switched off")
            if not app.ack(event_id):
                raise ApiError(404, "no such open event")
            return self._json(200, {"ok": True})
        if method == "GET" and path == API["notifications"]:
            try:
                limit = int(query.get("limit") or 50)
            except ValueError:
                raise ApiError(400, "bad limit") from None
            return self._json(200, app.notifications(limit))
        if method == "GET" and (target_id := route_id("notification", path)) is not None:
            try:
                event_id = int(target_id)
            except (IndexError, ValueError):
                raise ApiError(404, "That notification is no longer available.") from None
            item = app.notification(event_id)
            if item is None:
                raise ApiError(404, "That notification is no longer available.")
            return self._json(200, item)
        if method == "POST" and path == API['notifications_read_all']:
            if not app.settings.get("ui.acknowledge", True):
                raise ApiError(403, "acknowledging is switched off")
            return self._json(200, {"ok": True, "marked": app.notifications_read_all()})
        if method == "GET" and path == API['settings']:
            return self._json(200, app.settings_view(local=self.local))
        if method == "POST" and path == API['settings']:
            try:
                return self._json(200, app.update_settings(self._body(), local=self.local))
            except (KeyError, ValueError, PermissionError) as exc:
                raise ApiError(400, str(exc).strip("'")) from None
        if method == "POST" and path == API["quota_stops_ack"]:
            body = self._body()
            identities = body.get("identities", [])
            if not isinstance(identities, list) or len(identities) > 200 or not all(isinstance(i, str) for i in identities):
                raise ApiError(400, "identities must be an agent list")
            return self._json(200, app.ack_quota_stops(identities))
        if method == "POST" and path == API["workspaces_refresh"]:
            # v0.9.8.3: re-reads Herdr's workspace and agent lists now. Read-only toward Herdr.
            self._body()
            from .collector import TooSoon
            try:
                return self._json(200, app.refresh_workspaces())
            except TooSoon as exc:
                raise ApiError(429, f"Refreshed a moment ago; try again in {max(1, round(exc.retry_after))} s") from None
        if method == "POST" and path == API["quotas_refresh"]:
            self._body()
            return self._json(200, app.refresh_quotas())
        if path in (API["cleanup"], API["cleanup_preview"], API["cleanup_confirm"]):
            return self._cleanup(method, path, query)
        if method == "GET" and path == API['notify_setup']:
            return self._json(200, app.notify_setup())
        if method == "POST" and path == API['notify_test']:
            return self._json(200, app.notify_test())
        if method == "GET" and path == API['summaries_audit']:
            return self._json(200, app.summary_audit())
        if method == "GET" and path == API['summaries']:
            try:
                job_id = int(query.get("id") or 0)
            except ValueError:
                raise ApiError(400, "id must be a number") from None
            job = app.summary_job(job_id) if job_id else None
            if job is None:
                raise ApiError(404, "no such summary job")
            return self._json(200, job)
        if method == "POST" and path == API['summaries']:
            body = self._body()
            try:
                return self._json(200, app.request_summary(
                    str(body.get("identity") or ""), str(body.get("mode") or ""),
                    question=str(body.get("question") or "") or None,
                    context=str(body.get("context") or "") or None,
                ))
            except ValueError as exc:
                raise ApiError(400, str(exc)) from None
        if method == "GET" and path == API["replies"]:
            result = app.replies_for(query.get("identity") or "")
            if result is None:
                raise ApiError(404, "no such agent in a watched workspace")
            return self._json(200, result)
        if method == "POST" and path == API["replies"]:
            body = self._body()
            identity = str(body.get("identity") or "")
            text = str(body.get("text") or "")
            quote = str(body.get("quote") or "") or None
            confirm = bool(body.get("confirm"))
            try:
                event_id = int(body["event_id"]) if body.get("event_id") is not None else None
            except (TypeError, ValueError):
                raise ApiError(400, "bad event id") from None
            try:
                if not confirm:
                    return self._json(200, app.preview_reply(identity, text, quote))
                return self._json(200, app.submit_reply(
                    identity, text=text, quote=quote,
                    when=str(body.get("when") or "") or "now",
                    interrupt=bool(body.get("interrupt")),
                    confirm=True,
                    event_id=event_id, question_id=str(body.get("question_id") or "") or None,
                    recipient_identity=str(body.get("recipient_identity") or "") or None,
                    option=str(body["option"]) if body.get("option") is not None else None,
                    terminal_text=bool(body.get("terminal_text")),
                ))
            except ValueError as exc:
                raise ApiError(400, str(exc)) from None
        if method == "POST" and path == API["asks_answer"]:
            # One question item at a time, through the reply routing (v0.9.6 contract).
            body = self._body()
            try:
                return self._json(200, app.answer_ask(str(body.get("item") or ""), str(body.get("text") or "")))
            except ValueError as exc:
                raise ApiError(400, str(exc)) from None
        if method == "POST" and (target_id := route_id("reply_cancel", path)) is not None:
            try:
                reply_id = int(target_id)
            except (IndexError, ValueError):
                raise ApiError(400, "bad reply id") from None
            body = self._body()
            result = app.cancel_reply(reply_id, str(body.get("identity") or ""))
            if result is None:
                raise ApiError(404, "no such queued reply")
            return self._json(200, result)
        if method == "GET" and path == API['relations']:
            return self._json(200, app.relations_overview())
        if method == "POST" and path == API['relations']:
            # Declaring who reports to whom is configuration; it never reaches an agent.
            from .relations import RelationError
            body = self._body()
            if not self.local:
                body.pop("registered", None)    # only the local launch helper registers
            try:
                return self._json(200, app.relation_action(body, local=self.local))
            except RelationError as exc:
                raise ApiError(400, str(exc)) from None
        if method == "POST" and path == API['explain']:
            return self._json(200, app.explain(self._body().get("identity") or ""))

        if self.local:
            if method == 'GET' and path == API['inference_check']:
                from .relations import RelationError
                try:
                    ignored = [ref for ref in query.get('ignore_declared', '').split(',') if ref]
                    return self._json(200, app.inference_check(ignored))
                except RelationError as exc:
                    raise ApiError(400, str(exc)) from None
            if method == 'GET' and path == API['routing_walls']:
                return self._json(200, {'walls': app.observations.walls()})
            if method == 'POST' and path in (API['hooks'], API['launches'], API['launches_bind'], API['routing_clear']):
                from .relations import RelationError
                body = self._body()
                try:
                    if path == API['hooks']:
                        result = app.hook_event(body)
                    elif path == API['routing_clear']:
                        result = app.observations.clear_wall(body.get('provider'))
                        app.engine.poke()
                    else:
                        result = app.launch_action(body, bind=path.endswith('/bind'))
                    return self._json(200, result)
                except (RelationError, TypeError, ValueError) as exc:
                    raise ApiError(400, str(exc)) from None
            if method == "POST" and path == API['presence']:
                body = self._body()
                try:
                    app.report_presence(str(body.get("state")), float(body.get("idle_seconds") or 0))
                except ValueError as exc:
                    raise ApiError(400, str(exc)) from None
                return self._json(200, {"ok": True})
            if method == "POST" and path == API['settings_reset']:
                return self._json(200, app.reset_settings())
            if method == "GET" and path == API['stream']:
                return self._stream(int(query.get("since") or -1))
        raise ApiError(404, "not found")

    def _stream(self, since: int) -> None:
        """One JSON view per line whenever something changes, and a ping line
        every 25 s so the reader notices a dead collector."""
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        version = since
        last_write = 0.0
        try:
            while not self.server.stopping.is_set():  # type: ignore[attr-defined]
                current = self.app.wait_for_change(version, 25.0)
                if current != version:
                    time.sleep(max(0.0, 0.3 - (time.time() - last_write)))  # coalesce bursts
                    view = self._view()
                    version = view["version"]
                    self.wfile.write((json.dumps(view) + "\n").encode())
                else:
                    self.wfile.write(b'{"type":"ping"}\n')
                self.wfile.flush()
                last_write = time.time()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return


class LoginLimiter:
    def __init__(self, attempts: int = 5, window: float = 300.0):
        self.attempts = attempts
        self.window = window
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allowed(self, client: str) -> bool:
        with self._lock:
            now = time.time()
            recent = [t for t in self._failures.get(client, []) if now - t < self.window]
            self._failures[client] = recent
            return len(recent) < self.attempts

    def failed(self, client: str) -> None:
        with self._lock:
            self._failures.setdefault(client, []).append(time.time())

    def reset(self, client: str) -> None:
        with self._lock:
            self._failures.pop(client, None)


class LocalServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(self, path: str, handler: type):
        directory = Path(path).parent
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(directory, 0o700)
        if Path(path).exists():
            # Refuse to steal the socket from a running collector.
            probe = socket.socket(socket.AF_UNIX)
            try:
                probe.connect(path)
                raise RuntimeError(f"another collector is already listening on {path}")
            except (ConnectionRefusedError, FileNotFoundError):
                os.unlink(path)
            finally:
                probe.close()
        old_umask = os.umask(0o177)
        try:
            super().__init__(path, handler)
        finally:
            os.umask(old_umask)
        self.stopping = threading.Event()

    def server_close(self) -> None:
        super().server_close()
        try:
            os.unlink(self.server_address)
        except OSError:
            pass

    def get_request(self):  # UnixStreamServer gives "" as address; keep http.server happy
        request, _ = super().get_request()
        return request, ("local", 0)


class PhoneServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], handler: type):
        if getattr(handler, "desktop", False):
            import ipaddress
            if not ipaddress.IPv4Address(address[0]).is_loopback:
                raise ValueError("desktop listener must be loopback-only")
        super().__init__(address, handler)
        self.csrf = pysecrets.token_urlsafe(32)
        self.navigation = {"hash": "", "sequence": 0}
        self.navigation_lock = threading.Lock()
        self.stopping = threading.Event()
        self.login_limiter = LoginLimiter()


def make_handler(app: App, *, local: bool, desktop: bool = False) -> type:
    return type("BoundHandler", (Handler,), {"app": app, "local": local or desktop, "desktop": desktop})


def serve(server: socketserver.BaseServer) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, name=type(server).__name__, daemon=True)
    thread.start()
    return thread
