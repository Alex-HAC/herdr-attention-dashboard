"""Graph data: active time, verified links, hidden endpoints, conversation API,
and the graph page in a real browser at phone and desktop width. FIXTURE-BASED."""

import http.client
import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from herdr_attention.api import PhoneServer, make_handler, serve
from herdr_attention.collector import Collector
from herdr_attention.engine import Engine
from herdr_attention.store import Store
from tests.helpers import Clock, FakeHerdr, FakeKeyring, agent, settings, workspaces
from tests.trace_fixtures import codex_trace, codex_usage, sid
from tests.web_fixture import HIDDEN_LABEL, SECRET_SESSION, page_fixture
from tests.web_harness import BROWSER, run


class ActiveTime(unittest.TestCase):
    def test_only_observed_working_intervals_count(self):
        clock = Clock()
        store = Store(":memory:")
        engine = Engine(settings(), store, clock)
        engine.apply_snapshot([agent("w1:p1", "idle")], workspaces("w1"))
        clock.advance(600)
        engine.apply_agent(agent("w1:p1", "working"))
        clock.advance(300)
        engine.tick()
        clock.advance(300)
        engine.apply_agent(agent("w1:p1", "idle"))
        clock.advance(3600)                               # idle time is not active time
        a = engine.view()["agents"][0]
        self.assertEqual(a["metrics"]["active"]["value"], 600)
        self.assertTrue(a["metrics"]["active"]["exact"])

    def test_restart_gap_is_not_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state.db"
            clock = Clock()
            first = Engine(settings(), Store(db), clock)
            first.apply_snapshot([agent("w1:p1", "idle", seq=1)], workspaces("w1"))
            first.apply_agent(agent("w1:p1", "working", seq=2))
            clock.advance(120)
            first.tick()
            first.store.close()
            clock.advance(7200)                           # collector was off for two hours
            second = Engine(settings(), Store(db), clock)
            second.apply_snapshot([agent("w1:p1", "working", seq=2)], workspaces("w1"))
            clock.advance(60)
            active = second.view()["agents"][0]["metrics"]["active"]
            self.assertEqual(active["value"], 180)
            self.assertFalse(active["exact"], "the gap makes it a lower bound")


