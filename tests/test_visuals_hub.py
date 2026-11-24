"""v0.9.9 global visuals: seen state, safe download, hub feed, go-to-agent identity."""
import http.client
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from herdr_attention.api import PhoneServer, make_handler, serve
from herdr_attention.store import Store, SCHEMA_VERSION
from herdr_attention.ui_contract import hash_path, api_path
from herdr_attention.visuals import placeholder_jpeg_bytes
from herdr_attention.notify_prefs import enabled
from herdr_attention.config import DEFAULTS, Settings
from tests.helpers import settings
from tests.test_api import make_collector, PASSPHRASE
from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER, run
from tests.test_typing_survives_refresh import reading


class SeenState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "work"
        self.root.mkdir()
        (self.root / "shot.png").write_bytes(b"png-bytes")
        self.collector = make_collector()
        self.addCleanup(self.collector.store.close)
        self.collector.engine.agents["claude:a"]["cwd"] = str(self.root)
        self.view = self.collector.view(local=False)

    def test_new_items_are_unseen_until_marked(self):
        result = self.collector.latest_visuals(identity="claude:a")
        item, = result["items"]
        self.assertFalse(item["seen"])
        self.assertEqual(result["unseen"], 1)
        self.assertTrue(item["download"].endswith("/download"))
        self.assertEqual(item["identity"], "claude:a")
        self.assertEqual(item.get("pane_id"), self.view["agents"][0].get("pane_id") or item.get("pane_id"))
        marked = self.collector.mark_visuals_seen([item["id"]])
        self.assertEqual(marked["unseen"], 0)
        again = self.collector.latest_visuals(identity="claude:a")
        self.assertTrue(again["items"][0]["seen"])
        self.assertEqual(again["unseen"], 0)

    def test_mark_all_covers_current_and_older(self):
        result = self.collector.latest_visuals(identity="claude:a")
        self.assertEqual(result["unseen"], 1)
        self.collector.mark_visuals_seen(all=True)
        self.assertEqual(self.collector.latest_visuals(identity="claude:a")["unseen"], 0)
        snapshot = self.collector.visuals.snapshot(self.collector.view(local=False))
        self.assertEqual(snapshot["unseen"], 0)

    def test_go_to_agent_hash_uses_the_producing_agent(self):
        item, = self.collector.latest_visuals(identity="claude:a")["items"]
        self.assertEqual(hash_path("agent", item["identity"]), "#/a/" + item["identity"].replace(":", "%3A"))
        self.assertTrue(hash_path("agent", item["identity"]).startswith("#/a/"))
        self.assertTrue(item["identity"].startswith("claude:"))


