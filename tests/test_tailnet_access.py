"""v0.9.7.4 tailnet allow list: laptop-only API, enforcement on the phone listener, name resolution."""

import http.client
import json
import unittest

from herdr_attention.api import PhoneServer, make_handler, serve
from herdr_attention.store import Store
from herdr_attention.tailnet_access import TailnetAccess, resolve, nodes, validate_entry, validate_list
from tests.test_api import PASSPHRASE, make_collector

STATUS = {
    "Self": {"HostName": "omarchy", "DNSName": "omarchy.tail1234.ts.net.", "OS": "linux", "Online": True,
             "TailscaleIPs": ["100.64.0.1", "fd7a:115c:a1e0::1"]},
    "Peer": {
        "a": {"HostName": "Pixel 10a", "DNSName": "pixel-10a.tail1234.ts.net.", "OS": "android", "Online": True,
              "TailscaleIPs": ["100.64.0.10"]},
        "b": {"HostName": "nas", "DNSName": "nas.tail1234.ts.net.", "OS": "linux", "Online": False,
              "TailscaleIPs": ["100.64.0.20"]},
        "c": {"HostName": "cloud-vm", "DNSName": "cloud-vm-1.tail1234.ts.net.", "OS": "linux", "Online": False,
              "TailscaleIPs": ["100.64.0.30"]},
    },
}
PHONE, NAS, SELF = "100.64.0.10", "100.64.0.20", "100.64.0.1"


class Resolution(unittest.TestCase):
    def setUp(self):
        self.known = nodes(STATUS)

    def test_names_resolve_by_magicdns_label_full_name_and_host_name(self):
        for entry in ("pixel-10a", "pixel-10a.tail1234.ts.net", "PIXEL-10A.tail1234.ts.net.", "pixel 10a"):
            with self.subTest(entry=entry):
                self.assertEqual(resolve(entry.lower().rstrip("."), self.known), {PHONE})
        self.assertEqual(resolve("cloud-vm-1", self.known), {"100.64.0.30"})
        self.assertEqual(resolve(NAS, self.known), {NAS})
        self.assertEqual(resolve("not-there", self.known), set())

    def test_entries_are_checked_and_normalised(self):
        self.assertEqual(validate_entry(" Pixel-10a.tail1234.ts.net. "), "pixel-10a.tail1234.ts.net")
        self.assertEqual(validate_entry("100.64.0.10"), PHONE)
        for bad in ("8.8.8.8", "192.168.1.2", "", "a b;rm", 5, "x" * 300):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_entry(bad)
        self.assertEqual(validate_list(["nas", "NAS", PHONE]), ["nas", PHONE])

    def test_empty_list_allows_everyone_and_the_laptop_always_passes(self):
        access = TailnetAccess(Store(":memory:"), status=lambda: STATUS)
        self.assertTrue(access.allowed(NAS))
        access.set_entries(["pixel-10a"], True)
        self.assertTrue(access.allowed(PHONE))
        self.assertFalse(access.allowed(NAS))
        self.assertTrue(access.allowed(SELF))
        self.assertTrue(access.allowed(None))   # no X-Forwarded-For from loopback: this laptop
        self.assertFalse(access.allowed("invalid"))

    def test_the_list_survives_a_restart_and_names_follow_new_addresses(self):
        store = Store(":memory:")
        TailnetAccess(store, status=lambda: STATUS).set_entries(["nas"], True)
        moved = json.loads(json.dumps(STATUS))
        moved["Peer"]["b"]["TailscaleIPs"] = ["100.78.9.9"]
        again = TailnetAccess(store, status=lambda: moved)
        self.assertEqual(again.entries(), ["nas"])
        self.assertTrue(again.allowed("100.78.9.9"))
        self.assertFalse(again.allowed(NAS))

    def test_off_by_default_and_off_with_entries_is_unfiltered_and_keeps_the_list(self):
        store = Store(":memory:")
        access = TailnetAccess(store, status=lambda: STATUS)
        self.assertFalse(access.enabled())
        access.set_entries(["pixel-10a"])          # a list, but filtering still off
        self.assertTrue(access.allowed(NAS))
        self.assertFalse(access.overview()["enforced"])
        access.set_entries(enabled=True)
        self.assertFalse(access.allowed(NAS))
        access.set_entries(enabled=False)
        self.assertTrue(access.allowed(NAS))
        again = TailnetAccess(store, status=lambda: STATUS)
        self.assertEqual((again.entries(), again.enabled()), (["pixel-10a"], False))
        again.set_entries(enabled=True)               # turning it back on restores the saved list
        self.assertFalse(again.allowed(NAS))
        self.assertTrue(again.allowed(PHONE))
        with self.assertRaises(ValueError):
            again.set_entries(enabled="yes")

    def test_client_address_is_the_last_forwarded_hop(self):
        access = TailnetAccess(Store(":memory:"), status=lambda: STATUS)
        self.assertEqual(access.client_ip("127.0.0.1", f"{PHONE}, {NAS}"), NAS)
        self.assertIsNone(access.client_ip("127.0.0.1", None))
        self.assertEqual(access.client_ip("127.0.0.1", "garbage"), "invalid")

    def test_overview_marks_recent_sign_ins_and_listed_devices(self):
        access = TailnetAccess(Store(":memory:"), status=lambda: STATUS, clock=lambda: 1000.0)
        access.seen(PHONE)
        access.set_entries([NAS], True)
        o = access.overview()
        by = {p["name"]: p for p in o["peers"]}
        self.assertTrue(by["omarchy"]["self"])
        self.assertEqual(by["pixel-10a"]["signed_in_at"], 1000.0)
        self.assertTrue(by["nas"]["listed"])
        self.assertFalse(by["pixel-10a"]["listed"])
        self.assertEqual(o["recent"], [PHONE])
        self.assertEqual([p["name"] for p in o["peers"]][:2], ["omarchy", "pixel-10a"])


