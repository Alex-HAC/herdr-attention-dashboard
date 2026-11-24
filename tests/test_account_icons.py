"""v0.9.7 account icons: the icon object, defaults, never guessing, the setting, glyphs and colours."""
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

from herdr_attention import account_icons, herdr_theme
from herdr_attention.config import EDITABLE, Settings
from herdr_attention.quotas import enrich
from herdr_attention.ui_contract import CONTRACT
from tests.test_quotas import snapshot

REPO = Path(__file__).resolve().parent.parent
KEYS = {"account", "label", "glyph", "color", "badge", "image", "custom"}


def accounts():
    return enrich(snapshot(), [])["accounts"]


def view_with(agents, saved=None, images=None):
    view = {"quotas": {"accounts": accounts()}, "agents": agents}
    account_icons.decorate(view, saved or {}, images)
    return view


def icon_of(view, account):
    return next(a["icon"] for a in view["quotas"]["accounts"] if a["id"] == account)


class Defaults(unittest.TestCase):
    def test_every_account_gets_the_contract_object(self):
        view = view_with([])
        for account in view["quotas"]["accounts"]:
            self.assertEqual(set(account["icon"]), KEYS, account["id"])
            self.assertEqual(account["icon"]["account"], account["id"])
            self.assertIn(account["icon"]["color"], account_icons.COLORS)
            self.assertNotRegex(json.dumps(account["icon"]), r"#[0-9a-fA-F]{6}")   # names, never hex

    def test_personal_and_work_differ_at_a_glance(self):
        view = view_with([])
        for a, b in (("claude:main", "claude:personal"), ("codex", "codex-personal:personal")):
            x, y = icon_of(view, a), icon_of(view, b)
            self.assertEqual(x["glyph"], y["glyph"])
            self.assertNotEqual(x["color"], y["color"])
            self.assertNotEqual(x["badge"], y["badge"])
        self.assertEqual((icon_of(view, "codex")["badge"], icon_of(view, "codex-personal:personal")["badge"]), ("W", "P"))
        self.assertEqual((icon_of(view, "claude:main")["label"], icon_of(view, "codex")["label"]), ("Claude Code Main", "Codex Work"))

    def test_one_account_per_mark_has_no_badge_and_providers_get_their_mark(self):
        view = view_with([])
        self.assertEqual({a: icon_of(view, a)["glyph"] for a in ("cursor", "agy", "grokbot", "copilot")},
                         {"cursor": "cursor", "agy": "agy", "grokbot": "grokbot", "copilot": "copilot"})
        self.assertIsNone(icon_of(view, "cursor")["badge"])

    def test_an_unknown_provider_shows_its_text_label(self):
        icons = account_icons.defaults([{"id": "newco", "provider": "newco", "label": "NewCo"}])
        self.assertEqual(icons["newco"], {"glyph": None, "color": "neutral", "badge": None})


class NeverGuess(unittest.TestCase):
    def agent(self, identity, account=None, **extra):
        return {"identity": identity, "pane_id": "w1:p1", "present": True,
                **({"launch": {"account": account}} if account else {}), **extra}

    def test_only_agents_with_a_recorded_matching_account_get_an_icon(self):
        agents = [self.agent("claude:a", "claude-personal"), self.agent("codex:b", "codex-work"),
                  self.agent("claude:c"),                                   # a Claude agent, account not recorded
                  self.agent("claude:d", "claude-somewhere-else"),         # recorded, matches no account
                  self.agent("claude:e", "claude-personal", in_process=True)]
        view = view_with(agents)
        got = {a["identity"]: (a.get("icon") or {}).get("account") for a in view["agents"]}
        self.assertEqual(got, {"claude:a": "claude:personal", "codex:b": "codex", "claude:c": None, "claude:d": None, "claude:e": None})
        self.assertNotIn("icon", view["agents"][2])

    def test_questions_carry_the_icon_of_the_recorded_account(self):
        item = {"asked_by": {"identity": "claude:a", "name": "lead"}, "answer_to": {"identity": "claude:c", "name": "x"}}
        agents = [self.agent("claude:a", "claude-main", asks={"items": [item]}), self.agent("claude:c")]
        view_with(agents)
        self.assertEqual(item["asked_by"]["icon"]["account"], "claude:main")
        self.assertNotIn("icon", item["answer_to"])


