"""Waiting states and link actions (activity.py, flow.py): fixture traces, no Herdr."""

import unittest

from herdr_attention import classify as rules
from herdr_attention.relations import Registry
from herdr_attention.store import Store
from herdr_attention.traces import ClaudeTrace, CodexTrace
from tests.helpers import Clock, agent, settings, workspaces
from tests.test_engine import make

AT0 = 1795514400.0


def ts(seconds):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(AT0 + seconds, timezone.utc).isoformat().replace("+00:00", "Z")


def tool_use(at, call_id, name, **args):
    return {"type": "assistant", "timestamp": ts(at),
            "message": {"content": [{"type": "tool_use", "id": call_id, "name": name, "input": args}]}}


def tool_result(at, call_id, text="ok"):
    return {"type": "user", "timestamp": ts(at),
            "message": {"content": [{"type": "tool_result", "tool_use_id": call_id, "content": text}]}}


class ClaudeActivity(unittest.TestCase):
    def test_open_command_then_result(self):
        t = ClaudeTrace()
        t.feed(tool_use(0, "u1", "Bash", command="cd /x && pytest -q --token=abc", description="Run the full suite"))
        running = t.summary()["activity"]["running"]
        self.assertEqual((running["kind"], running["what"]), ("shell", "Run the full suite"))
        t.feed(tool_result(30, "u1"))
        self.assertIsNone(t.summary()["activity"]["running"])

    def test_command_without_description_keeps_only_program_and_first_argument(self):
        t = ClaudeTrace()
        t.feed(tool_use(0, "u1", "Bash", command="cd /x && curl -H 'Authorization: secret' https://y"))
        self.assertEqual(t.summary()["activity"]["running"]["what"], "curl -H")

    def test_background_task_until_its_notification(self):
        t = ClaudeTrace()
        t.feed(tool_use(0, "u1", "Bash", command="make test", description="Suite", run_in_background=True))
        t.feed(tool_result(1, "u1", "Command running in background with ID: bk1. Output is being written to: /tmp/x"))
        act = t.summary()["activity"]
        self.assertIsNone(act["running"])
        self.assertEqual((act["background"]["count"], act["background"]["what"]), (1, "Suite"))
        t.feed({"type": "queue-operation", "timestamp": ts(90),
                "content": "<task-notification>\n<task-id>bk1</task-id>\n<status>completed</status>\n</task-notification>"})
        self.assertIsNone(t.summary()["activity"]["background"])

    def test_schedule_wakeup_and_fire(self):
        t = ClaudeTrace()
        t.feed(tool_use(0, "u1", "ScheduleWakeup", delaySeconds=600, reason="Check the suite"))
        t.feed(tool_result(0, "u1"))
        wake = t.summary()["activity"]["wakeup"]
        self.assertEqual((wake["due_at"], wake["what"]), (AT0 + 600, "Check the suite"))
        t.feed({"type": "system", "subtype": "scheduled_task_fire", "timestamp": ts(600)})
        self.assertIsNone(t.summary()["activity"]["wakeup"])

    def test_herdr_messages_and_waits(self):
        t = ClaudeTrace()
        t.feed(tool_use(0, "u1", "Bash", command='herdr agent prompt w1:p2 "Build the thing"'))
        t.feed(tool_result(1, "u1"))
        t.feed(tool_use(5, "u2", "Bash", command="herdr agent wait w1:p2 --until idle --timeout 600000"))
        act = t.summary()["activity"]
        self.assertEqual(act["running"]["kind"], "agent_wait")
        self.assertEqual(act["running"]["targets"], ["w1:p2"])
        self.assertEqual({(s["target"], s["verb"]) for s in act["sent"]}, {("w1:p2", "prompt"), ("w1:p2", "wait")})

    def test_turn_end_closes_calls_and_sidechains_are_ignored(self):
        t = ClaudeTrace()
        side = tool_use(0, "s1", "Bash", command="sleep 100")
        side["isSidechain"] = True
        t.feed(side)
        self.assertIsNone(t.summary()["activity"]["running"])
        t.feed(tool_use(0, "u1", "Bash", command="sleep 300"))
        self.assertEqual(t.summary()["activity"]["running"]["kind"], "sleep")
        self.assertEqual(t.summary()["activity"]["running"]["due_at"], AT0 + 300)
        t.feed({"type": "system", "subtype": "turn_duration", "timestamp": ts(10)})
        self.assertIsNone(t.summary()["activity"]["running"])


class CodexActivity(unittest.TestCase):
    def item(self, at, payload):
        return {"type": "response_item", "timestamp": ts(at), "payload": payload}

    def test_sleep_and_exec(self):
        t = CodexTrace()
        t.feed(self.item(0, {"type": "function_call", "name": "sleep", "call_id": "c1", "arguments": '{"duration_ms":60000}'}))
        running = t.summary()["activity"]["running"]
        self.assertEqual((running["kind"], running["due_at"]), ("sleep", AT0 + 60))
        t.feed(self.item(60, {"type": "function_call_output", "call_id": "c1", "output": "done"}))
        code = 'await tools.exec_command({cmd:"herdr agent prompt coord-1 \\"rel-1: done\\""})'
        t.feed(self.item(61, {"type": "custom_tool_call", "name": "exec", "call_id": "c2", "input": code}))
        act = t.summary()["activity"]
        self.assertEqual(act["running"]["kind"], "shell")
        self.assertEqual([(s["target"], s["verb"], s["open"]) for s in act["sent"]], [("coord-1", "prompt", True)])
        t.feed({"type": "event_msg", "timestamp": ts(70), "payload": {"type": "task_complete"}})
        self.assertIsNone(t.summary()["activity"]["running"])


