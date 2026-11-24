"""0.9.3.1: rows are fetched under the store lock, and a view builds the relationship state once."""
import threading
import unittest
from unittest import mock

from herdr_attention.store import Store
from herdr_attention.relations import Registry
from herdr_attention import reply_routing, team_reports


class RowsTest(unittest.TestCase):
    def test_rows_behave_like_a_cursor(self):
        store = Store(":memory:")
        store._exec("CREATE TABLE t (a INTEGER, b TEXT)")
        cur = store._exec("INSERT INTO t VALUES (?, ?)", (1, "x"))
        self.assertEqual(cur.rowcount, 1)
        self.assertEqual(cur.lastrowid, 1)
        store._exec("INSERT INTO t VALUES (2, 'y')")
        rows = store._exec("SELECT a, b FROM t ORDER BY a")
        self.assertEqual(rows.fetchone(), (1, "x"))
        self.assertEqual(rows.fetchall(), [(2, "y")])
        self.assertIsNone(rows.fetchone())
        self.assertEqual(list(store._exec("SELECT a FROM t ORDER BY a")), [(1,), (2,)])

    def test_concurrent_reads_never_mix_results(self):
        store = Store(":memory:")
        store._exec("CREATE TABLE wide (a, b, c, d, e, f, g, h)")
        store._exec("CREATE TABLE narrow (x)")
        for i in range(300):
            store._exec("INSERT INTO wide VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (i,) * 8)
            store._exec("INSERT INTO narrow VALUES (?)", (i,))
        errors = []

        def read(sql, width):
            try:
                for _ in range(40):
                    rows = store._exec(sql).fetchall()
                    assert len(rows) == 300 and all(len(r) == width for r in rows), (len(rows), width)
            except Exception as exc:   # noqa: BLE001 - collected for the assertion below
                errors.append(exc)

        threads = [threading.Thread(target=read, args=(("SELECT * FROM wide", 8) if i % 2 else ("SELECT * FROM narrow", 1)))
                   for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])


class OncePerViewTest(unittest.TestCase):
    def test_labels_for_reuses_a_given_state(self):
        reg = Registry(Store(":memory:"))
        state = reg.state()
        with mock.patch.object(reg, "state", side_effect=AssertionError("state rebuilt")):
            self.assertIn("role", reg.labels_for("claude:x", state))

    def test_recipient_index_matches_the_plain_lookup(self):
        view = {"agents": [{"identity": "c", "present": True, "pane_id": "w1:p1"},
                           {"identity": "w", "present": True, "pane_id": "w1:p2"},
                           {"identity": "s", "in_process_parent": "w"}],
                "links": [{"kind": "reports_to", "from": "w", "to": "c"}]}
        index = reply_routing._index(view)
        for ident in ("c", "w", "s"):
            self.assertEqual(reply_routing.recipient(view, ident)["identity"], "c")
            self.assertEqual(reply_routing.recipient(view, ident, index)["identity"], "c")

    def test_coordinator_targets_skip_cycles(self):
        links = [{"kind": "reports_to", "from": "a", "to": "b"},
                 {"kind": "reports_to", "from": "x", "to": "y", "cycle": True}]
        self.assertTrue(team_reports.is_coordinator({"identity": "b"}, links))
        self.assertFalse(team_reports.is_coordinator({"identity": "y"}, links))
        self.assertTrue(team_reports.is_coordinator({"identity": "z", "roles": {"role": [{"value": "Coordinator"}]}}, links))


if __name__ == "__main__":
    unittest.main()
