"""v0.9.5: what each agent runs on (service, model, effort), from recorded sources only."""

import tempfile
import time
import unittest
from pathlib import Path

from herdr_attention import agent_runtime, metrics
from herdr_attention.traces import ClaudeTrace, CodexTrace, Locator, TraceReader
from herdr_attention.ui_contract import CONTRACT
from tests.trace_fixtures import claude_trace, codex_trace, codex_usage, sid


class ModelLabels(unittest.TestCase):
    def test_recorded_ids_get_short_names(self):
        cases = {
            "claude-opus-5-5": "Opus 5.5",
            "claude-fable-5-2": "Fable 5.2",
            "claude-sonnet-4-5-20250929": "Sonnet 4.5",
            "claude-sonnet-4-20250514": "Sonnet 4",
            "claude-opus-4-1-20250805": "Opus 4.1",
            "claude-3-5-sonnet-20241022": "Sonnet 3.5",
            "claude-opus-4-6[1m]": "Opus 4.6 1M",
            "fable": "Fable",
            "opus[1m]": "Opus 1M",
            "gpt-6.2-sol": "GPT-6.2 Sol",
            "gpt-6.1-sol": "GPT-6.1 Sol",
            "gpt-6-astra": "GPT-6 Astra",
            "gpt-5.1-codex-max": "GPT-5.1 Codex Max",
            "gemini-2.5-pro": "Gemini 2.5 Pro",
        }
        for model, label in cases.items():
            with self.subTest(model=model):
                self.assertEqual(agent_runtime.model_label(model), label)

    def test_unknown_patterns_are_shown_as_recorded(self):
        for model in ("gpt-4o", "o3", "some-new-model", "Kimi K2"):
            with self.subTest(model=model):
                self.assertEqual(agent_runtime.model_label(model), model)

    def test_no_value_or_unsafe_text_gives_no_label(self):
        for value in (None, "", "  ", 42, "x" * 81, "bad\nid"):
            with self.subTest(value=value):
                self.assertIsNone(agent_runtime.model_label(value))


class Effort(unittest.TestCase):
    def test_the_scale_is_low_to_max_in_five_steps(self):
        levels = {w: agent_runtime.effort(w)["level"] for w in ("low", "medium", "high", "xhigh", "max")}
        self.assertEqual(levels, {"low": 1, "medium": 2, "high": 3, "xhigh": 4, "max": 5})
        self.assertEqual(agent_runtime.effort("xhigh")["label"], "extra high")
        self.assertEqual(agent_runtime.effort("HIGH")["level"], 3)
        self.assertEqual(agent_runtime.effort("high")["steps"], 5)

    def test_below_the_scale_is_zero_and_an_unknown_word_has_no_dots(self):
        self.assertEqual(agent_runtime.effort("minimal")["level"], 0)
        self.assertEqual(agent_runtime.effort("none")["level"], 0)
        ultra = agent_runtime.effort("ultra")
        self.assertIsNone(ultra["level"])          # recorded, but never mapped onto the dots
        self.assertEqual(ultra["label"], "ultra")
        self.assertIsNone(agent_runtime.effort(None))


class Services(unittest.TestCase):
    def test_known_and_unknown_agent_kinds(self):
        self.assertEqual(agent_runtime.service("claude"), {"id": "claude", "label": "Claude Code", "short": "CC", "name": "Claude"})
        self.assertEqual(agent_runtime.service("codex")["short"], "CX")
        self.assertEqual(agent_runtime.service("agy")["label"], "Antigravity")
        self.assertEqual(agent_runtime.service("pi-agent"), {"id": "pi-agent", "label": "pi-agent", "short": "PI", "name": "pi-agent"})
        self.assertIsNone(agent_runtime.service(None))

    def test_every_service_in_the_contract_has_a_two_letter_short(self):
        for kind, entry in CONTRACT["runtime"]["services"].items():
            with self.subTest(kind=kind):
                self.assertRegex(entry["short"], r"^[A-Z]{2}$")
                self.assertTrue(entry["label"])