class Links(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.parent, self.child, self.orphan, self.secret = sid("p"), sid("c"), sid("o"), sid("s")
        codex_trace(root, self.parent, 1e9, [{"total": codex_usage(10, 0, 1), "last": codex_usage(10, 0, 1)}])
        codex_trace(root, self.child, 1e9, [], parent=self.parent)          # cross-workspace, both watched
        codex_trace(root, self.orphan, 1e9, [], parent=self.secret)         # parent is in an unwatched workspace
        codex_trace(root, self.secret, 1e9, [], messages=[("user", "SECRET")])
        herdr = FakeHerdr([agent("w1:p1", "working", session=self.parent, kind="codex", name="parent"),
                           agent("w2:p1", "working", session=self.child, kind="codex", name="child"),
                           agent("w2:p2", "working", session=self.orphan, kind="codex", name="orphan"),
                           agent("w3:p1", "working", session=self.secret, kind="codex", name="secret")],
                          workspaces("w1", "w2", "w3"))
        self.collector = Collector(db_path=":memory:", client=herdr, settings=settings(
            watch={"enabled_workspaces": ["w1", "w2"]},
            traces={"claude_dirs": [str(root / "claude")], "codex_dirs": [str(root / "codex")]}))
        self.collector.handle_snapshot()
        self.collector.poll_traces()

    def tearDown(self):
        self.tmp.cleanup()

    def test_cross_workspace_link_and_hidden_endpoint(self):
        view = self.collector.view(local=False)
        links = {(l["from"], l["to"], l["hidden"]) for l in view["links"]}
        self.assertIn((f"codex:{self.child}", f"codex:{self.parent}", False), links)
        hidden = [l for l in view["links"] if l["hidden"]]
        self.assertEqual(len(hidden), 1)
        self.assertEqual(hidden[0]["from"], f"codex:{self.orphan}")
        blob = json.dumps(view)
        self.assertNotIn(self.secret, blob, "the hidden endpoint's id is not exposed")
        self.assertNotIn("SECRET", blob)

    def test_unwatched_traces_are_never_read(self):
        self.assertNotIn(f"codex:{self.secret}", self.collector.engine.trace_targets())
        self.assertNotIn(f"codex:{self.secret}", self.collector.traces._states)
        self.assertIsNone(self.collector.conversation(f"codex:{self.secret}", 10, None))

    def test_detail_lists_connections_both_ways(self):
        detail = self.collector.agent_detail(f"codex:{self.parent}")
        self.assertEqual([(c["from"], c["kind"]) for c in detail["connections"]], [(f"codex:{self.child}", "spawned_by")])

    def test_switching_a_workspace_off_drops_its_links(self):
        self.collector.update_settings({"watch.enabled_workspaces": ["w1"]}, local=True)
        self.collector.poll_traces()
        self.assertEqual(self.collector.view(local=False)["links"], [])
        self.assertEqual(set(self.collector.traces._states), {f"codex:{self.parent}"})


class ConversationApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.session = sid("talker")
        codex_trace(root, self.session, 1e9, [], messages=[("user", f"q{i}") if i % 2 == 0 else ("assistant", f"a{i}")
                                                           for i in range(80)])
        herdr = FakeHerdr([agent("w1:p1", "idle", session=self.session, kind="codex"),
                           agent("w2:p1", "idle", session=sid("other"), kind="codex")], workspaces("w1", "w2"))
        self.collector = Collector(db_path=":memory:", client=herdr, settings=settings(
            phone={"cookie_secure": False},
            traces={"codex_dirs": [str(root / "codex")], "claude_dirs": [], "conversation_max": 50}))
        self.collector.keyring = FakeKeyring({"dashboard-passphrase": "correct horse battery"})
        self.collector.handle_snapshot()
        self.server = PhoneServer(("127.0.0.1", 0), make_handler(self.collector, local=False))
        serve(self.server)
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.stopping.set()
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def get(self, path, cookie=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", path, headers={"Cookie": cookie} if cookie else {})
        response = conn.getresponse()
        data = json.loads(response.read() or b"{}")
        conn.close()
        return response.status, data

    def login(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("POST", "/login", body=json.dumps({"passphrase": "correct horse battery"}),
                     headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{self.port}"})
        response = conn.getresponse()
        response.read()
        conn.close()
        return response.getheader("Set-Cookie").split(";")[0]

    def test_needs_login_watched_agent_and_is_bounded(self):
        identity = urllib.parse.quote(f"codex:{self.session}", safe="")
        self.assertEqual(self.get(f"/api/conversation?identity={identity}")[0], 401)
        cookie = self.login()
        status, page = self.get(f"/api/conversation?identity={identity}&limit=500", cookie)
        self.assertEqual(status, 200)
        self.assertEqual(page["limit"], 50)
        self.assertEqual(len(page["entries"]), 50)
        self.assertEqual(page["entries"][-1]["text"], "a79")
        status, older = self.get(f"/api/conversation?identity={identity}&limit=50&before={page['before']}", cookie)
        self.assertEqual(len(older["entries"]), 30)
        self.assertTrue(older["complete"])
        other = urllib.parse.quote(f"codex:{sid('other')}", safe="")
        self.assertEqual(self.get(f"/api/conversation?identity={other}", cookie)[0], 404)
        self.assertEqual(self.get(f"/api/conversation?identity={identity}&limit=x", cookie)[0], 400)


@unittest.skipUnless(BROWSER, "no Chromium found")
class GraphPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = page_fixture()
        ui = urllib.parse.quote(cls.data["ids"]["ui"], safe="")
        cls.pages = {}
        for device, width, height in (("phone", 390, 900), ("desktop", 1280, 900)):
            cls.pages[("graph", device)] = run(cls.data, "/graph", width, height)
            cls.pages[("selected", device)] = run(cls.data, f"/graph/{ui}", width, height)

    def svg(self, key):
        dom = self.pages[key]["dom"]
        return dom.split("<svg class=\"graph", 1)[1].split("</svg>", 1)[0]

    def test_one_shared_space_with_regions_nodes_and_edge_types(self):
        for device in ("phone", "desktop"):
            svg = self.svg(("graph", device))
            self.assertEqual(svg.count('class="viewport"'), 1, "one coordinate space")
            self.assertEqual(svg.count('class="region"'), 3)
            self.assertEqual(svg.count('data-id="'), len(self.data["view"]["agents"]))
            self.assertEqual(svg.count('class="region-g'), 3, "membership = the workspace region")
            self.assertEqual(svg.count("edge rel spawned_by"), 2)
            self.assertEqual(svg.count("edge rel forked_from"), 1)
            self.assertEqual(svg.count('class="hidden-node"'), 1)
            self.assertIn("cross", svg, "the spawn link crosses workspaces")
            self.assertIn("hidden or unknown agent", svg)

    def test_selection_highlights_links_and_opens_inspector(self):
        for device in ("phone", "desktop"):
            svg = self.svg(("selected", device))
            import re
            node = re.search(r'<g[^>]*data-id="%s"[^>]*>' % re.escape(self.data["ids"]["ui"]), svg).group(0)
            self.assertIn("node s-needs_you", node)
            self.assertIn("selected", node)
            self.assertEqual(svg.count(" active"), 3, "all links of the selected agent highlighted, incl. cross-workspace")
            self.assertIn(" dim", svg)
            dom = self.pages[("selected", device)]["dom"]
            for text in ("ui-builder", "Metrics", "Connections", "Recent history", "Conversation and terminal",
                         "Codex total_token_usage", "session start in the trace", "exact", "Show messages",
                         "Tokens are not money"):
                self.assertIn(text, dom, text)

    def test_privacy_layout_and_no_side_effects(self):
        for key, page in self.pages.items():
            with self.subTest(page=key):
                probe = page["probe"]
                self.assertEqual(probe["scrollWidth"], probe["innerWidth"])
                self.assertEqual(probe["smallTargets"], [])
                dom = page["dom"]
                for leak in ("SECRET", SECRET_SESSION, "secret-agent", HIDDEN_LABEL, "HIDDEN REASONING"):
                    self.assertNotIn(leak, dom.split("<script>const FIXTURE", 1)[0])
                calls = [c[0] for c in probe["calls"]]
                self.assertFalse(any(c.startswith("/api/conversation") or c.startswith("/api/output") for c in calls),
                                 "conversation and terminal only on request")
                self.assertFalse([c for c in probe["calls"] if c[1] == "POST"])


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(BROWSER, "no Chromium found")
class RelationshipsInTheGraph(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = page_fixture()
        lead = urllib.parse.quote(cls.data["ids"]["lead"], safe="")
        cls.graph = run(cls.data, "/graph", 390, 900)
        cls.lead = run(cls.data, f"/graph/{lead}", 1280, 900)
        node = f'[data-id="{cls.data["ids"]["etl"]}"]'
        # touch: pointerdown + pointerup on a node selects it
        cls.tap = run(cls.data, "/graph", 390, 900, script=f"""
            const el = document.querySelector('{node} .body'); const r = el.getBoundingClientRect();
            const opts = {{ bubbles: true, pointerId: 1, pointerType: 'touch', clientX: r.x + r.width / 2, clientY: r.y + r.height / 2 }};
            el.dispatchEvent(new PointerEvent('pointerdown', opts)); el.dispatchEvent(new PointerEvent('pointerup', opts));""")
        # keyboard: focus a node and press Enter
        cls.key = run(cls.data, "/graph", 1280, 900, script=f"""
            const el = document.querySelector('{node}'); el.focus();
            el.dispatchEvent(new KeyboardEvent('keydown', {{ key: 'Enter', bubbles: true }}));""")

    def test_declared_edges_roles_and_cycle(self):
        svg = self.graph["dom"].split('<svg class="graph', 1)[1].split("</svg>", 1)[0]
        self.assertEqual(svg.count("edge rel reports_to declared"), 4)
        self.assertEqual(svg.count("declared cycle"), 2, "the qa <-> etl loop is flagged on both edges")
        self.assertRegex(svg, r'class="role-label"[^>]*>coordinator</text>')

    def test_inspector_shows_provenance_editor_and_history(self):
        dom = self.lead["dom"]
        for text in ("coordinates", "registered by the launch helper", "declared by you (cli)", "coordinator (manual",
                     "checkout (manual", "Declare role, team or reporting line", "Relationship history", "Hand over"):
            self.assertIn(text, dom, text)

    def test_touch_and_keyboard_select_a_node(self):
        etl = urllib.parse.quote(self.data["ids"]["etl"], safe="")
        for page in (self.tap, self.key):
            self.assertEqual(page["probe"]["hash"], f"#/g/{etl}")
            self.assertIn("part of a reporting cycle", page["dom"])
            self.assertFalse([c for c in page["probe"]["calls"] if c[1] == "POST"], "selecting changes nothing")
