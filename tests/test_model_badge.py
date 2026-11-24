"""v0.9.7.4: the model in use replaces the CC / CX service badge."""

import unittest
from pathlib import Path

from herdr_attention import agent_runtime as rt

WEB = Path(__file__).resolve().parent.parent / "herdr_attention" / "web"


class CompactLabels(unittest.TestCase):
    def test_mapping(self):
        cases = {
            "gpt-6.2-sol": "Sol 6.2", "gpt-6.1-sol": "Sol 6.1", "gpt-6-astra": "Astra 6", "gpt-6-luna": "Luna 6",
            "claude-fable-5-2": "Fable 5.2", "claude-opus-5-5": "Opus 5.5", "claude-opus-4-6[1m]": "Opus 4.6",
            "claude-sonnet-4-5-20250929": "Sonnet 4.5", "claude-3-5-sonnet-20241022": "Sonnet 3.5",
            "opus": "Opus", "sonnet": "Sonnet", "grok-4.7-high": "Grok 4.7", "grok-4.7": "Grok 4.7",
            "gpt-5.6-sol-high": "Sol 5.6", "gpt-5.6-sol-xhigh-fast": "Sol 5.6", "claude-opus-5-thinking-high": "Opus 5",
            "gpt-5.1-codex-max": "Codex Max 5.1", "gpt-5": "GPT-5", "gemini-2.5-pro": "Gemini 2.5 Pro",
            "some-new-model": "some-new-model", "o3": "o3",
        }
        for model, label in cases.items():
            with self.subTest(model=model):
                self.assertEqual(rt.compact_label(model), label)

    def test_badge_is_the_model_else_the_service_by_name_never_cc_or_cx(self):
        card = lambda **kw: {"agent": kw.pop("agent", "codex"), **kw}
        b = rt.describe(card(metrics={"model": "gpt-6.2-sol", "effort": "high"}))["badge"]
        self.assertEqual((b["text"], b["from"], b["title"]), ("Sol 6.2", "model", "gpt-6.2-sol, effort high"))
        # v0.9.8.6: one chip text for every service, "Model · effort".
        self.assertEqual((b["label"], b["effort"]), ("Sol 6.2 \u00b7 high", "high"))
        self.assertEqual(rt.describe(card(launch={"kind": "claude", "model": "claude-fable-5-2"}, agent="claude"))["badge"]["text"], "Fable 5.2")
        for kind, name in (("codex", "Codex"), ("claude", "Claude"), ("cursor", "Cursor"), ("agy", "Antigravity"), ("newtool", "newtool")):
            with self.subTest(kind=kind):
                b = rt.describe(card(agent=kind))["badge"]
                self.assertEqual((b["text"], b["from"]), (name, "service"))
                self.assertNotIn(b["text"], ("CC", "CX", "CU", "AG"))
        self.assertIsNone(rt.describe({"agent": None})["badge"])

    def test_claude_and_codex_get_the_same_chip(self):
        """v0.9.8.6: Alex saw Claude and Codex models drawn differently. Same fields, same format."""
        claude = rt.describe({"agent": "claude", "metrics": {"model": "claude-opus-5-5", "effort": "high"}})["badge"]
        codex = rt.describe({"agent": "codex", "launch": {"kind": "codex", "model": "gpt-6.2-sol", "effort": "high"}})["badge"]
        self.assertEqual(set(claude), set(codex))
        self.assertEqual((claude["label"], codex["label"]), ("Opus 5.5 \u00b7 high", "Sol 6.2 \u00b7 high"))
        self.assertEqual(rt.describe({"agent": "claude", "metrics": {"model": "claude-opus-5-5", "effort": "xhigh"}})["badge"]["label"],
                         "Opus 5.5 \u00b7 xhigh")
        self.assertEqual(rt.describe({"agent": "codex", "metrics": {"model": "gpt-6-astra", "effort": "medium"}})["badge"]["label"],
                         "Astra 6 \u00b7 med")
        # Model without effort: the model alone, and the title says the effort is not recorded.
        only = rt.describe({"agent": "claude", "metrics": {"model": "claude-fable-5-2"}})["badge"]
        self.assertEqual((only["label"], only["effort"]), ("Fable 5.2", None))
        self.assertIn("effort not recorded", only["title"])
        # No model (an in-process subagent): the service by name, from "service", an honest unknown.
        for kind, name in (("claude", "Claude"), ("codex", "Codex")):
            b = rt.describe({"agent": kind, "in_process": True, "metrics": {"effort": "high"}})["badge"]
            self.assertEqual((b["label"], b["from"], b["effort"]), (name, "service", None))
            self.assertIn("model not recorded", b["title"])

    def test_screens_use_the_badge_not_the_short_code(self):
        app, graph = (WEB / "app.js").read_text(), (WEB / "graph.js").read_text()
        self.assertNotIn("service.short", graph)
        self.assertNotIn('text: "CC"', graph)
        self.assertIn("rt.badge", graph)   # line 2 of a node: badge text (the model)
        self.assertGreaterEqual(app.count("modelBadge("), 6)
        qml = (WEB.parent.parent / "plugin/alexvdb.herdr-attention/AgentRow.qml").read_text()
        self.assertIn("runtime.badge", qml)


if __name__ == "__main__":
    unittest.main()