def card(**fields):
    base = {"identity": "claude:x", "agent": "claude", "metrics": {}, "launch": None}
    base.update(fields)
    return base


class Describe(unittest.TestCase):
    def test_trace_values_win_over_the_launch_record(self):
        r = agent_runtime.describe(card(
            metrics={"model": "claude-opus-5-5", "effort": "xhigh", "runtime_at": 100.0,
                     "trace": {"available": True, "caught_up": True}},
            launch={"kind": "claude", "model": "fable", "effort": "high"}))
        self.assertEqual(r["model"], {"id": "claude-opus-5-5", "label": "Opus 5.5", "from": "trace"})
        self.assertEqual((r["effort"]["id"], r["effort"]["from"], r["effort"]["level"]), ("xhigh", "trace", 4))
        self.assertEqual(r["trace_at"], 100.0)
        self.assertFalse(r["reading"])

    def test_each_value_falls_back_to_the_launch_record_on_its_own(self):
        # An older Claude Code writes the model but not the effort.
        r = agent_runtime.describe(card(
            metrics={"model": "claude-sonnet-5-5", "effort": None, "trace": {"available": True}},
            launch={"kind": "claude", "effort": "medium"}))
        self.assertEqual(r["model"]["from"], "trace")
        self.assertEqual((r["effort"]["id"], r["effort"]["from"]), ("medium", "launch"))

    def test_launch_only(self):
        r = agent_runtime.describe(card(agent="agy", identity="agy:x",
                                        launch={"kind": "agy", "model": "gemini-3-pro", "effort": "high"}))
        self.assertEqual(r["service"]["label"], "Antigravity")
        self.assertEqual((r["model"]["label"], r["model"]["from"]), ("Gemini 3 Pro", "launch"))
        self.assertIsNone(r["trace_at"])

    def test_nothing_recorded_means_nothing_shown(self):
        r = agent_runtime.describe(card())
        self.assertEqual(r["service"]["short"], "CC")
        self.assertIsNone(r["model"])
        self.assertIsNone(r["effort"])

    def test_a_launch_record_of_another_kind_is_ignored(self):
        r = agent_runtime.describe(card(launch={"kind": "codex", "model": "gpt-6.2-sol", "effort": "high"}))
        self.assertIsNone(r["model"])
        self.assertIsNone(r["effort"])

    def test_a_trace_that_is_still_being_read_is_flagged(self):
        r = agent_runtime.describe(card(metrics={"model": "gpt-6.2-sol", "trace": {"available": True, "caught_up": False}},
                                        agent="codex"))
        self.assertTrue(r["reading"])

    def test_in_process_subagent_keeps_the_service_but_claims_no_model(self):
        r = agent_runtime.describe(card(in_process=True, metrics={}))
        self.assertTrue(r["in_process"])
        self.assertEqual(r["service"]["id"], "claude")
        self.assertIsNone(r["model"])