def trace(read_at, **activity):
    base = {"running": None, "background": None, "wakeup": None, "sent": []}
    base.update(activity)
    return {"available": True, "caught_up": True, "read_at": read_at, "activity": base}


class GraphFlow(unittest.TestCase):
    def setUp(self):
        self.engine, self.store, self.clock = make()
        self.engine.relations = Registry(self.store)
        self.engine.apply_snapshot([agent("w1:p1", "idle", session="boss", name="coord"),
                                    agent("w1:p2", "working", session="wkr", name="builder")], workspaces("w1"))
        self.boss, self.worker = "claude:boss", "claude:wkr"
        self.engine.relations.link(self.worker, "reports_to", self.boss, at=self.clock(), provenance="manual", actor="test")

    def view(self):
        view = self.engine.view()
        agents = {a["identity"]: a for a in view["agents"]}
        link = next(l for l in view["links"] if l["kind"] == "reports_to")
        return agents, link

    def test_idle_coordinator_waits_for_its_busy_worker(self):
        now = self.clock()
        self.engine.set_trace(self.boss, trace(now, sent=[{"target": "w1:p2", "verb": "prompt", "at": now - 60, "open": False}]))
        agents, link = self.view()
        wait = agents[self.boss]["waiting_for"]
        self.assertEqual((wait["kind"], wait["targets"], wait["label"]), ("worker", [self.worker], "Waiting for builder"))
        self.assertTrue(link["waiting"])
        self.assertEqual((link["last_action"]["kind"], link["last_action"]["from"]), ("task", self.boss))

    def test_worker_report_and_finish_point_back(self):
        now = self.clock()
        self.engine.set_trace(self.boss, trace(now, sent=[{"target": "builder", "verb": "prompt", "at": now - 300, "open": False}]))
        self.clock.advance(60)
        self.engine.apply_agent(agent("w1:p2", "done", session="wkr", name="builder"))
        agents, link = self.view()
        self.assertIsNone(agents[self.boss]["waiting_for"])
        self.assertFalse(link["waiting"])
        self.assertEqual((link["last_action"]["kind"], link["last_action"]["from"]), ("finished", self.worker))
        self.engine.set_trace(self.worker, trace(self.clock(), sent=[{"target": "w1:p1", "verb": "prompt", "at": self.clock() + 1, "open": False}]))
        self.assertEqual(self.view()[1]["last_action"]["kind"], "report")

    def test_running_command_wakeup_and_background(self):
        now = self.clock()
        self.engine.set_trace(self.worker, trace(now, running={"kind": "shell", "tool": "Bash", "what": "Run tests", "since": now - 120}))
        self.assertEqual(self.view()[0][self.worker]["waiting_for"]["label"], "Running: Run tests")
        self.engine.apply_agent(agent("w1:p2", "idle", session="wkr", name="builder"))
        self.engine.set_trace(self.worker, trace(now, wakeup={"due_at": now + 300, "set_at": now - 5, "what": "Check CI"}))
        wait = self.view()[0][self.worker]["waiting_for"]
        self.assertEqual((wait["kind"], wait["due_at"]), ("scheduled", now + 300))
        self.engine.set_trace(self.worker, trace(now, background={"count": 2, "what": "Suite", "since": now - 9, "until": now + 600}))
        self.assertEqual(self.view()[0][self.worker]["waiting_for"]["label"], "Background task: Suite (+1 more)")

    def test_stale_trace_says_nothing(self):
        now = self.clock()
        self.engine.set_trace(self.worker, trace(now - 3600, running={"kind": "shell", "tool": "Bash", "what": "x", "since": now - 4000}))
        self.assertIsNone(self.view()[0][self.worker]["waiting_for"])


class InProcessSubagents(unittest.TestCase):
    def test_a_subagent_never_takes_its_parents_waiting_state(self):
        from herdr_attention import flow
        now = 1_800_000_000.0
        parent = {"present": True, "trace": trace(now, background={"count": 1, "what": "Suite", "since": now - 60, "until": now + 600})}
        cards = [{"identity": "claude:p", "present": True, "status": "idle"},
                 {"identity": "claude:p/subagent/a1", "present": True, "status": "done", "in_process": True,
                  "waiting_for": {"kind": "shell", "label": "copied"}}]
        flow.decorate(cards, [], {"claude:p": parent}, now, fresh_seconds=180, patterns=[])
        self.assertEqual(cards[0]["waiting_for"]["kind"], "shell")
        self.assertIsNone(cards[1]["waiting_for"])


class StallIsNotWaiting(unittest.TestCase):
    def quiet_agent(self, now, running=None):
        return {"status": "working", "present": True, "workspace_id": "w1", "status_since": now - 3600, "since_known": True,
                "output_checked_at": now - 10, "output_changed_at": now - 3000, "output_unchanged_checks": 40,
                "trace": trace(now, running=running)}

    def test_long_command_is_not_a_stall(self):
        conf = settings(stall={"output_poll_seconds": 60, "minutes": 15})
        now = Clock()()
        self.assertEqual(rules.classify(self.quiet_agent(now), now, conf).kind, "stall")
        found = rules.classify(self.quiet_agent(now, {"kind": "shell", "what": "pytest", "since": now - 3000}), now, conf)
        self.assertEqual((found.kind, found.evidence["waiting_on"]), ("working", "waiting for command output"))


if __name__ == "__main__":
    unittest.main()
