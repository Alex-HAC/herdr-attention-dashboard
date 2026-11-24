"""Plugin Tokens.js and the CSS fallback are generated (herdr_theme.py + token block) and must not drift."""

import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGIN = REPO / "plugin" / "alexvdb.herdr-attention"


class TokensJs(unittest.TestCase):
    def test_tokens_js_matches_style_css(self):
        result = subprocess.run([sys.executable, str(REPO / "scripts" / "gen_tokens.py"), "--check"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_values_come_from_the_token_block(self):
        sys.path.insert(0, str(REPO / "scripts"))
        import gen_tokens
        t = gen_tokens.tokens((REPO / "herdr_attention" / "web" / "style.css").read_text())
        # v0.9.4: one Tokyo Night fallback set (herdr_theme.py); live colours come from the server.
        self.assertEqual(t["fallback"]["needs"], "#ff7a93")
        self.assertEqual(t["fallback"]["bg"], "#1a1b26")
        self.assertEqual(t["fallback"]["textStrong"], "#c0caf5")
        self.assertEqual(t["radius"]["md"], 14)
        self.assertEqual(t["space"]["4"], 16)
        self.assertEqual(t["type"], {"micro": 11, "small": 13, "body": 15, "title": 17, "head": 22})

    def test_web_colours_only_in_token_block(self):
        web = REPO / "herdr_attention" / "web"
        for name in ("style.css", "app.js", "graph.js"):
            source = (web / name).read_text()
            if name == "style.css":
                source = re.sub(r"/\* tokens:start\b.*?/\* tokens:end \*/", "", source, flags=re.S)
            with self.subTest(file=name):
                self.assertNotRegex(source, r"#[0-9a-fA-F]{3,8}\b")
                self.assertNotRegex(source, r"rgba?\(\s*\d")

    def test_plugin_uses_tokens_not_herdr_or_hex_colours(self):
        for name in ("Panel.qml", "AgentRow.qml", "WorkspaceCard.qml", "RelationshipLine.qml"):
            text = (PLUGIN / name).read_text()
            with self.subTest(file=name):
                self.assertIn('import "Tokens.js" as Tokens', text)
                self.assertNotRegex(text, r"Style\.font\.(caption|body|bodySmall|subtitle)\b")
                self.assertNotRegex(text, r"Style\.spacing\.(xs|sm|md|lg)\b")
                self.assertNotRegex(text, r"#[0-9a-fA-F]{6}\b")
        self.assertNotIn("herdrColor", (PLUGIN / "AgentRow.qml").read_text())


if __name__ == "__main__":
    unittest.main()
