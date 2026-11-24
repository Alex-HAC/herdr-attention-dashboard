"""v0.9.4: the dashboard uses the Omarchy theme's Ghostty palette (Tokyo Night), read live, on every device."""

import http.client
import json
import os
import re
import tempfile
import unittest
from pathlib import Path

from herdr_attention import herdr_theme
from herdr_attention.api import PhoneServer, make_handler, serve
from tests.test_typing_survives_refresh import reading
from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER, run

REPO = Path(__file__).resolve().parent.parent
WEB = REPO / "herdr_attention" / "web"

# ~/.local/state/omarchy/current/theme/ghostty.conf for Omarchy tokyo-night (Nov 2026).
TOKYO_NIGHT_GHOSTTY = """background = #1a1b26
foreground = #a9b1d6
cursor-color = #c0caf5
selection-background = #292e42
selection-foreground = #c0caf5

palette = 0=#1a1b26
palette = 1=#f7768e
palette = 2=#9ece6a
palette = 3=#e0af68
palette = 4=#7aa2f7
palette = 5=#ad8ee6
palette = 6=#449dab
palette = 7=#a9b1d6
palette = 8=#414868
palette = 9=#ff7a93
palette = 10=#b9f27c
palette = 11=#ff9e64
palette = 12=#7da6ff
palette = 13=#bb9af7
palette = 14=#0db9d7
palette = 15=#c0caf5
"""
TOKYO_NIGHT_COLORS = 'mode = "dark"\naccent = "#7aa2f7"\n'
# A light theme, to prove that a later Omarchy theme change is followed.
LIGHT_GHOSTTY = """background = #e1e2e7
foreground = #3760bf
selection-background = #b7c1e3
palette = 0=#e9e9ed
palette = 1=#f52a65
palette = 2=#587539
palette = 3=#8c6c3e
palette = 4=#2e7de9
palette = 5=#9854f1
palette = 6=#007197
palette = 7=#6172b0
palette = 8=#a1a6c5
palette = 9=#f52a65
palette = 10=#587539
palette = 11=#8c6c3e
palette = 12=#2e7de9
palette = 13=#9854f1
palette = 14=#007197
palette = 15=#3760bf
"""

# The brief's palette and Herdr's status mapping (terminal theme: Working=ANSI 3, Blocked=ANSI 9,
# Done=ANSI 6, Idle=ANSI 2, Unknown=ANSI 7).
EXPECTED = {"bg": "#1a1b26", "text": "#a9b1d6", "text-strong": "#c0caf5", "line-strong": "#414868",
            "accent": "#7aa2f7", "needs": "#ff7a93", "working": "#e0af68", "done": "#449dab",
            "waiting": "#9ece6a", "unknown": "#a9b1d6", "caution": "#ff9e64", "pressed": "#292e42"}
TEXT = ("text", "text-strong", "muted", "faint")
SURFACES = ("bg", "surface", "raised", "overlay")
STATES = ("accent", "needs", "working", "done", "waiting", "parked", "unknown", "caution")


def theme_dir(tmp: Path, ghostty: str | None = TOKYO_NIGHT_GHOSTTY, colors: str | None = TOKYO_NIGHT_COLORS,
              name: str = "tokyo-night") -> Path:
    d = tmp / "current" / "theme"
    d.mkdir(parents=True, exist_ok=True)
    (tmp / "current" / "theme.name").write_text(name + "\n")
    for file, text in (("ghostty.conf", ghostty), ("colors.toml", colors)):
        if text is None:
            (d / file).unlink(missing_ok=True)
        else:
            (d / file).write_text(text)
    return d


