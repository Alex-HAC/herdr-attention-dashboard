"""features.dashboard_url for the desktop plugin's deep links (v0.8.2 slice C)."""

import unittest
from types import SimpleNamespace

from herdr_attention.collector import Collector
from tests.helpers import settings


def url(**conf):
    return Collector.dashboard_url(SimpleNamespace(settings=settings(**conf)))


class DashboardUrl(unittest.TestCase):
    def test_configured_url_wins(self):
        self.assertEqual(url(notify={"dashboard_url": "https://laptop.example.ts.net/"},
                             phone={"enabled": True, "port": 9000}), "https://laptop.example.ts.net")

    def test_local_server_when_no_url(self):
        self.assertEqual(url(phone={"enabled": True, "bind": "0.0.0.0", "port": 8765}), "http://127.0.0.1:8765")

    def test_unknown_when_nothing_is_on(self):
        self.assertEqual(url(phone={"enabled": False}), "")


if __name__ == "__main__":
    unittest.main()
