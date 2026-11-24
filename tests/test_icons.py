"""T3: one inline SVG icon set; no emoji or glyph icons in the web UI (v0.8.3)."""

import re
import unittest
from pathlib import Path

WEB = Path(__file__).parent.parent / "herdr_attention" / "web"


class Icons(unittest.TestCase):
    def test_no_glyph_icons_left(self):
        for name in ("app.js", "graph.js", "index.html"):
            text = (WEB / name).read_text()
            for glyph in "✨⚠👁☰⤢▦⇅":
                self.assertNotIn(glyph, text, f"{glyph} in {name}")

    def test_every_used_icon_is_in_the_sprite(self):
        html = (WEB / "index.html").read_text()
        sprite = set(re.findall(r'<symbol id="i-([a-z]+)"', html))
        graph = (WEB / "graph.js").read_text()
        ids = dict(re.findall(r'(\w+): "(\w+)"', graph.split("const ICON_IDS = {", 1)[1].split("}", 1)[0]))
        used = set(re.findall(r'icon\("(\w+)"', graph + (WEB / "app.js").read_text()))
        used |= set(re.findall(r'href="#i-([a-z]+)"', html))
        self.assertFalse({ids.get(u, u) for u in used} - sprite)


if __name__ == "__main__":
    unittest.main()
