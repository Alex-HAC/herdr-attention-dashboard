"""v0.9.8.3: "Refresh workspaces" (read-only discovery now, at most once per 3 s), a cheap
workspace probe between reconciles, and the one-row-per-workspace Settings list."""
import json
import unittest
from unittest.mock import patch

from herdr_attention.collector import Collector, TooSoon
from tests.helpers import Clock, FakeHerdr, agent, settings, workspaces
from tests import test_api as _api
from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER, run


def collector(ws=("w1", "w2")):
    herdr = FakeHerdr([agent("w1:p1", "idle", session="a", name="alpha")], workspaces(*ws))
    c = Collector(db_path=":memory:", client=herdr, settings=settings(), clock=Clock())
    c.handle_snapshot()
    return c, herdr


class RefreshNow(unittest.TestCase):
    def test_a_new_workspace_shows_now_and_stays_off(self):
        c, herdr = collector()
        self.addCleanup(c.store.close)
        herdr.ws = workspaces("w1", "w2", "w3")
        result = c.refresh_workspaces()
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 3)
        self.assertEqual(result["new"], [{"id": "w3", "label": "label-w3"}])
        w3 = next(w for w in result["workspaces"] if w["id"] == "w3")
        self.assertFalse(w3["watched"], "new workspaces stay off")
        self.assertEqual(c.settings.get("watch.enabled_workspaces"), ["w1"])

    def test_at_most_once_per_3_seconds(self):
        c, _ = collector()
        self.addCleanup(c.store.close)
        c.refresh_workspaces()
        with self.assertRaises(TooSoon) as caught:
            c.refresh_workspaces()
        self.assertGreater(caught.exception.retry_after, 2.0)
        c._ws_refresh_at -= 3.1
        self.assertTrue(c.refresh_workspaces()["ok"])

    def test_read_only_toward_herdr(self):
        c, herdr = collector()
        self.addCleanup(c.store.close)
        calls = []
        for name in ("agents", "workspaces", "tabs", "panes"):
            if hasattr(herdr, name):
                original = getattr(herdr, name)
                setattr(herdr, name, (lambda o, n: (lambda *a, **k: (calls.append(n), o(*a, **k))[1]))(original, name))
        c.refresh_workspaces()
        self.assertTrue(set(calls) <= {"agents", "workspaces", "tabs", "panes"}, calls)
        self.assertIn("workspaces", calls)
        for verb in ("focus", "send_keys", "send_text", "create", "close", "move", "rename"):
            self.assertFalse(any(verb in n for n in calls), verb)

    def test_herdr_down_is_a_message_not_a_crash(self):
        c, herdr = collector()
        self.addCleanup(c.store.close)
        from herdr_attention.herdr import HerdrError
        with patch.object(herdr, "agents", side_effect=HerdrError("cannot reach Herdr")):
            result = c.refresh_workspaces()
        self.assertFalse(result["ok"])
        self.assertIn("cannot reach Herdr", result["detail"])


class Probe(unittest.TestCase):
    def test_a_new_workspace_id_asks_for_the_reconcile(self):
        c, herdr = collector()
        self.addCleanup(c.store.close)
        c._reconcile_requested.clear()
        self.assertFalse(c.probe_workspaces(now=100.0))
        herdr.ws = workspaces("w1", "w2", "w3")
        self.assertFalse(c.probe_workspaces(now=102.0), "not more often than every 5 s")
        self.assertTrue(c.probe_workspaces(now=105.5))
        self.assertTrue(c._reconcile_requested.is_set())

    def test_the_view_has_a_chooser_revision(self):
        c, herdr = collector()
        self.addCleanup(c.store.close)
        before = c.view(local=False)["chooser_rev"]
        herdr.ws = workspaces("w1", "w2", "w3")
        c.handle_snapshot()
        self.assertNotEqual(c.view(local=False)["chooser_rev"], before)


class Endpoint(unittest.TestCase):
    setUp, tearDown, request, login = _api.PhoneApi.setUp, _api.PhoneApi.tearDown, _api.PhoneApi.request, _api.PhoneApi.login

    def test_refresh_needs_login_and_csrf_and_is_rate_limited(self):
        self.assertEqual(self.request("POST", "/api/workspaces/refresh", {})[0], 401)
        cookie, csrf = self.login()
        self.assertEqual(self.request("POST", "/api/workspaces/refresh", {}, cookie=cookie)[0], 403)
        self.assertEqual(self.request("GET", "/api/workspaces/refresh", cookie=cookie)[0], 404, "POST only")
        status, _, data = self.request("POST", "/api/workspaces/refresh", {}, cookie=cookie, csrf=csrf)
        self.assertEqual(status, 200, data)
        self.assertTrue(data["ok"])
        self.assertEqual({w["id"] for w in data["workspaces"]}, {"w1", "w2"})
        self.assertNotIn("HIDDEN", json.dumps(data), "unwatched workspaces: id and label only")
        status, _, data = self.request("POST", "/api/workspaces/refresh", {}, cookie=cookie, csrf=csrf)
        self.assertEqual(status, 429)
        self.assertIn("try again", data["error"])