class Saved(unittest.TestCase):
    def test_choices_override_the_default_and_mark_it_custom(self):
        class Images:
            def version(self, account):
                return "abc123abc123" if account in ("cursor", "agy") else None
        saved = {"codex": {"color": "red", "badge": "Wk"}, "copilot": {"glyph": None},
                 "cursor": {"image": True}, "agy": {"image": False}, "claude:main": {"badge": None}}
        view = view_with([], saved, Images())
        codex = icon_of(view, "codex")
        self.assertEqual((codex["glyph"], codex["color"], codex["badge"], codex["custom"]), ("codex", "red", "Wk", True))
        self.assertEqual((icon_of(view, "copilot")["glyph"], icon_of(view, "copilot")["image"]), (None, None))
        self.assertEqual(icon_of(view, "cursor")["image"], "/api/account-icons/cursor?v=abc123abc123")
        self.assertIsNone(icon_of(view, "agy")["image"])                          # image switched off
        self.assertIsNone(icon_of(view, "claude:main")["badge"])
        self.assertFalse(icon_of(view, "claude:personal")["custom"])
        self.assertEqual(icon_of(view, "codex-personal:personal")["image"], None)

    def test_the_setting_is_validated_and_phone_editable(self):
        s = Settings()
        self.assertTrue(EDITABLE["accounts.icons"][1])
        ok = {"codex": {"glyph": "star", "color": "cyan", "badge": "W2", "image": False}, "copilot": {"glyph": None, "badge": ""}}
        self.assertEqual(s.validate("accounts.icons", ok, from_phone=True),
                         {"codex": {"glyph": "star", "color": "cyan", "badge": "W2", "image": False}, "copilot": {"glyph": None, "badge": None}})
        for bad in ({"codex": {"color": "#ff0000"}}, {"codex": {"glyph": "openai-logo"}}, {"codex": {"badge": "Wor"}},
                    {"codex": {"badge": " W"}}, {"codex": {"image": "yes"}}, {"codex": {"path": "/etc/passwd"}},
                    {"": {"color": "red"}}, ["codex"]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                s.validate("accounts.icons", bad)

    def test_a_bad_value_in_the_file_falls_back_to_the_defaults(self):
        self.assertEqual(Settings({"accounts": {"icons": {"codex": {"color": "pink"}}}}).account_icons(), {})


class GlyphsAndColours(unittest.TestCase):
    def test_glyphs_are_plain_paths(self):
        glyphs = CONTRACT["account_icons"]["glyphs"]
        self.assertEqual(len({g["id"] for g in glyphs}), len(glyphs))
        # The ids saved in accounts.icons before 0.9.8.5 still exist.
        self.assertTrue({"claude", "codex", "cursor", "gemini", "grok", "copilot", "circle", "square", "triangle",
                         "diamond", "star", "hex"} <= {g["id"] for g in glyphs})
        for g in glyphs:
            self.assertRegex(g["d"], r"^[MmLlHhVvAaZzCcQqSsTt0-9 .,\-]+$", g["id"])
            self.assertTrue(g["label"])
            self.assertIn(g.get("paint", "stroke"), ("stroke", "fill"), g["id"])
            self.assertRegex(g.get("view_box", "0 0 24 24"), r"^-?[\d.]+ -?[\d.]+ [\d.]+ [\d.]+$", g["id"])
            if g.get("paint") == "fill":
                self.assertIn(g["fill_rule"], ("evenodd", "nonzero"), g["id"])

    def test_brand_marks_come_from_the_vendored_omarchy_files(self):
        """v0.9.8.5: Omarchy's agent marks (MIT), copied into third_party/omarchy-agents with the licence."""
        here = REPO / "third_party/omarchy-agents"
        self.assertIn("Permission is hereby granted", (here / "LICENSE").read_text())
        self.assertIn("David Heinemeier Hansson", (here / "LICENSE").read_text())
        glyphs = {g["id"]: g for g in CONTRACT["account_icons"]["glyphs"]}
        for gid in ("claude", "codex", "cursor", "agy", "grok", "grokbot", "copilot", "opencode"):
            src = glyphs[gid]["source"]
            self.assertTrue(src.startswith("omarchy.agents/assets/"), gid)
            self.assertTrue((here / src.rsplit("/", 1)[1]).is_file(), gid)
        self.assertNotIn("source", glyphs["gemini"])   # no Gemini mark in Omarchy: ours stays
        self.assertIn("third_party/omarchy-agents", (REPO / "README.md").read_text())
        # The contract's paths are exactly what the import script makes from the files.
        r = subprocess.run([sys.executable, str(REPO / "scripts/import_omarchy_icons.py"), "--check"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_every_colour_name_is_a_live_theme_token(self):
        tokens = herdr_theme.fallback_theme()["tokens"]
        for name in account_icons.COLORS:
            self.assertRegex(tokens[f"icon-{name}"], r"^#[0-9a-f]{6}$")
            self.assertGreaterEqual(herdr_theme.contrast(tokens[f"icon-{name}"], tokens["overlay"]), 4.5, name)
        css = (REPO / "herdr_attention/web/style.css").read_text()
        for name in account_icons.COLORS:
            self.assertIn(f".acct-{name} {{ --acct: var(--icon-{name}); }}", css)

    def test_the_bar_panel_gets_the_same_marks(self):
        text = (REPO / "plugin/alexvdb.herdr-attention/Contract.js").read_text()
        data = json.loads(re.search(r"var UI = (\{.*\})\s*$", text, re.S).group(1))
        self.assertEqual(data["account_icons"]["glyphs"], CONTRACT["account_icons"]["glyphs"])
        self.assertIn("iconBlue", (REPO / "plugin/alexvdb.herdr-attention/Tokens.js").read_text())


class InTheView(unittest.TestCase):
    def test_the_page_fixture_has_every_kind(self):
        from tests.icon_fixture import RECORDED, icon_fixture
        view = icon_fixture()["view"]
        icons = {a["id"]: a["icon"] for a in view["quotas"]["accounts"]}
        self.assertEqual((icons["codex"]["color"], icons["codex"]["badge"]), ("red", "Wk"))
        self.assertTrue(icons["cursor"]["image"].startswith("data:image/png"))
        self.assertEqual((icons["copilot"]["glyph"], icons["copilot"]["image"]), (None, None))
        with_icon = {a["name"] for a in view["agents"] if a.get("icon")}
        self.assertEqual(with_icon, set(RECORDED))
        self.assertTrue(any(not a.get("icon") for a in view["agents"]))


if __name__ == "__main__":
    unittest.main()
