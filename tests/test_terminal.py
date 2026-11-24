"""v0.9.8 live terminal view: ANSI cleaning and redaction, throttled reads, endpoint auth."""

import json
import unittest

from herdr_attention.tailnet_access import TailnetAccess
from herdr_attention.terminal import TerminalReader, clean_ansi, plain, redact_ansi
from tests.test_tailnet_access import NAS, PHONE, STATUS, Servers as _Servers

E = "\x1b["


class Cleaning(unittest.TestCase):
    def test_only_colour_codes_survive(self):
        raw = (f"\x1b]0;evil title\x07\x1b]8;;https://x.example\x1b\\link\x1b]8;;\x1b\\ {E}2J{E}H{E}?25l"
               f"{E}38;2;1;2;3mok{E}0m\r\n\x07bell\x1b(B\x1b=x\x00\x9b")
        out = clean_ansi(raw)
        self.assertEqual(out, f"link {E}38;2;1;2;3mok{E}0m\nbellx")
        self.assertEqual(plain(out), "link ok\nbellx")

    def test_redaction_keeps_colours_and_works_across_style_changes(self):
        text = f"{E}32mtoken{E}0m={E}31mabc123{E}0m done"
        out, n = redact_ansi(text, [r"token=\S+"])
        self.assertEqual(n, 1)
        self.assertEqual(plain(out), "[redacted] done")
        self.assertIn(f"{E}32m", out)
        self.assertNotIn("abc123", out)
        self.assertEqual(redact_ansi("x", []), ("x", 0))
        self.assertEqual(redact_ansi("x", ["("]), ("x", 0))   # a broken pattern is skipped


class FakeClient:
    def __init__(self):
        self.reads = 0

    def read_ansi(self, target, lines=200):
        self.reads += 1
        return f"{E}1mscreen {self.reads}{E}0m\x1b]0;t\x07"


class Throttle(unittest.TestCase):
    def test_one_herdr_read_per_pane_per_interval_however_many_viewers(self):
        now = [100.0]
        client = FakeClient()
        reader = TerminalReader(client, clock=lambda: now[0])
        first = reader.screen("w1:p1")
        self.assertEqual(reader.screen("w1:p1"), first)
        self.assertEqual(client.reads, 1)
        self.assertNotIn("\x1b]", first)
        now[0] += 0.8
        reader.screen("w1:p1")
        reader.screen("w1:p2")
        self.assertEqual(client.reads, 3)


class Endpoint(_Servers):
    """Same servers as the allow-list tests: the phone listener with login, the desktop listener."""

    def setUp(self):
        super().setUp()
        herdr = self.collector.client
        herdr.screens["w1:p1"] = f"{E}38;2;158;206;106mbuild ok{E}0m\npassword=hunter2\n\x1b]0;title\x07"

    def get(self, server, path, headers=None):
        status, _, raw = self.request(server, "GET", path, headers=headers)
        return status, raw

    def ident(self, name="alpha"):
        return next(a["identity"] for a in self.collector.view(local=False)["agents"] if a["name"] == name)

    def test_phone_needs_the_login_then_gets_the_coloured_screen(self):
        path = f"/api/terminal?identity={self.ident()}"
        self.assertEqual(self.get(self.phone, path, {"X-Forwarded-For": PHONE})[0], 401)
        cookie, _ = self.login(PHONE)
        status, raw = self.get(self.phone, path, {"X-Forwarded-For": PHONE, "Cookie": cookie})
        self.assertEqual(status, 200)
        data = json.loads(raw)
        self.assertIn("\x1b[38;2;158;206;106mbuild ok", data["screen"])
        self.assertNotIn("hunter2", raw.decode())
        self.assertNotIn("\x1b]0", data["screen"])
        self.assertGreaterEqual(data["redactions"], 1)
        # Unchanged screen: no text is sent again.
        status, raw = self.get(self.phone, path + f"&since={data['hash']}", {"X-Forwarded-For": PHONE, "Cookie": cookie})
        self.assertTrue(json.loads(raw)["unchanged"])
        self.assertNotIn("screen", json.loads(raw))

    def test_the_allow_list_applies_to_the_terminal(self):
        self.assertEqual(self.desk("POST", {"entries": ["pixel-10a"], "enabled": True})[0], 200)
        cookie, _ = self.login(PHONE)
        status, raw = self.get(self.phone, f"/api/terminal?identity={self.ident()}", {"X-Forwarded-For": NAS, "Cookie": cookie})
        self.assertEqual((status, raw), (403, b"forbidden\n"))

    def test_unwatched_and_unknown_agents_are_404_and_never_read(self):
        cookie, _ = self.login(PHONE)
        herdr = self.collector.client
        herdr.calls.clear()
        for ident in ("nope", "w2:p1"):
            status, raw = self.get(self.phone, f"/api/terminal?identity={ident}", {"X-Forwarded-For": PHONE, "Cookie": cookie})
            self.assertEqual(status, 404)
            self.assertNotIn(b"HIDDEN", raw)
        self.assertEqual([c for c in herdr.calls if c[0].startswith("read")], [])

    def test_desktop_listener_reads_it_and_history_is_skipped_for_a_busy_agent(self):
        status, raw = self.get(self.desktop, f"/api/terminal?identity={self.ident()}&history=1")
        self.assertEqual(status, 200)
        self.assertIn("history", json.loads(raw))

    def test_the_view_only_reads(self):
        herdr = self.collector.client
        herdr.calls.clear()
        cookie, _ = self.login(PHONE)
        self.get(self.phone, f"/api/terminal?identity={self.ident()}&history=1", {"X-Forwarded-For": PHONE, "Cookie": cookie})
        self.assertTrue(herdr.calls)
        self.assertTrue(all(c[0] in ("read", "read_ansi") for c in herdr.calls), herdr.calls)
        self.assertEqual(self.request(self.phone, "POST", f"/api/terminal?identity={self.ident()}", {},
                                      {"X-Forwarded-For": PHONE, "Cookie": cookie})[0] in (403, 404, 405), True)


del _Servers   # do not run the allow-list tests twice

if __name__ == "__main__":
    unittest.main()
