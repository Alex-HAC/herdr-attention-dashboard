"""v0.9.8.7: the dashboard loads faster.

Measured 19 Nov 21:37 on the laptop: /api/view was 3.9 MB and took 3.3 s to build (700 subagent
cards, load average 16), polled every 10 s; nothing was gzipped and every file was no-store.
"""
import gzip
import http.client
import json
import re
import time
import unittest

from herdr_attention import api
from herdr_attention.api import PhoneServer, make_handler, serve
from tests.test_api import PASSPHRASE, make_collector


class Served(unittest.TestCase):
    def setUp(self):
        self.collector = make_collector()
        self.server = PhoneServer(("127.0.0.1", 0), make_handler(self.collector, local=False))
        self.port = self.server.server_address[1]
        serve(self.server)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.stopping.set)

    def get(self, path, gz=True, cookie=None, method="GET", body=None, csrf=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Origin": f"http://127.0.0.1:{self.port}"}
        if gz:
            headers["Accept-Encoding"] = "gzip, deflate, br"
        if cookie:
            headers["Cookie"] = cookie
        if csrf:
            headers["X-CSRF-Token"] = csrf
        if body is not None:
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        r = conn.getresponse()
        raw = r.read()
        conn.close()
        hdrs = {k.lower(): v for k, v in r.getheaders()}
        if hdrs.get("content-encoding") == "gzip":
            raw = gzip.decompress(raw)
        return r.status, hdrs, raw

    def login(self):
        status, headers, raw = self.get("/login", method="POST", body={"passphrase": PASSPHRASE})
        return headers["set-cookie"].split(";")[0], json.loads(raw)["csrf"]

    def test_index_names_versioned_files_and_stays_no_store(self):
        status, headers, raw = self.get("/")
        page = raw.decode()
        self.assertEqual(headers["cache-control"], "no-store")
        for name in ("app.js", "graph.js", "terminal.js", "ui-contract.js", "style.css", "InterVariable.woff2"):
            self.assertRegex(page, rf'"{re.escape(name)}\?v=[0-9a-f]{{12}}"', name)
        self.assertIn('rel="preload"', page)

    def test_versioned_files_are_immutable_and_gzipped(self):
        page = self.get("/")[2].decode()
        js = re.search(r'"(app\.js\?v=[0-9a-f]+)"', page).group(1)
        status, headers, raw = self.get("/" + js)
        self.assertEqual(status, 200)
        self.assertIn("immutable", headers["cache-control"])
        self.assertEqual(headers["content-encoding"], "gzip")
        self.assertEqual(headers["vary"], "Accept-Encoding")
        self.assertEqual(raw, (api.WEB_DIR / "app.js").read_bytes())
        # An old or missing version is never kept.
        self.assertEqual(self.get("/app.js?v=000000000000")[1]["cache-control"], "no-store")
        self.assertEqual(self.get("/app.js")[1]["cache-control"], "no-store")
        # Without gzip support: the plain file.
        plain = self.get("/" + js, gz=False)
        self.assertNotIn("content-encoding", plain[1])
        self.assertEqual(plain[2], (api.WEB_DIR / "app.js").read_bytes())

    def test_stylesheet_names_the_versioned_font(self):
        css = self.get("/style.css")[2].decode()
        self.assertRegex(css, r'url\("InterVariable\.woff2\?v=[0-9a-f]{12}"\)')
        font = self.get("/InterVariable.woff2?v=" + api._asset("InterVariable.woff2")[2])
        self.assertNotIn("content-encoding", font[1])   # already compressed
        self.assertIn("immutable", font[1]["cache-control"])
        self.assertLess(len(font[2]), 200_000, "the subset font")

    def test_view_is_gzipped_no_store_and_has_no_server_only_fields(self):
        cookie, _ = self.login()
        status, headers, raw = self.get("/api/view", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(headers["cache-control"], "no-store")
        view = json.loads(raw)
        self.assertTrue(view["agents"])
        for a in view["agents"]:
            for key in api.VIEW_DROP:
                self.assertNotIn(key, a)
        self.assertIn("last_assistant", self.collector.view(local=False)["agents"][0], "the server keeps it")


class SharedView(unittest.TestCase):
    def setUp(self):
        self.collector = make_collector()

    def test_readers_share_a_recent_build_until_something_changes(self):
        c = self.collector
        first = c.view(local=False, cached=True)
        self.assertIs(c.view(local=False, cached=True), first)
        self.assertIsNot(c.view(local=False), first, "uncached readers always get their own")
        c.invalidate_view()
        second = c.view(local=False, cached=True)
        self.assertIsNot(second, first)
        # After the fresh window: reused only while the engine version is the same.
        c._view_cache[False] = (time.monotonic() - c.VIEW_FRESH - 1, c.engine.version, second)
        self.assertIs(c.view(local=False, cached=True), second)
        c._view_cache[False] = (time.monotonic() - c.VIEW_FRESH - 1, c.engine.version - 1, second)
        self.assertIsNot(c.view(local=False, cached=True), second)
        c._view_cache[False] = (time.monotonic() - c.VIEW_TTL - 1, c.engine.version, second)
        self.assertIsNot(c.view(local=False, cached=True), second)

    def test_local_and_phone_views_are_kept_apart(self):
        self.assertIsNot(self.collector.view(local=True, cached=True), self.collector.view(local=False, cached=True))


class SidebarBuildsOnlyWhenDrawn(unittest.TestCase):
    def test_passive_or_off_never_builds_the_view(self):
        from herdr_attention.sidebar import Sidebar
        calls = []
        sidebar = Sidebar.__new__(Sidebar)
        sidebar.disabled, sidebar.passive = False, True
        sidebar.update(lambda: calls.append(1) or {}, enabled=True)
        sidebar.disabled, sidebar.passive = True, False
        sidebar.update(lambda: calls.append(1) or {}, enabled=False)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