class TraceParsing(unittest.TestCase):
    def feed_claude(self, *entries):
        trace = ClaudeTrace()
        for i, extra in enumerate(entries):
            entry = {"type": "assistant", "timestamp": f"2026-11-17T10:00:{i:02d}Z", "requestId": f"r{i}",
                     "message": {"id": f"m{i}", "model": "claude-opus-5-5", "usage": {"output_tokens": 1}}}
            message = extra.pop("message", {})
            entry.update(extra)
            entry["message"].update(message)
            trace.feed(entry)
        return trace.summary()

    def test_claude_effort_and_model_of_the_latest_main_thread_request(self):
        s = self.feed_claude({"effort": "high"}, {"effort": "xhigh", "perTurnEffort": "xhigh"})
        self.assertEqual((s["model"], s["effort"]), ("claude-opus-5-5", "xhigh"))
        self.assertIsNotNone(s["runtime_at"])

    def test_claude_per_turn_effort_is_used_when_effort_is_missing(self):
        self.assertEqual(self.feed_claude({"perTurnEffort": "medium"})["effort"], "medium")

    def test_claude_subagent_lines_and_synthetic_messages_do_not_change_it(self):
        s = self.feed_claude({"effort": "high"},
                             {"isSidechain": True, "effort": "low", "message": {"model": "claude-haiku-4-5"}},
                             {"message": {"model": "<synthetic>"}})
        self.assertEqual((s["model"], s["effort"]), ("claude-opus-5-5", "high"))

    def test_claude_without_effort_records_none(self):
        self.assertIsNone(self.feed_claude({})["effort"])

    def test_codex_turn_context_model_and_effort_latest_wins(self):
        trace = CodexTrace()
        trace.feed({"timestamp": "2026-11-17T10:00:00Z", "type": "turn_context", "payload": {"model": "gpt-6.2-sol", "effort": "high"}})
        trace.feed({"timestamp": "2026-11-17T10:05:00Z", "type": "turn_context", "payload": {"model": "gpt-6.2-sol", "effort": "low"}})
        s = trace.summary()
        self.assertEqual((s["model"], s["effort"]), ("gpt-6.2-sol", "low"))
        self.assertEqual(s["runtime_at"], 1794909900.0)


class FromTraceFiles(unittest.TestCase):
    def test_reader_to_metrics_to_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = time.time()
            claude_trace(root, sid("a"), now - 600, [{"user": "go", "reply": ["ok"]}], model="claude-fable-5-2", effort="high")
            codex_trace(root, sid("b"), now - 600, [{"total": codex_usage(10, 5, 1), "last": codex_usage(10, 5, 1)}],
                        model="gpt-6.2-sol", effort="xhigh")
            reader = TraceReader(Locator([str(root / "claude")], [str(root / "codex")], retry_seconds=0))
            for kind, session, label, level in (("claude", sid("a"), "Fable 5.2", 3), ("codex", sid("b"), "GPT-6.2 Sol", 4)):
                with self.subTest(kind=kind):
                    m = metrics.build({"trace": reader.update(f"{kind}:{session}")}, now)
                    r = agent_runtime.describe({"agent": kind, "metrics": m, "launch": None})
                    self.assertEqual((r["model"]["label"], r["effort"]["level"], r["model"]["from"]), (label, level, "trace"))


class ServedView(unittest.TestCase):
    """The real collector over fixture traces and a launch record (tests/web_fixture.py)."""

    @classmethod
    def setUpClass(cls):
        from tests.web_fixture import page_fixture
        cls.data = page_fixture()
        cls.by_name = {a.get("name"): a for a in cls.data["view"]["agents"]}

    def test_every_agent_card_carries_runtime(self):
        for a in self.data["view"]["agents"]:
            with self.subTest(agent=a.get("name")):
                self.assertIn("runtime", a)
                self.assertEqual(a["runtime"]["service"]["id"], a["agent"])

    def test_values_and_their_sources(self):
        lead, docs, spike = self.by_name["web-lead"], self.by_name["docs-writer"], self.by_name["old-spike"]
        self.assertEqual((lead["runtime"]["model"]["label"], lead["runtime"]["effort"]["id"]), ("Opus 5.5", "xhigh"))
        self.assertEqual(lead["runtime"]["effort"]["from"], "trace")
        self.assertIsNone(docs["runtime"]["effort"])                    # not in its trace: not shown
        self.assertEqual((spike["runtime"]["model"]["from"], spike["runtime"]["effort"]["from"]), ("launch", "launch"))

    def test_the_agent_detail_carries_it_too(self):
        lead = self.by_name["web-lead"]
        self.assertEqual(self.data["details"][lead["identity"]]["agent"]["runtime"], lead["runtime"])


if __name__ == "__main__":
    unittest.main()