SETTINGS_SCRIPT = """
const report = (out) => { const t = window.parent !== window ? window.parent.document.body : document.body; t.dataset.probe = JSON.stringify(out); };
window.__probed = true;
const wait = (ms) => new Promise((ok) => setTimeout(ok, ms));
const rows = () => [...document.querySelectorAll("main .ws-row")];
const out = {};
out.rows = rows().map((r) => ({id: r.dataset.workspace, name: r.querySelector(".ws-name b").textContent, off: r.classList.contains("is-off"),
  on: r.querySelector(".seg-btn.is-on").dataset.mode, meta: r.querySelector(".ws-name small").textContent}));
out.headButton = !!document.querySelector("#settings-workspaces .section-head .ws-refresh");
out.small = [...document.querySelectorAll("#settings-workspaces button")].filter((b) => b.getBoundingClientRect().height < 40).map((b) => b.textContent);
out.wide = document.documentElement.scrollWidth;
const row = (id) => document.querySelector(`main .ws-row[data-workspace="${id}"]`);
row(%(watched)s).querySelector('[data-mode="watch"]').click();
await wait(150);
row(%(off)s).querySelector('[data-mode="alerts"]').click();
await wait(150);
document.querySelector(".ws-refresh").click();
await wait(300);
out.posts = (window.__posts || []).filter((p) => p[0] !== "/api/session");
out.status = document.querySelector(".ws-refresh-status")?.textContent || null;
report(out);
"""


@unittest.skipUnless(BROWSER, "no Chromium found")
class SettingsList(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data = page_fixture()
        cls.chooser = data["chooser"]
        watched = next(w for w in cls.chooser if w["watched"])
        off = next(w for w in cls.chooser if not w["watched"])
        data["workspacesRefresh"] = {"ok": True, "workspaces": cls.chooser + [{"id": "wNew", "label": "fresh-ws", "number": 9,
                                                                               "watched": False, "muted": False}],
                                     "count": len(cls.chooser) + 1, "new": [{"id": "wNew", "label": "fresh-ws"}]}
        cls.watched, cls.off = watched, off
        script = SETTINGS_SCRIPT % {"watched": json.dumps(watched["id"]), "off": json.dumps(off["id"])}
        cls.probe = run(data, "/settings", 390, 2400, script=script)["probe"]

    def test_one_row_per_workspace_watched_first(self):
        rows = self.probe["rows"]
        self.assertEqual(len(rows), len(self.chooser))
        flags = [r["off"] for r in rows]
        self.assertEqual(flags, sorted(flags), "watched first, then not watched")
        for r in rows:
            w = next(w for w in self.chooser if w["id"] == r["id"])
            self.assertEqual(r["on"], "off" if not w["watched"] else "watch" if w["muted"] else "alerts")

    def test_modes_save_the_same_settings(self):
        posts = [p for p in self.probe["posts"] if p[0] == "/api/settings"]
        self.assertEqual(len(posts), 2, posts)
        first, second = posts[0][1], posts[1][1]
        # Watch on a watched, unmuted workspace: only the mute list changes.
        self.assertEqual(set(first), {"watch.muted_workspaces"})
        self.assertIn(self.watched["id"], first["watch.muted_workspaces"])
        # Alerts on a workspace that is off: watched, and not muted.
        self.assertIn(self.off["id"], second["watch.enabled_workspaces"])
        self.assertNotIn(self.off["id"], second.get("watch.muted_workspaces", []))

    def test_refresh_button_in_the_header_reports_what_it_found(self):
        self.assertTrue(self.probe["headButton"])
        self.assertIn(["/api/workspaces/refresh", {}], self.probe["posts"])
        self.assertEqual(self.probe["status"], f"Found {len(self.chooser) + 1} workspaces, 1 new: fresh-ws.")

    def test_fits_390_with_tap_size_buttons(self):
        self.assertLessEqual(self.probe["wide"], 390)
        self.assertEqual(self.probe["small"], [])


if __name__ == "__main__":
    unittest.main()