class DownloadSafety(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "work"
        self.root.mkdir()
        (self.root / "frame.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
        self.collector = make_collector()
        self.addCleanup(self.collector.store.close)
        self.collector.engine.agents["claude:a"]["cwd"] = str(self.root)
        self.server = PhoneServer(("127.0.0.1", 0), make_handler(self.collector, local=False))
        serve(self.server)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]

    def request(self, path, method="GET", body=None, cookie=None, csrf=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Origin": f"http://127.0.0.1:{self.port}", "Content-Type": "application/json"}
        if cookie:
            headers["Cookie"] = cookie
        if csrf:
            headers["X-CSRF-Token"] = csrf
        conn.request(method, path, json.dumps(body) if body is not None else None, headers)
        res = conn.getresponse()
        data, status, head = res.read(), res.status, dict(res.getheaders())
        conn.close()
        return status, head, data

    def login(self):
        _, headers, raw = self.request("/login", "POST", {"passphrase": PASSPHRASE})
        cookie = headers["Set-Cookie"].split(";")[0]
        csrf = json.loads(raw)["csrf"]
        return cookie, csrf

    def test_download_needs_login_and_uses_attachment(self):
        status, _, _ = self.request("/api/visuals/" + "a" * 32 + "/download")
        self.assertEqual(status, 401)
        cookie, csrf = self.login()
        status, _, raw = self.request("/api/visuals?identity=claude:a", cookie=cookie)
        self.assertEqual(status, 200)
        item, = json.loads(raw)["items"]
        self.assertIn("/download", item["download"])
        status, headers, body = self.request(item["download"], cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertIn("filename=", headers["Content-Disposition"])
        self.assertIn("frame.svg", headers["Content-Disposition"])
        self.assertIn(b"<svg", body)
        # A path query must not bypass discovery: only the id is accepted.
        status, _, _ = self.request(item["download"] + "?path=/etc/passwd", cookie=cookie)
        self.assertEqual(status, 404)
        # Unknown ids are 404, not a file read.
        status, _, _ = self.request("/api/visuals/" + "b" * 32 + "/download", cookie=cookie)
        self.assertEqual(status, 404)

    def test_seen_endpoint_syncs_unseen(self):
        cookie, csrf = self.login()
        status, _, raw = self.request("/api/visuals?identity=claude:a", cookie=cookie)
        item, = json.loads(raw)["items"]
        self.assertFalse(item["seen"])
        status, _, raw = self.request("/api/visuals/seen", "POST", {"ids": [item["id"]]}, cookie=cookie)
        self.assertEqual(status, 403)  # CSRF required
        status, _, raw = self.request("/api/visuals/seen", "POST", {"ids": [item["id"]]}, cookie=cookie, csrf=csrf)
        self.assertEqual(status, 200)
        payload = json.loads(raw)
        self.assertEqual(payload["unseen"], 0)
        status, _, raw = self.request("/api/visuals?identity=claude:a", cookie=cookie)
        self.assertTrue(json.loads(raw)["items"][0]["seen"])


class SchemaAndPrefs(unittest.TestCase):
    def test_existing_database_gains_visuals_seen(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "state.db"
        db = sqlite3.connect(path)
        db.executescript("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT); CREATE TABLE agents (identity TEXT PRIMARY KEY, data TEXT, updated_at REAL);"
                         "INSERT INTO meta VALUES ('schema_version', '2');")
        db.close()
        store = Store(path)
        self.addCleanup(store.close)
        self.assertEqual(int(store.get_meta("schema_version")), SCHEMA_VERSION)
        store.mark_visuals_seen(["a" * 32], 1)
        ids, watermark = store.visuals_seen_state()
        self.assertIn("a" * 32, ids)
        self.assertEqual(watermark, 0)
        store.mark_visuals_seen_all(9, ["b" * 32])
        ids, watermark = store.visuals_seen_state()
        self.assertEqual(watermark, 9)
        self.assertIn("b" * 32, ids)

    def test_phone_visuals_kind_is_off_by_default(self):
        self.assertFalse(enabled(Settings(DEFAULTS), "visuals"))
        self.assertEqual(hash_path("visuals"), "#/visuals")
        self.assertTrue(api_path("visual_download", "a" * 32).endswith("/download"))


@unittest.skipUnless(BROWSER, "no Chromium")
class HubUi(unittest.TestCase):
    def test_topbar_and_hub_and_viewer_at_390(self):
        f = page_fixture()
        identity = f["view"]["agents"][0]["identity"]
        item = {"id": "a" * 32, "name": "screen.svg", "path": "/work/screen.svg", "relative_path": "screen.svg",
                "identity": identity, "agent": "builder", "at": 100, "video": False, "source": "trace",
                "url": "data:image/svg+xml,<svg/>", "thumbnail": "data:image/svg+xml,<svg/>",
                "download": "/api/visuals/" + "a" * 32 + "/download", "seen": False,
                "workspace_label": "Demo", "pane_id": "w1:p1", "size": 12}
        f["visuals"] = {"items": [item], "unseen": 1, "desktop": True}
        f["view"]["visuals"] = {"unseen": 1, "items": [item]}
        result = reading(run(f, "/visuals", 390, 900, script="""
          const entry=document.querySelector('.visuals-entry');
          const badge=document.querySelector('#visuals-badge');
          document.body.dataset.reading=JSON.stringify({
            title:document.querySelector('h1')?.textContent,
            entry:!!entry, href:entry?.getAttribute('href'),
            badge:badge && !badge.hidden ? badge.textContent : '',
            tiles:document.querySelectorAll('.visuals-hub .visual-tile').length
          });
        """))
        self.assertEqual(result["title"], "Visuals")
        self.assertTrue(result["entry"])
        self.assertEqual(result["href"], "#/visuals")
        # The hub loads from /api/visuals (stubbed FIXTURE.visuals), so a tile is present.
        self.assertEqual(result["tiles"], 1)

    def test_viewer_has_download_and_go_to_the_producing_agent(self):
        f = page_fixture()
        identity = f["view"]["agents"][0]["identity"]
        f["visuals"] = {"desktop": True, "items": [{"id": "a" * 32, "name": "screen.svg", "path": "/work/screen.svg",
          "relative_path": "screen.svg", "identity": identity, "agent": "builder", "at": 100, "video": False,
          "source": "trace", "url": "data:image/svg+xml,<svg/>", "thumbnail": "data:image/svg+xml,<svg/>",
          "download": "/api/visuals/" + "a" * 32 + "/download", "seen": False}]}
        result = reading(run(f, "/a/" + identity + "/activity", 390, 900, script="""
          document.querySelector('.latest-visuals').open=true;await new Promise(r=>setTimeout(r,300));
          document.querySelector('.visual-tile').click();
          const dialog=document.querySelector('#visual-viewer');
          const go=[...dialog.querySelectorAll('a')].find(a=>a.textContent==='Go to agent');
          const dl=[...dialog.querySelectorAll('a')].find(a=>a.textContent==='Download');
          document.body.dataset.reading=JSON.stringify({
            open:!!dialog?.open, go:go?.getAttribute('href'), download:dl?.getAttribute('href'),
            filename:dl?.getAttribute('download')
          });
        """))
        self.assertTrue(result["open"])
        self.assertEqual(result["go"], hash_path("agent", identity))
        self.assertTrue(result["download"].endswith("/download"))
        self.assertEqual(result["filename"], "screen.svg")


class ThumbnailsAndCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "work"
        self.root.mkdir()
        (self.root / "shot.png").write_bytes(b"original-png-bytes-ok-as-fallback")
        self.collector = make_collector()
        self.addCleanup(self.collector.store.close)
        self.collector.engine.agents["claude:a"]["cwd"] = str(self.root)
        # Discover so records exist, without depending on live ffmpeg.
        self.collector.latest_visuals(identity="claude:a")

    def test_failed_preview_falls_back_to_the_original(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        item, = self.collector.latest_visuals(identity="claude:a")["items"]
        with patch("herdr_attention.visuals.subprocess.run", return_value=SimpleNamespace(returncode=1)):
            file, mime, size = self.collector.visual_file(item["id"], True)
            with file:
                data = file.read()
        self.assertEqual(data, b"original-png-bytes-ok-as-fallback")
        self.assertEqual(mime, "image/png")
        self.assertEqual(size, len(data))

    def test_thumbnail_has_long_cache_and_etag(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        item, = self.collector.latest_visuals(identity="claude:a")["items"]
        def ffmpeg(command, **kwargs):
            Path(command[-1]).write_bytes(placeholder_jpeg_bytes())
            return SimpleNamespace(returncode=0)
        self.server = PhoneServer(("127.0.0.1", 0), make_handler(self.collector, local=False))
        serve(self.server)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        port = self.server.server_address[1]
        cookie, csrf = _login(port)
        with patch("herdr_attention.visuals.subprocess.run", side_effect=ffmpeg):
            status, headers, body = _request(port, item["thumbnail"] or f"/api/visuals/{item['id']}/thumbnail", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn("max-age", (headers.get("Cache-Control") or "").lower())
        self.assertNotIn("no-store", (headers.get("Cache-Control") or "").lower())
        self.assertTrue(headers.get("ETag"))
        status, headers2, _ = _request(port, item["thumbnail"] or f"/api/visuals/{item['id']}/thumbnail", cookie=cookie,
                                       extra={"If-None-Match": headers["ETag"]})
        self.assertEqual(status, 304)

    def test_thumbnail_reads_reuse_a_cached_view(self):
        item, = self.collector.latest_visuals(identity="claude:a")["items"]
        builds = {"n": 0}
        original = self.collector._build_view
        def counted(*, local):
            builds["n"] += 1
            return original(local=local)
        self.collector._build_view = counted
        # Prime the cache once, then several thumbnail reads must not rebuild.
        self.collector.view(local=False, cached=True)
        start = builds["n"]
        from unittest.mock import patch
        from types import SimpleNamespace
        with patch("herdr_attention.visuals.subprocess.run", return_value=SimpleNamespace(returncode=1)):
            for _ in range(6):
                file, mime, size = self.collector.visual_file(item["id"], True)
                file.close()
        self.assertEqual(builds["n"], start)


def _login(port):
    status, headers, raw = _request(port, "/login", "POST", {"passphrase": PASSPHRASE})
    return headers["Set-Cookie"].split(";")[0], json.loads(raw)["csrf"]


def _request(port, path, method="GET", body=None, cookie=None, csrf=None, extra=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Origin": f"http://127.0.0.1:{port}", "Content-Type": "application/json"}
    if cookie:
        headers["Cookie"] = cookie
    if csrf:
        headers["X-CSRF-Token"] = csrf
    if extra:
        headers.update(extra)
    conn.request(method, path, json.dumps(body) if body is not None else None, headers)
    res = conn.getresponse()
    data, status, head = res.read(), res.status, {k: v for k, v in res.getheaders()}
    conn.close()
    return status, head, data


@unittest.skipUnless(BROWSER, "no Chromium")
class LazyHubAndPan(unittest.TestCase):
    def _fixture(self, n=8):
        f = page_fixture()
        identity = f["view"]["agents"][0]["identity"]
        items = []
        for i in range(n):
            items.append({
                "id": f"{i:032x}", "name": f"shot-{i}.png", "path": f"/work/shot-{i}.png",
                "relative_path": f"shot-{i}.png", "identity": identity, "agent": "builder",
                "at": 1000 - i, "video": False, "source": "trace",
                "url": "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' width='800' height='600'/>",
                "thumbnail": "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' width='80' height='60'/>",
                "download": f"/api/visuals/{i:032x}/download", "seen": False,
                "workspace_label": "Demo", "pane_id": "w1:p1", "size": 12,
            })
        f["visuals"] = {"items": items, "unseen": n, "desktop": True, "next": 1}
        f["view"]["visuals"] = {"unseen": n, "items": items[:5]}
        return f, identity, items

    def test_hub_uses_skeletons_and_lazy_thumbs_not_full_images(self):
        f, identity, items = self._fixture(8)
        result = reading(run(f, "/visuals", 390, 900, script="""
          const tiles=[...document.querySelectorAll('.visuals-hub .visual-tile')];
          const thumbs=[...document.querySelectorAll('.visuals-hub img[data-thumb]')];
          const full=[...document.querySelectorAll('.visuals-hub img')].filter(i => (i.getAttribute('src')||'').includes('/file'));
          document.body.dataset.reading=JSON.stringify({
            tiles:tiles.length,
            thumbs:thumbs.length,
            skeletons:document.querySelectorAll('.visual-skeleton').length + document.querySelectorAll('.visual-thumb-wrap').length,
            wrap:document.querySelectorAll('.visual-thumb-wrap').length,
            full:full.length,
            dataThumbs:thumbs.map(i=>i.getAttribute('data-thumb')).slice(0,2)
          });
        """))
        self.assertGreaterEqual(result["tiles"], 1)
        self.assertGreaterEqual(result["wrap"], 1)
        self.assertEqual(result["full"], 0)
        self.assertTrue(all("/thumbnail" in (u or "") or (u or "").startswith("data:") for u in result["dataThumbs"]))

    def test_viewer_zooms_and_pans_with_pointer_and_swipes_only_when_not_zoomed(self):
        f, identity, items = self._fixture(3)
        result = reading(run(f, "/visuals", 390, 900, script="""
          const wait=ms=>new Promise(r=>setTimeout(r,ms));
          const fire=(el,type,x,y,extra={})=>{
            el.dispatchEvent(new PointerEvent(type,{bubbles:true,cancelable:true,pointerId:1,pointerType:'touch',
              clientX:x,clientY:y,buttons:type==='pointerup'?0:1,...extra}));
          };
          await wait(200);
          const first=document.querySelector('.visuals-hub .visual-tile');
          first.click();
          await wait(200);
          const dialog=document.querySelector('#visual-viewer');
          const stage=dialog.querySelector('.visual-stage');
          const img=stage.querySelector('img');
          const title0=dialog.querySelector('.visual-viewer-head b')?.textContent;
          const box=stage.getBoundingClientRect();
          const cx=box.left+box.width/2, cy=box.top+box.height/2;
          // double-tap to zoom
          fire(stage,'pointerdown',cx,cy); fire(stage,'pointerup',cx,cy);
          await wait(40);
          fire(stage,'pointerdown',cx,cy); fire(stage,'pointerup',cx,cy);
          await wait(80);
          const zoomed=img.style.transform||'';
          const scale1=/scale\(([^)]+)\)/.exec(zoomed);
          // drag to pan
          fire(stage,'pointerdown',cx,cy);
          fire(stage,'pointermove',cx-80,cy);
          fire(stage,'pointerup',cx-80,cy);
          await wait(40);
          const panned=img.style.transform||'';
          const titleZoomed=dialog.querySelector('.visual-viewer-head b')?.textContent;
          // reset zoom and swipe to next
          document.dispatchEvent(new KeyboardEvent('keydown',{key:'0',bubbles:true}));
          await wait(40);
          fire(stage,'pointerdown',cx+40,cy);
          fire(stage,'pointermove',cx-80,cy);
          fire(stage,'pointerup',cx-80,cy);
          await wait(80);
          const titleAfter=dialog.querySelector('.visual-viewer-head b')?.textContent;
          document.body.dataset.reading=JSON.stringify({
            open:!!dialog?.open, title0, titleZoomed, titleAfter, zoomed, panned,
            scale: scale1 ? Number(scale1[1]) : 0,
            touchAction: getComputedStyle(stage).touchAction
          });
        """))
        self.assertTrue(result["open"])
        self.assertGreaterEqual(result["scale"], 1.5)
        self.assertIn("translate(", result["panned"])
        self.assertEqual(result["titleZoomed"], result["title0"], "swipe while zoomed must not change the image")
        self.assertNotEqual(result["titleAfter"], result["title0"], "swipe when not zoomed goes to the next image")
        self.assertEqual(result["touchAction"], "none")
