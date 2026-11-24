"""Notifications view API (v0.8.2): list, one item, read state, click links."""

import json
import os
import shutil
import tempfile
import unittest

from herdr_attention.api import LocalServer, make_handler, serve
from herdr_attention.cli import Client
from herdr_attention.notifications import at_label
from tests.helpers import settings
from tests.test_api import make_collector
from tests.test_notify import Rig


class NotificationsApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hat-", dir=os.environ.get("XDG_RUNTIME_DIR") or "/tmp")
        self.collector = make_collector()
        store = self.collector.store
        [self.done] = [e["id"] for e in store.recent_events(10) if e["type"] == "completion"]
        store.set_notify_state([self.done], "sent", at=self.collector.clock())
        store.save_notification_text([self.done], "alpha finished the review", "The review is ready.", "ai", self.collector.clock())
        # A held-back event with a gate reason, and one in an unwatched workspace.
        self.held = store.add_event(at=self.collector.clock() + 1, identity="claude:a", workspace_id="w1",
                                    pane_id="w1:p1", type="stall",
                                    evidence={"notify_tier": "might_look", "note": "password=hunter2 then nothing"},
                                    confidence="medium", seen_late=False, notify_state="gated_skip")
        self.hidden = store.add_event(at=self.collector.clock() + 2, identity="claude:b", workspace_id="w2",
                                      pane_id="w2:p1", type="approval", evidence={}, confidence="high",
                                      seen_late=False, notify_state="sent", title="HIDDEN")
        self.pending = store.add_event(at=self.collector.clock() + 3, identity="claude:a", workspace_id="w1",
                                       pane_id="w1:p1", type="failure", evidence={}, confidence="high",
                                       seen_late=False, notify_state="pending")
        gate = self.collector.notifier.gate
        if gate is not None:
            store._exec("INSERT INTO notify_gates (at, event_ids, identities, status, decision, reason, model, effort, account, tier) "
                        "VALUES (?, ?, '[]', 'complete', 'skip', 'Same stall as an hour ago', 'm', 'low', 'x', 'might_look')",
                        (self.collector.clock(), json.dumps([self.held])))
        self.path = os.path.join(self.tmp, "api.sock")
        self.server = LocalServer(self.path, make_handler(self.collector, local=True))
        serve(self.server)
        self.client = Client(self.path)

    def tearDown(self):
        self.server.stopping.set()
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_list_is_newest_first_watched_only_and_counts_unread(self):
        data = self.client.get("/api/notifications?limit=50")
        self.assertEqual([i["id"] for i in data["items"]], [self.done])
        self.assertEqual(data["unread"], 1)
        self.assertEqual(self.client.get("/api/view")["notifications"]["unread"], 1)
        self.assertEqual(len(self.client.get("/api/view")["notifications"]["items"]), 1)
        self.assertNotIn("HIDDEN", json.dumps(data))

    def test_item_shape_and_plain_words(self):
        item = self.client.get(f"/api/notifications/{self.done}")
        for key in ("id", "at", "at_label", "tier", "tier_label", "what", "why", "action", "agent", "workspace",
                    "coordinator", "evidence", "delivery", "gate", "read", "links"):
            self.assertIn(key, item)
        self.assertEqual(item["tier"], "fyi_finished")
        self.assertEqual(item["tier_label"], "Finished")
        self.assertEqual(item["what"], "alpha finished the review")
        self.assertEqual(item["agent"], {"identity": "claude:a", "name": "alpha", "present": True})
        self.assertEqual(item["workspace"]["id"], "w1")
        self.assertEqual(item["delivery"]["state"], "sent")
        self.assertFalse(item["read"])

    def test_held_item_stays_in_history_but_is_not_listed(self):
        data = self.client.get('/api/notifications')
        self.assertNotIn(self.held, [n['id'] for n in data['items']])
        self.assertIn(self.held, [e['id'] for e in self.collector.store.events_for('claude:a')])
        with self.assertRaises(RuntimeError):
            self.client.get(f'/api/notifications/{self.held}')

    def test_unknown_unwatched_or_unsent_ids_are_404(self):
        for event_id in (999999, self.hidden, self.pending, "abc"):
            with self.assertRaises(RuntimeError) as caught:
                self.client.get(f"/api/notifications/{event_id}")
            self.assertIn("no longer available", str(caught.exception))

    def test_mark_one_and_mark_all_read(self):
        self.client.post(f"/api/events/{self.done}/ack", {})
        self.assertTrue(self.client.get(f"/api/notifications/{self.done}")["read"])
        self.assertEqual(self.client.get("/api/notifications")["unread"], 0)
        self.collector.store.add_event(at=self.collector.clock(), identity='claude:a', workspace_id='w1',
            pane_id='w1:p1', type='failure', evidence={}, confidence='high', seen_late=False, notify_state='sent')
        self.assertEqual(self.client.post("/api/notifications/read-all", {})["marked"], 1)
        self.assertEqual(self.client.get("/api/notifications")["unread"], 0)
        self.assertEqual(self.collector.store.event(self.hidden)["acked_at"], None, "unwatched stays untouched")


class ClickLinks(unittest.TestCase):
    def test_batch_links_to_newest_event_and_test_to_list(self):
        rig = Rig(presence={"mode": "always"}, notify={"dashboard_url": "https://dash.example/"})
        rig.finish("w1:p1", "a")
        rig.finish("w1:p2", "b")
        rig.notifier.tick()
        newest = rig.store.recent_events(1)[0]["id"]
        self.assertTrue(rig.sender.sent[-1]["click"].endswith(f"#/n/{newest}"))

    def test_no_dashboard_url_means_no_click(self):
        rig = Rig(presence={"mode": "always"})
        rig.finish("w1:p1", "a")
        rig.notifier.tick()
        self.assertEqual(rig.sender.sent[0]["click"], "")

    def test_at_label_is_amsterdam_time(self):
        self.assertEqual(at_label(1763217720, settings()), "15 Nov 15:42")
