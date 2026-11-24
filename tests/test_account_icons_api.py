"""v0.9.7 account images: upload, serve and delete with every safety rule of the contract."""
import http.client
import json
import tempfile
import unittest
from pathlib import Path

from herdr_attention.api import PhoneServer, make_handler, serve
from tests.icon_fixture import tiny_png
from tests import test_api
from tests.test_api import make_collector
from tests.test_quotas import ORDER, TZ, USAGE

PNG = tiny_png()
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 200
WEBP = b"RIFF\x20\x00\x00\x00WEBPVP8 " + b"\x00" * 200
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"></svg>'


class AccountImageRoutes(unittest.TestCase):
    request = test_api.PhoneApi.request
    login = test_api.PhoneApi.login

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "icons"
        self.collector = make_collector(phone={"cookie_secure": False},
                                        quotas={"usage_dir": str(USAGE), "order_path": str(ORDER), "timezone": TZ},
                                        accounts={"icons": {}, "image_dir": str(self.dir)})
        self.server = PhoneServer(("127.0.0.1", 0), make_handler(self.collector, local=False))
        self.port = self.server.server_address[1]
        self.thread = serve(self.server)
        self.origin = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.server.stopping.set()
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def raw(self, method, account, data=b"", cookie=None, csrf=None, ctype="image/png", origin=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Content-Type": ctype}
        if cookie:
            headers["Cookie"] = cookie
        if csrf:
            headers["X-CSRF-Token"] = csrf
        if origin:
            headers["Origin"] = self.origin
        conn.request(method, f"/api/account-icons/{account}", body=data or None, headers=headers)
        response = conn.getresponse()
        body = response.read()
        conn.close()
        return response.status, dict(response.getheaders()), body

    def icon(self, account):
        view = self.collector.view(local=False)
        return next(a["icon"] for a in view["quotas"]["accounts"] if a["id"] == account)

    def test_upload_needs_login_and_csrf(self):
        self.assertEqual(self.raw("POST", "cursor", PNG)[0], 401)
        cookie, csrf = self.login()
        self.assertEqual(self.raw("POST", "cursor", PNG, cookie=cookie)[0], 403)
        self.assertEqual(self.raw("POST", "cursor", PNG, cookie=cookie, csrf="wrong")[0], 403)
        self.assertEqual(self.raw("DELETE", "cursor", cookie=cookie)[0], 403)
        self.assertEqual(self.raw("GET", "cursor")[0], 401)
        self.assertFalse(self.dir.exists() and any(self.dir.iterdir()))

    def test_only_png_jpeg_and_webp_by_content_and_only_known_accounts(self):
        cookie, csrf = self.login()
        for data, ctype in ((SVG, "image/svg+xml"), (SVG, "image/png"), (b"<html>hi</html>", "image/png"), (b"GIF89a....", "image/gif")):
            status, _, body = self.raw("POST", "cursor", data, cookie, csrf, ctype)
            self.assertEqual(status, 400, (data[:10], ctype))
            self.assertIn("not a PNG, JPEG or WebP", json.loads(body)["error"])
        for account in ("unknown-account", "..%2F..%2Fetc%2Fpasswd", "%2Ftmp%2Fx"):
            self.assertEqual(self.raw("POST", account, PNG, cookie, csrf)[0], 404, account)
        for data in (JPEG, WEBP, PNG):
            self.assertEqual(self.raw("POST", "cursor", data, cookie, csrf, "application/octet-stream")[0], 200)
        self.assertEqual(len(list(self.dir.iterdir())), 1)
        name = next(self.dir.iterdir()).name
        self.assertRegex(name, r"^[0-9a-f]{32}$")                 # a hash, never a name from the client

    def test_size_cap(self):
        cookie, csrf = self.login()
        big = PNG + b"\x00" * (61440 - len(PNG) + 1)
        status, _, body = self.raw("POST", "cursor", big, cookie, csrf)
        self.assertEqual(status, 400)
        self.assertIn("too big", json.loads(body)["error"])
        self.assertEqual(self.raw("POST", "cursor", b"\x89PNG\r\n\x1a\n" + b"\x00" * 70000, cookie, csrf)[0], 413)

    def test_upload_serve_and_delete(self):
        cookie, csrf = self.login()
        status, _, body = self.raw("POST", "cursor", PNG, cookie, csrf)
        self.assertEqual(status, 200, body)
        version = json.loads(body)["version"]
        self.assertEqual(self.collector.settings.account_icons()["cursor"], {"image": True})
        icon = self.icon("cursor")
        self.assertEqual((icon["image"], icon["custom"]), (f"/api/account-icons/cursor?v={version}", True))
        status, headers, served = self.raw("GET", "cursor?v=" + version, cookie=cookie)
        self.assertEqual((status, served), (200, PNG))
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("sandbox", headers["Content-Security-Policy"])
        # Delete keeps other choices, drops the image.
        self.collector.update_settings({"accounts.icons": {"cursor": {"image": True, "color": "red"}}}, local=False)
        status, _, body = self.raw("DELETE", "cursor", cookie=cookie, csrf=csrf)
        self.assertEqual((status, json.loads(body)["removed"]), (200, True))
        self.assertEqual(self.collector.settings.account_icons()["cursor"], {"color": "red"})
        self.assertIsNone(self.icon("cursor")["image"])
        self.assertEqual(self.raw("GET", "cursor", cookie=cookie)[0], 404)
        self.assertFalse(any(self.dir.iterdir()))

    def test_a_file_that_is_not_an_image_is_never_served(self):
        cookie, csrf = self.login()
        self.assertEqual(self.raw("POST", "cursor", PNG, cookie, csrf)[0], 200)
        self.collector.account_images.path("cursor").write_bytes(SVG)       # changed on disk behind our back
        self.assertEqual(self.raw("GET", "cursor", cookie=cookie)[0], 404)


class LocalSettingsStillWork(unittest.TestCase):
    def test_the_view_has_icons_without_any_image(self):
        collector = make_collector(quotas={"usage_dir": str(USAGE), "order_path": str(ORDER), "timezone": TZ})
        icons = [a["icon"] for a in collector.view(local=True)["quotas"]["accounts"]]
        self.assertTrue(icons and all(i["image"] is None for i in icons))


if __name__ == "__main__":
    unittest.main()
