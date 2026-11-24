"""Trace parsing: metrics, dedupe, compaction, links, conversation, file safety. FIXTURE-BASED."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from herdr_attention import metrics
from herdr_attention.traces import Locator, TraceReader, split_identity
from tests.trace_fixtures import claude_trace, codex_trace, codex_usage, sid


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "claude").mkdir()
        (self.root / "codex").mkdir()
        self.reader = TraceReader(Locator([str(self.root / "claude")], [str(self.root / "codex")], retry_seconds=0))
        self.now = time.time()

    def tearDown(self):
        self.tmp.cleanup()

    def read_all(self, identity):
        for _ in range(100):
            summary = self.reader.update(identity)
            if not summary.get("available") or summary["caught_up"]:
                return summary
        raise AssertionError("never caught up")


class ClaudeMetrics(Base):
    def test_usage_counts_each_request_once_and_context_is_the_last_request(self):
        session = sid("c1")
        claude_trace(self.root, session, self.now - 3600, [
            {"user": "a", "reply": ["x", "y"], "usage": {"input_tokens": 1, "cache_creation_input_tokens": 10,
                                                        "cache_read_input_tokens": 100, "output_tokens": 5}},
            {"user": "b", "reply": ["z"], "retry": True, "usage": {"input_tokens": 2, "cache_creation_input_tokens": 20,
                                                                  "cache_read_input_tokens": 200, "output_tokens": 7}},
        ])
        s = self.read_all(f"claude:{session}")
        self.assertEqual(s["usage"], {"input": 3, "cache_write": 30, "cache_read": 300, "output": 12, "requests": 2})
        self.assertEqual(s["context"]["tokens"], 2 + 20 + 200 + 7)
        self.assertIsNone(s["context"]["window"])
        self.assertAlmostEqual(s["started_at"], self.now - 3600, delta=1)
        self.assertEqual(s["model"], "claude-test-1")

    def test_compaction_resets_context_not_lifetime_usage(self):
        session = sid("c2")
        big = {"input_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 150000, "output_tokens": 1000}
        small = {"input_tokens": 0, "cache_creation_input_tokens": 5000, "cache_read_input_tokens": 0, "output_tokens": 100}
        claude_trace(self.root, session, self.now, [{"user": "a", "reply": ["r"], "usage": big},
                                                    {"compact": True, "user": "b", "reply": ["r"], "usage": small}])
        s = self.read_all(f"claude:{session}")
        self.assertEqual(s["context"]["tokens"], 5100)
        self.assertEqual(s["compactions"], 1)
        self.assertEqual(s["usage"]["cache_read"], 150000)

    def test_sidechain_counts_for_usage_but_not_context(self):
        session = sid("c3")
        claude_trace(self.root, session, self.now, [
            {"user": "a", "reply": ["main"], "usage": {"input_tokens": 1, "cache_creation_input_tokens": 0,
                                                      "cache_read_input_tokens": 1000, "output_tokens": 0}},
            {"reply": ["side"], "sidechain": True, "usage": {"input_tokens": 1, "cache_creation_input_tokens": 0,
                                                            "cache_read_input_tokens": 9, "output_tokens": 0}},
        ])
        s = self.read_all(f"claude:{session}")
        self.assertEqual(s["context"]["tokens"], 1001)
        self.assertEqual(s["usage"]["cache_read"], 1009)

    def test_incremental_reads_with_a_small_budget(self):
        session = sid("c4")
        path = claude_trace(self.root, session, self.now, [{"user": f"q{i}", "reply": ["r"]} for i in range(40)])
        self.reader.max_bytes = 4096
        first = self.reader.update(f"claude:{session}")
        self.assertFalse(first["caught_up"])
        self.assertLess(first["read_percent"], 100)
        s = self.read_all(f"claude:{session}")
        self.assertEqual(s["usage"]["requests"], 40)
        # Appending later is picked up from the saved offset.
        with open(path, "a") as fh:
            fh.write(json.dumps({"type": "assistant", "requestId": "late", "timestamp": "2026-11-07T12:00:00Z",
                                 "message": {"id": "late", "usage": {"input_tokens": 1, "output_tokens": 1}}}) + "\n")
            fh.write('{"type": "assistant", "partial line without newline')
        s = self.read_all(f"claude:{session}")
        self.assertEqual(s["usage"]["requests"], 41)

    def test_replaced_file_starts_over(self):
        session = sid("c5")
        claude_trace(self.root, session, self.now, [{"user": "a", "reply": ["r"]} for _ in range(3)])
        self.assertEqual(self.read_all(f"claude:{session}")["usage"]["requests"], 3)
        path = claude_trace(self.root, session, self.now, [{"user": "a", "reply": ["r"]}])
        os.utime(path)
        self.assertEqual(self.read_all(f"claude:{session}")["usage"]["requests"], 1)


class CodexMetrics(Base):
    def test_cumulative_snapshot_latest_wins(self):
        session = sid("x1")
        codex_trace(self.root, session, self.now - 7200, [
            {"total": codex_usage(1000, 800, 50, 10), "last": codex_usage(500, 400, 5)},
            {"total": codex_usage(3000, 2500, 90, 30), "last": codex_usage(900, 800, 9), "window": 475000},
        ])
        s = self.read_all(f"codex:{session}")
        self.assertEqual(s["usage"], {"input": 500, "cache_read": 2500, "cache_write": 0, "output": 90, "reasoning": 30})
        self.assertEqual(s["context"], {"tokens": 909, "window": 475000, "as_of": s["context"]["as_of"]})
        self.assertAlmostEqual(s["started_at"], self.now - 7200, delta=1)
        self.assertEqual(s["links"], [])

    def test_spawn_and_fork_links(self):
        child, parent, origin = sid("child"), sid("parent"), sid("origin")
        codex_trace(self.root, child, self.now, [], parent=parent, fork=origin)
        s = self.read_all(f"codex:{child}")
        self.assertEqual({(l["kind"], l["target"]) for l in s["links"]},
                         {("spawned_by", f"codex:{parent}"), ("forked_from", f"codex:{origin}")})


class Metrics(Base):
    def test_provenance_and_unavailable(self):
        session = sid("m1")
        codex_trace(self.root, session, self.now - 3600, [{"total": codex_usage(100, 0, 10), "last": codex_usage(100, 0, 10)}])
        agent = {"trace": self.read_all(f"codex:{session}"), "first_seen": self.now - 60, "present": True,
                 "status": "working", "working_started": self.now - 120, "active_seconds": 300}
        m = metrics.build(agent, self.now)
        self.assertEqual(m["age"]["source"], "session start in the trace")
        self.assertTrue(m["age"]["exact"])
        self.assertAlmostEqual(m["active"]["value"], 420, delta=1)
        self.assertEqual(m["tokens"]["value"], 110)
        self.assertFalse(m["context"]["exact"])
        self.assertIn("source", m["context"])
        none = metrics.build({"first_seen": self.now - 600, "trace": {"available": False, "reason": "no trace"}}, self.now)
        self.assertFalse(none["context"]["available"])
        self.assertEqual(none["context"]["reason"], "no trace")
        self.assertEqual(none["age"]["kind"], "at least")
        self.assertIn("collector first saw", none["age"]["source"])
        self.assertNotIn("$", json.dumps(m))


class Conversation(Base):
    def test_roles_only_text_no_reasoning_or_tools_and_paging(self):
        session = sid("talk")
        claude_trace(self.root, session, self.now, [
            {"user": f"question {i} <system-reminder>INJECTED</system-reminder>", "reply": [f"answer {i}", f"more {i}"]}
            for i in range(6)])
        reader = self.reader
        page = reader.conversation(f"claude:{session}", 4, None, [])
        self.assertEqual([e["role"] for e in page["entries"]], ["user", "assistant", "user", "assistant"])
        self.assertEqual(page["entries"][-1]["text"], "answer 5\nmore 5")
        blob = json.dumps(page)
        for leak in ("HIDDEN REASONING", "TOOL INPUT", "INJECTED", "local-command-caveat", "retry duplicate"):
            self.assertNotIn(leak, blob)
        older = reader.conversation(f"claude:{session}", 4, page["before"], [])
        self.assertEqual(older["entries"][-1]["text"], "answer 3\nmore 3")
        texts = [e["text"] for e in older["entries"] + page["entries"]]
        self.assertEqual(len(texts), len(set(texts)), "pages do not overlap")
        rest = reader.conversation(f"claude:{session}", 50, older["before"], [])
        self.assertTrue(rest["complete"])
        self.assertIsNone(rest["before"])

    def test_codex_conversation_is_sanitized_and_redacted(self):
        session = sid("ctalk")
        codex_trace(self.root, session, self.now, [], messages=[
            ("user", "<environment_context>cwd</environment_context>"),
            ("user", "deploy with token=abc123 \x1b[31mnow\x1b[0m"),
            ("assistant", "<b>done</b>")])
        page = self.reader.conversation(f"codex:{session}", 10, None, [r"token=\S+"])
        self.assertEqual([(e["role"], e["text"]) for e in page["entries"]],
                         [("user", "deploy with [redacted] now"), ("assistant", "<b>done</b>")])
        self.assertEqual(page["entries"][0]["redactions"], 1)


class FileSafety(Base):
    def test_only_valid_ids_and_trace_files_inside_the_roots(self):
        self.assertIsNone(split_identity("claude:../../etc/passwd"))
        self.assertIsNone(split_identity("cursor:" + sid("x")))
        self.assertFalse(self.reader.update("claude:*")["available"])
        outside = Path(self.tmp.name).parent / f"{sid('escape')}.jsonl"
        outside.write_text('{"type": "assistant"}\n')
        try:
            (self.root / "claude" / "proj").mkdir()
            os.symlink(outside, self.root / "claude" / "proj" / f"{sid('escape')}.jsonl")
            self.assertFalse(self.reader.update(f"claude:{sid('escape')}")["available"], "symlink out of the root refused")
        finally:
            outside.unlink()
        (self.root / "claude" / "proj" / ".credentials.json").write_text("{}")
        self.assertFalse(self.reader.update("claude:" + sid("nope"))["available"])

    def test_missing_trace_is_reported_not_invented(self):
        s = self.reader.update(f"codex:{sid('missing')}")
        self.assertEqual(s, {"available": False, "reason": "no codex trace found for this session"})


if __name__ == "__main__":
    unittest.main()
