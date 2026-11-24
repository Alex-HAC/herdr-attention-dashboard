"""Replay a recorded live Herdr session (anonymised) through the collector.

The fixture holds the real snapshot and the real event stream from
Herdr 0.9.1. Herdr's agent.get answers are not in the recording, so the fake
derives them from the events (status from the event, a rising
state_change_seq), which is exactly what Herdr returned live in phase 0.
"""

import unittest

from herdr_attention.collector import Collector
from tests.helpers import FakeHerdr, load_fixture, settings

FIXTURE = "herdr-0.9.1-live.jsonl"


def build(watched=("w4",)):
    rows = load_fixture(FIXTURE)
    snapshot = rows[0]
    events = [r["envelope"] for r in rows[1:]]
    ws = snapshot["workspaces"] or [{"workspace_id": w, "label": f"workspace {w}", "number": i}
                                    for i, w in enumerate(sorted({a["workspace_id"] for a in snapshot["agents"]}))]
    herdr = FakeHerdr(snapshot["agents"], ws)
    collector = Collector(db_path=":memory:", client=herdr,
                          settings=settings(watch={"enabled_workspaces": list(watched)}, stall={"output_poll_seconds": 0}))
    return collector, herdr, snapshot, events


def play(collector, herdr, events, seq_start=10_000):
    seq = seq_start
    for envelope in events:
        data = envelope["data"]
        current = herdr.agent(data["pane_id"])
        if current is not None and envelope["event"] == "pane.agent_status_changed":
            seq += 1
            current.update(agent_status=data["agent_status"], state_change_seq=seq)
            herdr.set(current)
        collector.handle_event(envelope)
    return seq


class Replay(unittest.TestCase):
    def test_fixture_is_real_shape(self):
        rows = load_fixture(FIXTURE)
        self.assertEqual(rows[0]["kind"], "snapshot")
        self.assertGreater(len(rows), 20)
        for row in rows[1:]:
            self.assertEqual(row["envelope"]["event"], "pane.agent_status_changed")
            self.assertEqual(set(row["envelope"]["data"]) - {"agent", "agent_status", "pane_id", "workspace_id"}, set())

    def test_completions_match_transitions(self):
        collector, herdr, snapshot, events = build()
        collector.handle_snapshot()
        play(collector, herdr, events)

        status = {a["pane_id"]: a["agent_status"] for a in snapshot["agents"] if a["workspace_id"] == "w4"}
        expected = sum(1 for s in status.values() if s == "done")  # already done at start (seen late)
        for envelope in events:
            data = envelope["data"]
            if data["workspace_id"] != "w4":
                continue
            if data["agent_status"] == "done" and status.get(data["pane_id"]) != "done":
                expected += 1
            status[data["pane_id"]] = data["agent_status"]

        stored = collector.store.recent_events(500)
        completions = [e for e in stored if e["type"] == "completion"]
        self.assertEqual(len(completions), expected)
        self.assertTrue(all(e["workspace_id"] == "w4" for e in stored), "w5 is not watched")
        open_done = {e["pane_id"] for e in completions if e["resolved_at"] is None}
        self.assertEqual(open_done, {p for p, s in status.items() if s == "done"})
        view = collector.view(local=False)
        self.assertEqual({a["pane_id"] for a in view["agents"] if a["kind"] == "completion"}, open_done)

    def test_reconnect_midway_creates_no_duplicates(self):
        collector, herdr, _, events = build()
        collector.handle_snapshot()
        half = len(events) // 2
        seq = play(collector, herdr, events[:half])
        before = len(collector.store.recent_events(500))
        collector.handle_snapshot()  # reconnect: same state
        collector.handle_snapshot()
        self.assertEqual(len(collector.store.recent_events(500)), before)
        play(collector, herdr, events[half:], seq_start=seq)
        types = [(e["identity"], e["type"], e["at"]) for e in collector.store.recent_events(500)]
        self.assertEqual(len(types), len(set(types)))

    def test_events_from_unwatched_panes_are_ignored(self):
        collector, herdr, _, events = build(watched=())
        collector.handle_snapshot()
        play(collector, herdr, events)
        self.assertEqual(collector.store.recent_events(500), [])
        self.assertEqual(collector.view(local=False)["agents"], [])


if __name__ == "__main__":
    unittest.main()
