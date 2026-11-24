"""Freshness in the shipped browser page; fixture-only."""
import unittest
from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER, run


@unittest.skipUnless(BROWSER, 'no Chromium found')
class DiscoveryFreshness(unittest.TestCase):
    def test_connected_stream_cannot_hide_failed_discovery(self):
        data = page_fixture()
        data['view']['collector']['herdr'].update(connected=True, last_reconcile=1,
                                                  reconcile_error='metadata unavailable')
        for route in ('', '/list'):
            with self.subTest(route=route):
                page = run(data, route=route, width=390)
                self.assertIn('Agent list may be out of date (last check', page['dom'])
        self.assertIn('metadata unavailable', run(data)['dom'])
