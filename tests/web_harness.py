"""Load the real phone page in headless Chromium with the API stubbed.

Used by tests/test_web.py and scripts/web_screens.py. The page, its CSS and
its JS are the shipped files; only fetch() is replaced by a stub that answers
from a fixture dict. A throwaway browser profile is used every time.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

WEB = Path(__file__).parent.parent / "herdr_attention" / "web"
BROWSER = shutil.which("chromium") or shutil.which("google-chrome-stable")

STUB = """
window.__calls = [];
window.fetch = async (path, init) => {
  path = String(path);
  const method = (init && init.method) || "GET";
  window.__calls.push([path, method]);
  if (method === "POST") (window.__posts = window.__posts || []).push([new URL(path, "http://x").pathname, init && init.body ? JSON.parse(init.body) : null]);
  const url = new URL(path, "http://x");
  let body = {ok: true};
  if (method === "POST" && url.pathname.endsWith("/ack") && FIXTURE.ackFails)
    return {ok: false, status: 500, json: async () => ({error: "database is locked"})};
  if (FIXTURE.loginFirst && !window.__loggedIn && url.pathname.startsWith("/api/"))
    return {ok: false, status: 401, json: async () => ({error: "login required"})};
  if (url.pathname === "/login") { window.__loggedIn = true; return {ok: true, status: 200, json: async () => ({csrf: "t"})}; }
  const notes = FIXTURE.notifications || [];
  const unread = () => notes.filter((n) => !n.read).length;
  const ack = url.pathname.match(/^[/]api[/]events[/]([0-9]+)[/]ack$/);
  if (method === "POST" && ack) notes.filter((n) => String(n.id) === ack[1]).forEach((n) => { n.read = true; });
  if (method === "POST" && url.pathname === "/api/notifications/read-all") notes.forEach((n) => { n.read = true; });
  if (FIXTURE.view && FIXTURE.view.notifications) FIXTURE.view.notifications.unread = unread();
  const one = url.pathname.match(/^[/]api[/]notifications[/]([0-9]+)$/);
  if (one) {
    const item = notes.find((n) => String(n.id) === one[1]);
    return item ? {ok: true, status: 200, json: async () => item} : {ok: false, status: 404, json: async () => ({error: "no such notification"})};
  }
  if (url.pathname === "/api/notifications") return {ok: true, status: 200, json: async () => ({items: notes, unread: unread()})};
  if (url.pathname === "/api/session") body = {csrf: "t"};
  else if (url.pathname === "/api/view") body = FIXTURE.view;
  else if (url.pathname === "/api/chooser") body = {workspaces: FIXTURE.chooser};
  else if (url.pathname === "/api/workspaces/refresh") body = FIXTURE.workspacesRefresh ||
    {ok: true, workspaces: FIXTURE.chooser, count: (FIXTURE.chooser || []).length, new: []};
  else if (url.pathname === "/api/agent") body = FIXTURE.details[url.searchParams.get("identity")] || null;
  else if (url.pathname === "/api/visuals") body = FIXTURE.visuals || {items:[],desktop:false,unseen:0};
  else if (url.pathname === "/api/visuals/seen") body = {ok:true,marked:(FIXTURE.visuals?.items||[]).length,unseen:0};
  else if (url.pathname === "/api/output") body = FIXTURE.output;
  else if (url.pathname === "/api/terminal") { window.__term = (window.__term || 0) + 1; body = typeof FIXTURE.terminal === "function" ? FIXTURE.terminal() : FIXTURE.terminal; }
  else if (url.pathname === "/api/conversation") body = FIXTURE.conversation;
  else if (url.pathname === "/api/file") {
    (window.__files = window.__files || []).push(url.search);
    const key = url.searchParams.get("path") + (url.searchParams.get("against") ? "|" + url.searchParams.get("against") : "");
    const f = (FIXTURE.files || {})[key];
    if (!f || f.error) return {ok: false, status: f ? f.status : 404, json: async () => ({error: f ? f.error : "file not found"})};
    body = f;
  }
  else if (url.pathname === "/api/notify/setup") body = FIXTURE.notifySetup || {configured: false};
  if (body === null) return {ok: false, status: 404, json: async () => ({error: "no such agent"})};
  return {ok: true, status: 200, json: async () => body};
};
"""

# Runs after the page settles: records layout facts the tests check.
PROBE = """
setTimeout(() => {
  if (window.__probed) return;
  const doc = document.documentElement;
  // Shapes inside the pannable graph canvas may extend past the edge; the page may not.
  const wide = [...document.querySelectorAll("main *")].filter(el => !el.closest("svg.graph")).filter(el => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.right > window.innerWidth + 1;
  }).map(el => el.className || el.tagName).slice(0, 5);
  const small = [...document.querySelectorAll("main a, main button, .topbar a")].filter(el => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && r.height < 30;
  }).map(el => (el.textContent || el.className).trim().slice(0, 30)).slice(0, 8);
  const target = window.parent !== window ? window.parent.document.body : document.body;
  window.__probed = true;
  target.dataset.probe = JSON.stringify({
    innerWidth: window.innerWidth, scrollWidth: doc.scrollWidth, overflowing: wide, smallTargets: small,
    calls: window.__calls, title: document.title, hash: location.hash,
    active: document.activeElement ? (document.activeElement.getAttribute("data-id") || document.activeElement.tagName) : null,
  });
}, PROBE_AT);
"""


def build_page(directory: Path, fixture: dict, script: str | None = None) -> Path:
    html = (WEB / "index.html").read_text()
    html = html.replace('<script src="ui-contract.js"></script>', f'<script src="{(WEB / "ui-contract.js").as_uri()}"></script>')
    html = html.replace('href="style.css"', f'href="{(WEB / "style.css").as_uri()}"')
    html = html.replace('<script src="graph.js"></script>', f'<script src="{(WEB / "graph.js").as_uri()}"></script>')
    html = html.replace('<script src="terminal.js"></script>', f'<script src="{(WEB / "terminal.js").as_uri()}"></script>')
    html = html.replace('<script src="details.js"></script>', f'<script src="{(WEB / "details.js").as_uri()}"></script>')
    html = html.replace(
        '<script src="app.js"></script>',
        f"<script>const FIXTURE = {json.dumps(fixture)};{STUB}</script>"
        f'<script src="{(WEB / "app.js").as_uri()}"></script>'
        + (f"<script>setTimeout(async () => {{ {script} }}, 700);</script>" if script else "")
        + f"<script>{PROBE.replace('PROBE_AT', '2600' if script else '1200')}</script>",
    )
    page = directory / "page.html"
    page.write_text(html)
    return page


def run(fixture: dict, route: str = "", width: int = 390, height: int = 900,
        screenshot: Path | None = None, script: str | None = None, theme: str = "light") -> dict:
    """``script`` runs in the page after load (simulated taps, keys)."""
    """Render once. Returns {"dom": str, "probe": dict}."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        page = build_page(tmp, fixture, script)
        url = page.as_uri() + (f"#{route}" if route else "")
        # Chromium will not make a window narrower than 500 px, so the layout
        # probe runs inside an iframe of the exact width.
        frame = tmp / "frame.html"
        frame.write_text(f'<!doctype html><body style="margin:0"><iframe src="{url}" width="{width}" '
                         f'height="{height}" style="border:0"></iframe></body>')
        base = [BROWSER, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                "--hide-scrollbars", f"--user-data-dir={tmp / 'profile'}", "--allow-file-access-from-files",
                "--virtual-time-budget=6000"]
        if theme == "dark":
            base.append("--force-dark-mode")
        outer = subprocess.run(base + [f"--window-size={max(width, 800)},{height}", "--dump-dom", frame.as_uri()],
                               capture_output=True, text=True, timeout=90).stdout
        dom = subprocess.run(base + [f"--window-size={width},{height}", "--dump-dom", url],
                             capture_output=True, text=True, timeout=90).stdout
        if screenshot:
            subprocess.run(base + [f"--window-size={width},{height}", f"--screenshot={screenshot}", url],
                           capture_output=True, timeout=90)
    probe = {}
    marker = 'data-probe="'
    if marker in outer:
        raw = outer.split(marker, 1)[1].split('"', 1)[0]
        probe = json.loads(raw.replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">"))
    return {"dom": dom, "probe": probe}