class Palette(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.no_herdr_config = self.tmp / "herdr.toml"   # missing: Herdr's default "terminal" theme

    def current(self, d):
        return herdr_theme.current(self.no_herdr_config, d)

    def test_tokyo_night_uses_exactly_the_ghostty_palette_and_herdr_status_colours(self):
        theme = self.current(theme_dir(self.tmp))
        self.assertEqual(theme["name"], "tokyo-night")
        self.assertEqual(theme["mode"], "dark")
        self.assertEqual({k: theme["tokens"][k] for k in EXPECTED}, EXPECTED)
        self.assertEqual(theme["missing"], [])
        self.assertIn("live", theme["source"])

    def test_built_in_fallback_is_the_same_tokyo_night(self):
        self.assertEqual(herdr_theme.fallback_theme()["tokens"], self.current(theme_dir(self.tmp))["tokens"])

    def test_missing_theme_files_give_the_built_in_tokyo_night(self):
        theme = self.current(self.tmp / "nothing" / "theme")
        self.assertEqual(theme["tokens"], herdr_theme.fallback_theme()["tokens"])
        self.assertIn("not found", theme["source"])

    def test_colors_toml_alone_is_enough(self):
        colors = TOKYO_NIGHT_COLORS + "".join(f'{k} = "{v}"\n' for k, v in {
            "background": "#1a1b26", "foreground": "#a9b1d6", "selection": "#292e42", "red": "#f7768e",
            "green": "#9ece6a", "yellow": "#e0af68", "blue": "#7aa2f7", "magenta": "#ad8ee6", "cyan": "#449dab",
            "muted": "#414868", "bright_red": "#ff7a93", "bright_green": "#b9f27c", "bright_yellow": "#ff9e64",
            "bright_blue": "#7da6ff", "bright_magenta": "#bb9af7", "bright_cyan": "#0db9d7",
            "bright_foreground": "#c0caf5"}.items())
        theme = self.current(theme_dir(self.tmp, ghostty=None, colors=colors))
        self.assertEqual({k: theme["tokens"][k] for k in EXPECTED}, EXPECTED)

    def test_a_later_omarchy_theme_change_is_followed_without_a_restart(self):
        d = theme_dir(self.tmp)
        before = self.current(d)
        theme_dir(self.tmp, ghostty=LIGHT_GHOSTTY, colors='mode = "light"\naccent = "#2e7de9"\n', name="tokyo-night-day")
        after = self.current(d)
        self.assertNotEqual(before["key"], after["key"])
        self.assertEqual((after["name"], after["mode"], after["tokens"]["bg"]), ("tokyo-night-day", "light", "#e1e2e7"))
        # Herdr's Working = ANSI 3 of the new theme, darkened just enough to stay readable on its light surfaces.
        self.assertEqual(after["tokens"]["working"], herdr_theme.readable("#8c6c3e", after["tokens"]["overlay"]))
        for name in TEXT + STATES:   # every text and state colour stays AA in any theme
            self.assertGreaterEqual(herdr_theme.contrast(after["tokens"][name], after["tokens"]["overlay"]), 4.5, name)
        self.assertEqual(self.current(d)["key"], after["key"])        # unchanged files: same theme

    def test_herdr_built_in_theme_and_custom_overrides_set_the_status_colours(self):
        config = self.tmp / "herdr-config.toml"
        config.write_text('[theme]\nname = "catppuccin"\n[theme.custom]\nyellow = "#e5c890"\n')
        theme = herdr_theme.current(config, theme_dir(self.tmp))
        self.assertEqual(theme["tokens"]["working"], "#e5c890")
        self.assertEqual(theme["tokens"]["needs"], herdr_theme.BUILTIN["catppuccin"]["red"])
        self.assertEqual(theme["tokens"]["bg"], "#1a1b26")             # the base stays the Omarchy palette

    def test_contrast_meets_wcag_aa(self):
        """Pairs measured (WCAG 2 ratio, AA = 4.5 for text): every text token and every state colour on each
        surface, text on every soft state fill (status card, notes), each state colour on its own soft fill
        (pills, avatars), and the ink colours on their solid fills."""
        for theme in (herdr_theme.fallback_theme(), self.current(theme_dir(self.tmp))):
            t, tint = theme["tokens"], int(theme["tint"].rstrip("%")) / 100
            soft = {f"{k}-soft": herdr_theme.mix(t[k], t["surface"], tint) for k in STATES}
            for fg in TEXT + STATES:
                for bg in SURFACES:
                    with self.subTest(pair=f"{fg} on {bg}"):
                        self.assertGreaterEqual(herdr_theme.contrast(t[fg], t[bg]), 4.5)
            for fg in TEXT:
                for name, fill in soft.items():
                    with self.subTest(pair=f"{fg} on {name}"):
                        self.assertGreaterEqual(herdr_theme.contrast(t[fg], fill), 4.5)
            for state in STATES:
                with self.subTest(pair=f"{state} on {state}-soft"):
                    self.assertGreaterEqual(herdr_theme.contrast(t[state], soft[f"{state}-soft"]), 4.5)
            for ink, fill in (("accent-ink", "accent"), ("needs-ink", "needs")):
                self.assertGreaterEqual(herdr_theme.contrast(t[ink], t[fill]), 4.5)

    def test_theme_css_declares_every_token(self):
        text = herdr_theme.css(herdr_theme.fallback_theme())
        for name in herdr_theme.TOKEN_NAMES:
            self.assertRegex(text, rf"--{name}: #[0-9a-f]{{6}};")
        self.assertIn("--tint: 8%;", text)
        self.assertIn("color-scheme: dark", text)


class Sources(unittest.TestCase):
    def test_generated_fallbacks_are_up_to_date(self):
        import subprocess
        import sys
        r = subprocess.run([sys.executable, str(REPO / "scripts" / "gen_tokens.py"), "--check"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_no_light_theme_switch_on_the_device_setting(self):
        css = (WEB / "style.css").read_text()
        self.assertNotIn("prefers-color-scheme", css)                  # Tokyo Night on light-mode phones too
        html = (WEB / "index.html").read_text()
        self.assertNotIn("prefers-color-scheme", html)
        self.assertLess(html.index('href="style.css"'), html.index('href="theme.css"'))   # live colours win

    def test_hand_written_colours_live_only_in_herdr_theme(self):
        for path in (WEB / "app.js", WEB / "graph.js", *(REPO / "plugin" / "alexvdb.herdr-attention").glob("*.qml")):
            with self.subTest(file=path.name):
                self.assertNotRegex(path.read_text(), r"#[0-9a-fA-F]{6}\b")


class ServedTheme(unittest.TestCase):
    def setUp(self):
        from tests.test_api import make_collector
        self.collector = make_collector()
        self.server = PhoneServer(("127.0.0.1", 0), make_handler(self.collector, local=False))
        self.thread = serve(self.server)

    def tearDown(self):
        self.server.stopping.set()
        self.server.shutdown()
        self.server.server_close()

    def test_phone_listener_serves_theme_css_without_login(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        conn.request("GET", "/theme.css")
        response = conn.getresponse()
        body = response.read().decode()
        conn.close()
        self.assertEqual(response.status, 200)
        self.assertTrue(response.getheader("Content-Type").startswith("text/css"))
        self.assertEqual(response.getheader("Cache-Control"), "no-store")
        self.assertEqual(body, herdr_theme.css(herdr_theme.current()))

    def test_view_carries_the_theme_for_the_page_and_the_bar_panel(self):
        theme = self.collector.view(local=True)["theme"]
        self.assertEqual(set(theme["tokens"]), set(herdr_theme.TOKEN_NAMES))
        self.assertRegex(theme["key"], r"^[0-9a-f]{12}$")


def themed_fixture(key="k1", bg="#1a1b26"):
    data = page_fixture()
    tokens = dict(herdr_theme.fallback_theme()["tokens"], bg=bg)
    data["view"]["theme"] = {"key": key, "name": "tokyo-night", "mode": "dark", "tokens": tokens}
    return data


PROBE = """
  const css = getComputedStyle(document.documentElement);
  const node = document.querySelector('svg.graph .node.s-working .body') || document.querySelector('svg.graph .node .body');
  document.body.dataset.reading = JSON.stringify({
    body: getComputedStyle(document.body).backgroundColor, text: getComputedStyle(document.body).color,
    working: css.getPropertyValue('--working').trim(), needs: css.getPropertyValue('--needs').trim(),
    done: css.getPropertyValue('--done').trim(), waiting: css.getPropertyValue('--waiting').trim(),
    scheme: css.colorScheme, node: node ? getComputedStyle(node).stroke : null, nodeState: node ? node.parentNode.getAttribute('class') : null,
    nodes: [...document.querySelectorAll('svg.graph .node')].map(n => [n.getAttribute('class'), getComputedStyle(n.querySelector('.body')).stroke]),
  });
"""


@unittest.skipUnless(BROWSER, "no Chromium found")
class PhonePage(unittest.TestCase):
    def test_390px_phone_shows_tokyo_night_in_light_and_dark_device_mode(self):
        for device in ("light", "dark"):
            with self.subTest(device=device):
                r = reading(run(themed_fixture(), "", 390, 844, script=PROBE, theme=device))
                self.assertEqual(r["body"], "rgb(26, 27, 38)")              # Ghostty background
                self.assertEqual(r["text"], "rgb(169, 177, 214)")           # Ghostty foreground
                self.assertEqual((r["working"], r["needs"], r["done"], r["waiting"]),
                                 ("#e0af68", "#ff7a93", "#449dab", "#9ece6a"))   # Herdr's status colours
                self.assertEqual(r["scheme"], "dark")

    def test_graph_nodes_take_herdr_status_colours(self):
        r = reading(run(themed_fixture(), "", 390, 844, script=PROBE))
        expected = {"working": "rgb(224, 175, 104)", "done": "rgb(68, 157, 171)", "waiting": "rgb(158, 206, 106)",
                    "needs_you": "rgb(255, 122, 147)"}
        checked = 0
        for cls, stroke in r["nodes"]:
            state = re.search(r"\bs-([a-z_]+)", cls)
            if state and state.group(1) in expected and not re.search(r"\b(stall|failure)\b", cls):
                checked += 1
                self.assertEqual(stroke, expected[state.group(1)], cls)   # stalls keep the caution outline
        self.assertGreater(checked, 0)

    def test_a_new_theme_key_loads_the_new_sheet_and_bar_colour(self):
        r = reading(run(themed_fixture(), "", 390, 844, script="""
          FIXTURE.view.theme = {...FIXTURE.view.theme, key: 'k2', tokens: {...FIXTURE.view.theme.tokens, bg: '#e1e2e7'}};
          await refresh(true);
          document.body.dataset.reading = JSON.stringify({
            sheets: [...document.querySelectorAll('link[href^="theme.css"]')].map(l => l.getAttribute('href')),
            meta: document.querySelector('meta[name="theme-color"]').content });
        """))
        self.assertIn("theme.css?k=k2", r["sheets"])
        self.assertEqual(r["meta"], "#e1e2e7")


if __name__ == "__main__":
    unittest.main()
