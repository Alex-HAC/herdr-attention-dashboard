"""v0.9.5 bar panel: theme colours from view.theme.tokens and the saved Arrange order."""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from herdr_attention import ui_contract
from herdr_attention.ui_contract import CONTRACT, PLUGIN_CONTRACT, definition_errors, plugin_source

REPO = Path(__file__).resolve().parent.parent
PLUGIN = REPO / "plugin" / "alexvdb.herdr-attention"


class PanelContract(unittest.TestCase):
    def test_generated_copy_has_the_arrange_sections_and_panel_labels(self):
        text = (REPO / PLUGIN_CONTRACT).read_text()
        self.assertEqual(text, plugin_source())
        self.assertTrue(text.startswith(".pragma library\n"))
        ui = json.loads(text.split("var UI = ", 1)[1])
        self.assertEqual(ui["arrange"], CONTRACT["arrange"])
        self.assertEqual(ui["panel"], CONTRACT["panel"])
        self.assertNotIn("api", ui, "the panel copy holds labels only, no routes")

    def test_a_stale_panel_copy_is_reported(self):
        self.assertEqual(definition_errors(), [])
        with mock.patch.object(ui_contract, "plugin_source", return_value="stale"):
            self.assertIn("generated panel contract is stale", definition_errors())

    def test_panel_labels_are_read_from_the_contract(self):
        panel = (PLUGIN / "Panel.qml").read_text()
        self.assertIn('import "Contract.js" as Contract', panel)
        for value in CONTRACT["panel"]["labels"].values():
            self.assertNotIn(json.dumps(value, ensure_ascii=False), panel, "label copied into Panel.qml")


class PanelColours(unittest.TestCase):
    """Every colour in the panel is a theme token (Tokens.palette(view)); no shell or fixed colours."""

    def test_no_shell_fixed_or_derived_colours_in_plugin_qml(self):
        files = sorted(PLUGIN.glob("*.qml"))
        self.assertIn(PLUGIN / "TokenButton.qml", files)
        for path in files:
            text = re.sub(r"//[^\n]*", "", path.read_text())   # comments may name the old colours
            with self.subTest(file=path.name):
                self.assertNotRegex(text, r"Commons\.Color\b")
                self.assertNotRegex(text, r"\bQt\.(darker|lighter|rgba|hsla|hsva|tint)\(")
                self.assertNotRegex(text, r"\b(barForeground|bar\.foreground)\b")
                self.assertNotRegex(text, r"#[0-9a-fA-F]{3,8}\b")
                self.assertNotRegex(text, r'color:\s*"(?!transparent")[a-z]+"')
                self.assertNotRegex(text, r'property color \w+:\s*"')

    def test_shell_buttons_and_headers_get_token_colours(self):
        for name in ("Panel.qml", "AgentRow.qml", "WorkspaceCard.qml"):
            text = (PLUGIN / name).read_text()
            with self.subTest(file=name):
                self.assertNotRegex(text, r"(?<!Token)\bButton \{", "use TokenButton")
                self.assertEqual(text.count("PanelSectionHeader {"), len(re.findall(r"PanelSectionHeader \{\n\s+color: ", text)))
        panel = (PLUGIN / "Panel.qml").read_text()
        self.assertNotIn("PanelHero {", panel, "the shell hero darkens its own subtitle")
        self.assertIn("readonly property var tokens: Tokens.palette(view)", panel)


class PanelArrange(unittest.TestCase):
    """Accounts and blocks in the saved Arrange order (view.layout), the same rules as the web."""

    def test_panel_reads_the_saved_order_through_model_helpers(self):
        panel = (PLUGIN / "Panel.qml").read_text()
        self.assertIn("Model.shownAccounts(view, sections)", panel)
        self.assertIn("Model.panelBlocks(view, sections)", panel)
        self.assertIn("Contract.UI.arrange.sections", panel)
        self.assertIn("Contract.UI.arrange.labels.hidden_blocks", panel)
        model = (PLUGIN / "Model.js").read_text()
        blocks = json.loads(re.search(r"var PANEL_BLOCKS = (\[.*?\])", model).group(1))
        ids = [s["id"] for s in CONTRACT["arrange"]["sections"]]
        self.assertTrue(set(blocks) <= set(ids), "panel blocks are Arrange sections")
        self.assertIn("needs_action", blocks)

    @unittest.skipUnless(shutil.which("node"), "no node")
    def test_model_helpers(self):
        result = subprocess.run(["node", str(REPO / "tests" / "js" / "model.test.js")], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertRegex(result.stdout, r"model\.test\.js: \d+ passed")

    @unittest.skipUnless(shutil.which("qs") and Path("/usr/share/omarchy/shell/Commons").is_dir(), "no Quickshell")
    def test_real_engine_order_and_colours(self):
        result = subprocess.run([str(REPO / "scripts" / "qml-check.sh")], capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-1000:])
        self.assertIn("ARRANGE_ORDER blocks quotas,needs_action accounts agy,codex,claude:personal | Hidden: Other updates (1)",
                      result.stdout)
        self.assertEqual(result.stdout.count("THEME_TOKENS"), 2)
        self.assertIn("ACCOUNT_ROW codex Full", result.stdout)


if __name__ == "__main__":
    unittest.main()