class Servers(unittest.TestCase):
    def setUp(self):
        self.collector = make_collector()
        self.collector.tailnet = TailnetAccess(self.collector.store, status=lambda: STATUS)
        self.phone = self.server(local=False)
        self.desktop = self.server(local=True, desktop=True)

    def server(self, **kw):
        server = PhoneServer(("127.0.0.1", 0), make_handler(self.collector, **kw))
        serve(server)
        def close():
            server.stopping.set(); server.shutdown(); server.server_close()
        self.addCleanup(close)
        return server

    def request(self, server, method, path, body=None, headers=None):
        host, port = server.server_address
        conn = http.client.HTTPConnection(host, port, timeout=5)
        h = {"Origin": f"http://{host}:{port}", **(headers or {})}
        if body is not None:
            h["Content-Type"] = "application/json"
        conn.request(method, path, json.dumps(body) if body is not None else None, h)
        r = conn.getresponse()
        raw = r.read()
        conn.close()
        return r.status, dict(r.getheaders()), raw

    def login(self, ip):
        status, headers, raw = self.request(self.phone, "POST", "/login", {"passphrase": PASSPHRASE}, {"X-Forwarded-For": ip})
        self.assertEqual(status, 200)
        return headers["Set-Cookie"].split(";")[0], json.loads(raw)["csrf"]

    def desk(self, method, body=None):
        return self.request(self.desktop, method, "/api/tailnet-access", body,
                            {"X-CSRF-Token": self.desktop.csrf})

    def test_the_allow_list_api_is_laptop_only(self):
        cookie, csrf = self.login(PHONE)
        for method, body in (("GET", None), ("POST", {"entries": [PHONE]})):
            status, _, raw = self.request(self.phone, method, "/api/tailnet-access", body,
                                          {"X-Forwarded-For": PHONE, "Cookie": cookie, "X-CSRF-Token": csrf})
            self.assertEqual(status, 403, method)
            self.assertNotIn(b"nas", raw)
        self.assertEqual(self.collector.tailnet.entries(), [])
        status, _, raw = self.desk("GET")
        self.assertEqual(status, 200)
        self.assertIn("pixel-10a", [p["name"] for p in json.loads(raw)["peers"]])
        self.assertEqual(self.desk("POST", {"entries": ["8.8.8.8"]})[0], 400)
        self.assertEqual(self.request(self.phone, "POST", "/api/tailnet-access", {"enabled": True},
                                      {"X-Forwarded-For": PHONE, "Cookie": cookie, "X-CSRF-Token": csrf})[0], 403)
        self.assertFalse(self.collector.tailnet.enabled())
        self.assertEqual(self.request(self.desktop, "POST", "/api/tailnet-access", {"entries": []})[0], 403)   # no CSRF

    def test_off_with_entries_changes_nothing_on_the_wire(self):
        self.assertEqual(self.desk("POST", {"entries": ["pixel-10a"], "enabled": False})[0], 200)
        self.assertEqual(self.request(self.phone, "GET", "/", headers={"X-Forwarded-For": NAS})[0], 200)
        self.assertEqual(self.desk("POST", {"enabled": True})[0], 200)
        self.assertEqual(self.request(self.phone, "GET", "/", headers={"X-Forwarded-For": NAS})[0], 403)

    def test_empty_list_changes_nothing(self):
        self.assertEqual(self.request(self.phone, "GET", "/", headers={"X-Forwarded-For": NAS})[0], 200)
        self.assertEqual(self.request(self.phone, "GET", "/api/view", headers={"X-Forwarded-For": NAS})[0], 401)

    def test_a_device_not_on_the_list_gets_a_bare_403_and_a_listed_one_passes(self):
        status, _, _ = self.desk("POST", {"entries": ["pixel-10a"], "enabled": True})
        self.assertEqual(status, 200)
        cookie, _ = self.login(PHONE)
        for path in ("/", "/app.js", "/api/view", "/api/health"):
            status, headers, raw = self.request(self.phone, "GET", path, headers={"X-Forwarded-For": NAS, "Cookie": cookie})
            self.assertEqual((status, raw), (403, b"forbidden\n"), path)
        self.assertEqual(self.request(self.phone, "POST", "/login", {"passphrase": PASSPHRASE}, {"X-Forwarded-For": NAS})[0], 403)
        status, _, raw = self.request(self.phone, "GET", "/api/view", headers={"X-Forwarded-For": PHONE, "Cookie": cookie})
        self.assertEqual(status, 200)
        self.assertIn(b"alpha", raw)
        # A spoofed earlier hop does not help: Serve's own (last) hop counts.
        self.assertEqual(self.request(self.phone, "GET", "/", headers={"X-Forwarded-For": f"{PHONE}, {NAS}"})[0], 403)
        # The laptop's own processes (no forwarded address) and its own tailnet address still pass.
        self.assertEqual(self.request(self.phone, "GET", "/")[0], 200)
        self.assertEqual(self.request(self.phone, "GET", "/", headers={"X-Forwarded-For": SELF})[0], 200)

    def test_a_signed_in_device_is_remembered_for_pre_selection(self):
        cookie, _ = self.login(PHONE)
        self.request(self.phone, "GET", "/api/view", headers={"X-Forwarded-For": PHONE, "Cookie": cookie})
        overview = json.loads(self.desk("GET")[2])
        self.assertIn(PHONE, overview["recent"])


if __name__ == "__main__":
    unittest.main()
