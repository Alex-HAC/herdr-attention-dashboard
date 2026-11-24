"""Workspace-centred view: states, grouping, timeline, detail, chooser (fixture data)."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from herdr_attention.classify import state_of
from herdr_attention.engine import Engine
from herdr_attention.store import SCHEMA_VERSION, Store
from tests.helpers import Clock, agent, settings, workspaces


def make(conf=None):
    clock = Clock()
    conf = conf or settings(watch={"enabled_workspaces": ["w1", "w2"]})
    store = Store(":memory:")
    engine = Engine(conf, store, clock)
    return engine, store, clock


def tabbed(pane, status, tab, **kw):
    info = agent(pane, status, **kw)
    info["tab_id"] = tab
    return info


class States(unittest.TestCase):
    def test_mapping_from_kinds(self):
        conf = settings()
        for kind, expected in [("approval", "needs_you"), ("question", "needs_you"), ("blocked", "needs_you"),
                               ("failure", "needs_you"), ("working", "working"), ("stall", "working"),
                               ("completion", "done"), ("unknown", "unknown")]:
            self.assertEqual(state_of({"kind": kind}, 0, conf)[0], expected, kind)
        self.assertTrue(state_of({"kind": "stall"}, 0, conf)[1]["possible_stall"])

    def test_waiting_turns_parked_after_threshold(self):
        conf = settings(ui={"parked_after_minutes": 60})
        idle = {"kind": "idle", "status": "idle", "last_activity_at": 1000.0, "first_seen": 0.0}
        self.assertEqual(state_of(idle, 1000 + 59 * 60, conf)[0], "waiting")
        state, basis = state_of(idle, 1000 + 61 * 60, conf)
        self.assertEqual(state, "parked")
        self.assertEqual((basis["quiet_minutes"], basis["quiet_known"]), (61, True))

    def test_unknown_activity_counts_from_first_seen_and_says_so(self):
        conf = settings(ui={"parked_after_minutes": 60})
        state, basis = state_of({"kind": "idle", "first_seen": 0.0}, 90 * 60, conf)
        self.assertEqual(state, "parked")
        self.assertFalse(basis["quiet_known"])


class Timeline(unittest.TestCase):
    def test_status_and_title_changes_are_recorded(self):
        engine, store, clock = make()
        engine.apply_snapshot([agent("w1:p1", "idle", title="Old task")], workspaces("w1"))
        clock.advance(60)
        engine.apply_agent(agent("w1:p1", "working", title="Old task"))
        clock.advance(60)
        engine.apply_agent(agent("w1:p1", "working", title="New task"))
        rows = store.activity("claude:s1")
        self.assertEqual([(r["kind"], r["status"], r["title"], r["live"]) for r in rows], [
            ("title", "working", "New task", True),
            ("status", "working", "Old task", True),
            ("seen", "idle", "Old task", False),
        ])
        self.assertEqual(engine.agents["claude:s1"]["last_activity_at"], clock.now)

    def test_changes_found_in_a_snapshot_are_not_activity(self):
        engine, store, clock = make()
        engine.apply_snapshot([agent("w1:p1", "working", seq=1)], workspaces("w1"))
        clock.advance(600)
        engine.apply_snapshot([agent("w1:p1", "idle", seq=5)])
        self.assertIsNone(engine.agents["claude:s1"].get("last_activity_at"))
        self.assertFalse(store.activity("claude:s1")[0]["live"])

    def test_output_change_counts_as_activity(self):
        engine, _, clock = make()
        engine.apply_snapshot([agent("w1:p1", "idle")], workspaces("w1"))
        engine.apply_agent(agent("w1:p1", "working"))
        engine.record_output("claude:s1", "a")
        clock.advance(300)
        engine.record_output("claude:s1", "a")
        self.assertEqual(engine.agents["claude:s1"]["last_activity_at"], clock.now - 300)
        clock.advance(300)
        engine.record_output("claude:s1", "b")
        self.assertEqual(engine.agents["claude:s1"]["last_activity_at"], clock.now)

    def test_completion_keeps_the_title_of_that_moment(self):
        engine, store, clock = make()
        engine.apply_snapshot([agent("w1:p1", "working", title="Write the guide")], workspaces("w1"))
        engine.apply_agent(agent("w1:p1", "done", title="Write the guide"))
        engine.apply_agent(agent("w1:p1", "idle", title="Something else"))
        view = engine.view()["agents"][0]
        self.assertEqual(view["last_done"]["title"], "Write the guide")


class WorkspaceView(unittest.TestCase):
    def build(self):
        engine, store, clock = make()
        engine.set_tabs([{"tab_id": "w1:tA", "workspace_id": "w1", "label": "Lead", "number": 1},
                         {"tab_id": "w1:tB", "workspace_id": "w1", "label": "Workers", "number": 2}])
        engine.apply_snapshot([
            tabbed("w1:p1", "working", "w1:tA", session="lead", name="lead"),
            tabbed("w1:p2", "working", "w1:tB", session="w1", name="worker-1"),
            tabbed("w1:p3", "idle", "w1:tB", session="w2", name="worker-2"),
            tabbed("w1:p4", "working", "w1:tZ", session="lost", name="no-tab"),
            tabbed("w2:p1", "idle", "w2:tA", session="other", name="other"),
            tabbed("w3:p1", "blocked", "w3:tA", session="hidden", name="hidden", title="SECRET"),
        ], workspaces("w1", "w2", "w3"))
        clock.advance(30)
        engine.apply_agent(tabbed("w1:p2", "done", "w1:tB", session="w1", name="worker-1"))
        return engine, store, clock

    def test_counts_tabs_and_relationships(self):
        engine, _, _ = self.build()
        view = engine.view()
        self.assertEqual([w["id"] for w in view["workspaces"]], ["w1", "w2"])
        w1 = view["workspaces"][0]
        self.assertEqual(w1["counts"], {"needs_you": 0, "working": 2, "done": 1, "waiting": 1, "parked": 0, "unknown": 0,
                                        "waiting_on_you": 0, "might_look": 0, "fyi_finished": 1, "waiting_on_coordinator": 0})
        self.assertEqual(w1["attention"], 1)
        self.assertEqual(w1["agent_count"], 4)
        self.assertEqual([(t["label"], len(t["agents"])) for t in w1["tabs"]],
                         [("Lead", 1), ("Workers", 2), ("tab unknown", 1)])
        self.assertEqual(w1["relationships"], "unknown")
        self.assertIsNotNone(w1["last_activity_at"])
        names = [a["name"] for a in view["agents"]]
        self.assertNotIn("hidden", names)

    def test_agent_fields(self):
        engine, _, _ = self.build()
        lead = next(a for a in engine.view()["agents"] if a["name"] == "lead")
        self.assertEqual((lead["state"], lead["tab_label"], lead["cwd"]), ("working", "Lead", "/tmp/x"))
        self.assertIsNone(lead["last_activity_at"], "nothing seen happen yet")
        self.assertIn("state_basis", lead)

    def test_detail_is_watched_only_and_lists_same_tab(self):
        engine, _, _ = self.build()
        self.assertIsNone(engine.agent_detail("claude:hidden"))
        detail = engine.agent_detail("claude:w2")
        self.assertEqual([p["name"] for p in detail["same_tab"]], ["worker-1"])
        self.assertEqual(detail["relationships"], "unknown")
        self.assertTrue(detail["activity"])
        worker = engine.agent_detail("claude:w1")
        self.assertEqual([e["type"] for e in worker["events"]], ["completion"])

    def test_chooser_has_labels_only(self):
        engine, _, _ = self.build()
        chooser = engine.chooser()
        self.assertEqual([c["id"] for c in chooser], ["w1", "w2", "w3"])
        self.assertEqual([c["watched"] for c in chooser], [True, True, False])
        for entry in chooser:
            self.assertEqual(set(entry), {"id", "label", "number", "watched", "muted"})
        self.assertNotIn("SECRET", repr(chooser))

    def test_zero_numbered_items_sort_first(self):
        engine, _, _ = make()
        engine.set_workspaces([{"workspace_id": "w2", "label": "b", "number": 1},
                               {"workspace_id": "w1", "label": "a", "number": 0}])
        self.assertEqual([c["id"] for c in engine.chooser()], ["w1", "w2"])


class Migration(unittest.TestCase):
    def test_version_one_database_gets_the_title_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.db"
            db = sqlite3.connect(path)
            db.executescript("""
                CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, identity TEXT NOT NULL,
                  workspace_id TEXT, pane_id TEXT, type TEXT NOT NULL, evidence TEXT NOT NULL, confidence TEXT NOT NULL,
                  seen_late INTEGER NOT NULL DEFAULT 0, resolved_at REAL, acked_at REAL,
                  notify_state TEXT NOT NULL DEFAULT 'pending', notified_at REAL, notify_error TEXT);
                CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
                INSERT INTO meta VALUES ('schema_version', '1');
                INSERT INTO events (at, identity, type, evidence, confidence) VALUES (1, 'a', 'completion', '{}', 'medium');
            """)
            db.commit()
            db.close()
            store = Store(path)
            [event] = store.recent_events(5)
            self.assertIsNone(event["title"])
            self.assertEqual(store.get_meta("schema_version"), str(SCHEMA_VERSION))
            store.close()


if __name__ == "__main__":
    unittest.main()
