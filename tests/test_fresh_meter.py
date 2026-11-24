"""Top-bar and graph freshness slot: status colour, working/total meter (v0.9.9.2)."""

import copy
import time
import unittest

from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER, run


def live(data):
    data["view"]["collector"]["herdr"]["last_reconcile"] = time.time()
    return data


def slot(dom, selector_id=None, cls=None):
    if selector_id:
        start = dom.index(f'id="{selector_id}"')
        start = dom.rindex("<", 0, start)
    else:
        start = dom.index(f'class="{cls}')
        start = dom.rindex("<", 0, start)
    # take a generous chunk of the element
    return dom[start:start + 1200]


@unittest.skipUnless(BROWSER, "no Chromium found")
class FreshMeter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = live(page_fixture())

    def test_list_topbar_shows_working_over_total_and_no_look_at_count(self):
        page = run(copy.deepcopy(self.data), "/list", 390, 844)
        fresh = slot(page["dom"], selector_id="fresh")
        self.assertIn("fresh-pip", fresh)
        self.assertIn("agent-meter", fresh)
        self.assertIn("2/7", fresh)
        self.assertIn("fresh-ago", fresh)
        self.assertIn("is-needs", fresh)
        self.assertNotIn("to look at", page["dom"].split('<main id="app"', 1)[0] + fresh)
        self.assertNotIn("to look at", slot(page["dom"], cls="status-header"))
        self.assertEqual(page["probe"]["overflowing"], [])

    def test_graph_freshness_slot_mirrors_the_meter(self):
        page = run(copy.deepcopy(self.data), "", 390, 844)
        fresh = slot(page["dom"], cls="sh-fresh")
        self.assertIn("fresh-pip", fresh)
        self.assertIn("2/7", fresh)
        self.assertIn("fresh-ago", fresh)
        self.assertIn("is-needs", fresh)
        self.assertNotIn("to look at", slot(page["dom"], cls="status-header"))
        self.assertIn("1 waiting on you", page["dom"])

    def test_pip_is_working_when_nobody_needs_you(self):
        data = copy.deepcopy(self.data)
        for a in data["view"]["agents"]:
            if a.get("tier") == "waiting_on_you" or a.get("state") == "needs_you":
                a["tier"] = None
                a["state"] = "working"
                a["kind"] = "working"
                a["status"] = "working"
        page = run(data, "/list", 390, 844)
        fresh = slot(page["dom"], selector_id="fresh")
        self.assertIn("is-working", fresh)
        self.assertIn("3/7", fresh)
        self.assertNotIn("is-needs", fresh)

    def test_pip_is_idle_when_everyone_is_quiet(self):
        data = copy.deepcopy(self.data)
        for a in data["view"]["agents"]:
            a["tier"] = None
            a["state"] = "done"
            a["kind"] = "idle"
            a["status"] = "done"
        page = run(data, "/list", 390, 844)
        fresh = slot(page["dom"], selector_id="fresh")
        self.assertIn("is-idle", fresh)
        self.assertIn("0/7", fresh)
        self.assertNotIn("is-needs", fresh)
        self.assertNotIn("is-working", fresh)

    def test_stale_discovery_wins_over_agent_colour(self):
        data = copy.deepcopy(self.data)
        data["view"]["collector"]["herdr"]["last_reconcile"] = None
        page = run(data, "/list", 390, 844)
        fresh = slot(page["dom"], selector_id="fresh")
        self.assertIn('class="fresh stale"', fresh)
        self.assertIn("2/7", fresh)
        self.assertIn("stale", slot(page["dom"], selector_id="fresh").lower())
