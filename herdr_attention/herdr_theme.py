"""Herdr's own agent-status colours, resolved for the dashboard.

Source of truth (read from Herdr 0.9.1, tag v0.9.1, commit 065ef9d6):

* ``src/client/shell.rs`` ``status_color``: Working -> ``yellow``,
  Blocked -> ``red``, Done -> ``teal``, Idle -> ``green``, Unknown -> ``overlay0``
  (the mobile view uses ``blue`` for Done).
* ``src/client/shell.rs`` ``status_icon`` ("symbols" style): Blocked ``×``,
  Working ``◐``, Done ``✓``, Idle ``○``, Unknown ``·``.
* ``src/app/state.rs``: built-in theme palettes (hex values below) and the
  ``terminal`` theme, which uses ANSI colours: yellow = ANSI 3,
  red = ``LightRed`` = ANSI 9, teal = ``Cyan`` = ANSI 6, green = ANSI 2,
  overlay0 = ``Gray`` = ANSI 7, blue = ANSI 4.

For the ``terminal`` theme the real colour is whatever the terminal paints
for those ANSI slots; on Omarchy that is the current theme's palette, read
from ``~/.local/state/omarchy/current/theme`` (ghostty.conf, else colors.toml).
``[theme.custom]`` hex overrides in Herdr's config.toml are applied on top.
Herdr's theme is only read, never changed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tomllib
from pathlib import Path

STATUS_TOKEN = {"working": "yellow", "blocked": "red", "done": "teal", "idle": "green", "unknown": "overlay0"}
STATUS_SYMBOL = {"blocked": "×", "working": "◐", "done": "✓", "idle": "○", "unknown": "·"}
TERMINAL_ANSI = {"yellow": 3, "red": 9, "teal": 6, "green": 2, "overlay0": 7, "blue": 4}
# colors.toml keys for the same ANSI slots (when no terminal palette file exists).
COLORS_TOML_KEY = {3: "yellow", 9: "bright_red", 6: "cyan", 2: "green", 7: "foreground", 4: "blue"}
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

BUILTIN = {
    "catppuccin": {"blue": "#89b4fa", "green": "#a6e3a1", "overlay0": "#6c7086", "red": "#f38ba8", "teal": "#94e2d5", "yellow": "#f9e2af"},
    "catppuccin-latte": {"blue": "#1e66f5", "green": "#40a02b", "overlay0": "#9ca0b0", "red": "#d20f39", "teal": "#179299", "yellow": "#df8e1d"},
    "dracula": {"blue": "#8be9fd", "green": "#50fa7b", "overlay0": "#6272a4", "red": "#ff5555", "teal": "#8be9fd", "yellow": "#f1fa8c"},
    "gruvbox": {"blue": "#83a598", "green": "#b8bb26", "overlay0": "#928374", "red": "#fb4934", "teal": "#8ec07c", "yellow": "#fabd2f"},
    "gruvbox-light": {"blue": "#076678", "green": "#79740e", "overlay0": "#928374", "red": "#9d0006", "teal": "#427b58", "yellow": "#b57614"},
    "kanagawa": {"blue": "#7e9cd8", "green": "#76946a", "overlay0": "#727169", "red": "#c34043", "teal": "#7fb4ca", "yellow": "#c0a36e"},
    "kanagawa-lotus": {"blue": "#4d699b", "green": "#6f894e", "overlay0": "#a09cac", "red": "#c84053", "teal": "#4e8ca2", "yellow": "#77713f"},
    "nord": {"blue": "#81a1c1", "green": "#a3be8c", "overlay0": "#4c566a", "red": "#bf616a", "teal": "#8fbcbb", "yellow": "#ebcb8b"},
    "one-dark": {"blue": "#61afef", "green": "#98c379", "overlay0": "#5c6370", "red": "#e06c75", "teal": "#56b6c2", "yellow": "#e5c07b"},
    "one-light": {"blue": "#4078f2", "green": "#50a14f", "overlay0": "#a0a1a7", "red": "#e45649", "teal": "#0184bc", "yellow": "#c18401"},
    "rose-pine": {"blue": "#31748f", "green": "#31748f", "overlay0": "#6e6a86", "red": "#eb6f92", "teal": "#9ccfd8", "yellow": "#f6c177"},
    "rose-pine-dawn": {"blue": "#286983", "green": "#286983", "overlay0": "#9893a5", "red": "#b4637a", "teal": "#56949f", "yellow": "#ea9d34"},
    "solarized": {"blue": "#268bd2", "green": "#859900", "overlay0": "#586e75", "red": "#dc322f", "teal": "#2aa198", "yellow": "#b58900"},
    "solarized-light": {"blue": "#268bd2", "green": "#859900", "overlay0": "#93a1a1", "red": "#dc322f", "teal": "#2aa198", "yellow": "#b58900"},
    "tokyo-night": {"blue": "#7aa2f7", "green": "#9ece6a", "overlay0": "#565f89", "red": "#f7768e", "teal": "#7dcfff", "yellow": "#e0af68"},
    "tokyo-night-day": {"blue": "#2e7de9", "green": "#587539", "overlay0": "#8990b3", "red": "#f52a65", "teal": "#118c74", "yellow": "#8c6c3e"},
    "vesper": {"blue": "#b0b0b0", "green": "#99ffe4", "overlay0": "#5c5c5c", "red": "#ff8080", "teal": "#66ddcc", "yellow": "#ffc799"},
}


def _herdr_theme(config_path: Path) -> tuple[str, dict]:
    try:
        with open(config_path, "rb") as fh:
            theme = (tomllib.load(fh).get("theme") or {})
    except (OSError, tomllib.TOMLDecodeError):
        return "terminal", {}
    custom = theme.get("custom") if isinstance(theme.get("custom"), dict) else {}
    overrides = {k: v for k, v in custom.items() if k in TERMINAL_ANSI and isinstance(v, str) and HEX.match(v)}
    return str(theme.get("name") or "terminal"), overrides


def _ansi_palette(theme_dir: Path) -> dict[int, str]:
    palette: dict[int, str] = {}
    try:
        for line in (theme_dir / "ghostty.conf").read_text().splitlines():
            m = re.match(r"\s*palette\s*=\s*(\d+)\s*=\s*(#[0-9a-fA-F]{6})", line)
            if m:
                palette[int(m.group(1))] = m.group(2).lower()
    except OSError:
        pass
    if not palette:
        try:
            with open(theme_dir / "colors.toml", "rb") as fh:
                colors = tomllib.load(fh)
            for slot, key in COLORS_TOML_KEY.items():
                if isinstance(colors.get(key), str) and HEX.match(colors[key]):
                    palette[slot] = colors[key].lower()
        except (OSError, tomllib.TOMLDecodeError):
            pass
    return palette


def resolve(config_path: Path | None = None, theme_dir: Path | None = None) -> dict:
    """{"theme", "source", "colors": {status: hex}, "symbols", "tokens"}."""
    config_path = config_path or Path(os.path.expanduser("~/.config/herdr/config.toml"))
    theme_dir = theme_dir or Path(os.path.expanduser("~/.local/state/omarchy/current/theme"))
    name, overrides = _herdr_theme(config_path)
    tokens: dict[str, str] = {}
    if name in BUILTIN:
        tokens = dict(BUILTIN[name])
        source = f"Herdr built-in theme '{name}' (v0.9.1 palette)"
    else:
        palette = _ansi_palette(theme_dir)
        tokens = {token: palette[slot] for token, slot in TERMINAL_ANSI.items() if slot in palette}
        theme_name = (theme_dir.parent / "theme.name") if theme_dir else None
        try:
            omarchy = theme_name.read_text().strip() if theme_name else ""
        except OSError:
            omarchy = ""
        source = (f"Herdr theme '{name}' uses ANSI colours; values from the Omarchy theme '{omarchy or '?'}' palette"
                  if tokens else f"Herdr theme '{name}': terminal palette not found, dashboard defaults used")
    tokens.update({k: v.lower() for k, v in overrides.items()})
    if overrides:
        source += f"; overrides from [theme.custom]: {', '.join(sorted(overrides))}"
    colors = {status: tokens.get(token) for status, token in STATUS_TOKEN.items()}
    return {"theme": name, "source": source, "colors": colors, "symbols": dict(STATUS_SYMBOL),
            "tokens": {status: token for status, token in STATUS_TOKEN.items()}, "done_mobile": tokens.get("blue")}


# --------------------------------------------------------------------------------------------- dashboard palette
# v0.9.4: the dashboard (phone, desktop window, bar panel) is drawn in the Omarchy theme's terminal
# palette, the same colours Herdr shows with its "terminal" theme. The palette is read live from
# ~/.local/state/omarchy/current/theme on every request (cached on the files' mtimes), so a later
# Omarchy theme change is followed without a reinstall. TOKYO_NIGHT is the built-in fallback for
# missing files or values; it is the only place where dashboard colours are written by hand.
# scripts/gen_tokens.py writes the same fallback into style.css, index.html and the plugin's Tokens.js.

TOKYO_NIGHT = {
    "background": "#1a1b26", "foreground": "#a9b1d6", "selection": "#292e42", "accent": "#7aa2f7",
    "ansi": ["#1a1b26", "#f7768e", "#9ece6a", "#e0af68", "#7aa2f7", "#ad8ee6", "#449dab", "#a9b1d6",
             "#414868", "#ff7a93", "#b9f27c", "#ff9e64", "#7da6ff", "#bb9af7", "#0db9d7", "#c0caf5"],
    "mode": "dark",
}
# colors.toml names for the 16 ANSI slots (Omarchy writes these; "colorN" is read too).
COLORS_TOML_ANSI = {1: "red", 2: "green", 3: "yellow", 4: "blue", 5: "magenta", 6: "cyan", 7: "foreground",
                    8: "muted", 9: "bright_red", 10: "bright_green", 11: "bright_yellow", 12: "bright_blue",
                    13: "bright_magenta", 14: "bright_cyan", 15: "bright_foreground"}
# Dashboard state -> Herdr status whose colour it takes (shell.rs status_color).
STATE_STATUS = {"needs": "blocked", "working": "working", "done": "done", "waiting": "idle", "unknown": "unknown"}
# Every colour token the dashboard and the bar panel use (CSS custom properties without "--").
TOKEN_NAMES = ("bg", "surface", "raised", "overlay", "pressed", "line", "line-strong",
               "text", "text-strong", "muted", "faint", "accent", "accent-ink", "needs", "needs-ink",
               "working", "done", "waiting", "parked", "unknown", "caution",
               "level-ok", "level-mid", "level-full",
               *(f"icon-{name}" for name in ("blue", "green", "yellow", "magenta", "cyan", "red", "neutral")))
# Account icon colour names (ui_contract.json account_icons.colors) -> the palette's ANSI slot.
ICON_ANSI = {"blue": 4, "green": 2, "yellow": 3, "magenta": 5, "cyan": 6, "red": 1}
PALETTE_FILES = ("ghostty.conf", "colors.toml")


def _rgb(hex_: str) -> tuple[int, int, int]:
    return int(hex_[1:3], 16), int(hex_[3:5], 16), int(hex_[5:7], 16)


def mix(a: str, b: str, weight: float) -> str:
    """``weight`` of a over b, per sRGB channel (as CSS color-mix(in srgb, a w%, b))."""
    return "#" + "".join(f"{round(x * weight + y * (1 - weight)):02x}" for x, y in zip(_rgb(a), _rgb(b)))


def luminance(hex_: str) -> float:
    lin = [(c / 255) / 12.92 if c / 255 <= 0.04045 else ((c / 255 + 0.055) / 1.055) ** 2.4 for c in _rgb(hex_)]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    """WCAG 2 contrast ratio, 1..21."""
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def read_palette(theme_dir: Path | None = None) -> dict:
    """The Omarchy theme's terminal palette, each missing value from TOKYO_NIGHT.

    {"name", "background", "foreground", "selection", "accent", "ansi": [16], "mode", "missing": [...]}"""
    theme_dir = theme_dir or Path(os.path.expanduser("~/.local/state/omarchy/current/theme"))
    found: dict = {}
    ansi: dict[int, str] = {}
    try:
        for line in (theme_dir / "ghostty.conf").read_text().splitlines():
            m = re.match(r"\s*(background|foreground|selection-background)\s*=\s*(#[0-9a-fA-F]{6})\s*$", line)
            if m:
                found[{"selection-background": "selection"}.get(m.group(1), m.group(1))] = m.group(2).lower()
            m = re.match(r"\s*palette\s*=\s*(\d+)\s*=\s*(#[0-9a-fA-F]{6})", line)
            if m and int(m.group(1)) < 16:
                ansi[int(m.group(1))] = m.group(2).lower()
    except OSError:
        pass
    colors: dict = {}
    try:
        with open(theme_dir / "colors.toml", "rb") as fh:
            colors = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        pass
    good = lambda v: isinstance(v, str) and bool(HEX.match(v))
    for key, toml_key in (("background", "background"), ("foreground", "foreground"),
                          ("selection", "selection"), ("accent", "accent")):
        if key not in found and good(colors.get(toml_key)):
            found[key] = colors[toml_key].lower()
    for slot in range(16):
        for toml_key in (f"color{slot}", COLORS_TOML_ANSI.get(slot)):
            if slot not in ansi and toml_key and good(colors.get(toml_key)):
                ansi[slot] = colors[toml_key].lower()
    if 0 not in ansi and "background" in found:
        ansi[0] = found["background"]
    missing = [k for k in ("background", "foreground", "selection", "accent") if k not in found]
    missing += [f"ansi{slot}" for slot in range(16) if slot not in ansi]
    palette = {k: found.get(k, TOKYO_NIGHT[k]) for k in ("background", "foreground", "selection", "accent")}
    palette["ansi"] = [ansi.get(slot, TOKYO_NIGHT["ansi"][slot]) for slot in range(16)]
    mode = colors.get("mode")
    palette["mode"] = mode if mode in ("dark", "light") else ("light" if luminance(palette["background"]) > 0.4 else "dark")
    try:
        palette["name"] = (theme_dir.parent / "theme.name").read_text().strip() or "?"
    except OSError:
        palette["name"] = "?"
    palette["missing"] = missing
    return palette


def readable(color: str, on: str, target: float = 4.5) -> str:
    """``color`` itself when it meets ``target`` on ``on``; else moved towards black or white until it does.

    The Omarchy palettes we ship for (Tokyo Night) pass unchanged; this only guards other themes."""
    end = "#000000" if luminance(on) > 0.18 else "#ffffff"
    for step in range(0, 101, 2):
        candidate = mix(end, color, step / 100)
        if contrast(candidate, on) >= target:
            return candidate
    return end


def _weight_for(fg: str, bg: str, backgrounds, target: float, own_tint: float | None = None,
                surface: str | None = None) -> float:
    """Smallest share of fg over bg that reaches target on every background (1.0 when never).

    With own_tint, the colour must also read on its own soft fill (it is a state colour too)."""
    for step in range(50, 101):
        candidate = mix(fg, bg, step / 100)
        extra = [mix(candidate, surface, own_tint)] if own_tint is not None else []
        if min(contrast(candidate, b) for b in [*backgrounds, *extra]) >= target:
            return step / 100
    return 1.0


TINTS = (0.14, 0.12, 0.10, 0.08, 0.06)   # share of a state colour in its soft fill (--tint), most first
HUES = ("accent", "needs", "working", "done", "waiting", "caution")


def build(palette: dict, status: dict | None = None) -> tuple[dict, float]:
    """(colour tokens, tint) from a palette and Herdr's status colours (resolve()["colors"]).

    Surfaces mix a little foreground into the background. Text and state colours are the palette's own
    when they meet WCAG AA (4.5) on every surface, else moved just far enough towards black or white.
    The tint is the strongest that keeps each state colour AA on its own soft fill (pills, avatars);
    muted and faint text take the least foreground that stays AA (4.6, 4.5) on every surface and fill."""
    ansi, bg, fg = palette["ansi"], palette["background"], palette["foreground"]
    status = {k: v for k, v in (status or {}).items() if isinstance(v, str) and HEX.match(v)}
    for name, token in STATUS_TOKEN.items():   # terminal theme fallback: the ANSI slot Herdr paints
        status.setdefault(name, ansi[TERMINAL_ANSI[token]])
    t = {"bg": bg, "surface": mix(fg, bg, 0.035), "raised": mix(fg, bg, 0.06), "overlay": mix(fg, bg, 0.085),
         "pressed": palette["selection"], "line": mix(fg, bg, 0.13), "line-strong": ansi[8]}
    on = t["overlay"]   # the lightest (dark mode) surface: text that reads there reads everywhere
    text = t["text"] = readable(fg, on)
    t["text-strong"] = readable(max((ansi[15], ansi[0], fg), key=lambda c: contrast(c, bg)), on, 7.0)
    t["accent"] = readable(palette["accent"], on)
    for state, herdr_status in STATE_STATUS.items():
        t[state] = readable(status[herdr_status].lower(), on)
    t["caution"] = readable(ansi[11], on)
    soft = lambda c, tint: mix(c, t["surface"], tint)
    tint = next((x for x in TINTS if all(contrast(t[h], soft(t[h], x)) >= 4.5 for h in HUES)), TINTS[-1])
    fills = [t[k] for k in SURFACES] + [soft(t[h], tint) for h in (*HUES, "unknown")]
    t["muted"] = mix(text, bg, _weight_for(text, bg, fills, 4.6))
    t["faint"] = mix(text, bg, _weight_for(text, bg, fills, 4.5, tint, t["surface"]))
    t["parked"] = t["faint"]
    # How full something is (quota rings and bars): the palette's green, yellow and red.
    t["level-ok"], t["level-mid"], t["level-full"] = (readable(ansi[i], on) for i in (2, 3, 1))
    # Account icons: the colour names map to the palette, readable on every surface; grey is muted text.
    for name, slot in ICON_ANSI.items():
        t[f"icon-{name}"] = readable(ansi[slot], on)
    t["icon-neutral"] = t["muted"]
    ink = lambda c: max((bg, t["text-strong"]), key=lambda i: contrast(i, c))
    t["accent-ink"], t["needs-ink"] = ink(t["accent"]), ink(t["needs"])
    return {name: t[name] for name in TOKEN_NAMES}, tint


SURFACES = ("bg", "surface", "raised", "overlay")


def tokens(palette: dict, status: dict | None = None) -> dict:
    """Dashboard colour tokens (see build())."""
    return build(palette, status)[0]


def css(theme: dict) -> str:
    """A :root block with the colour tokens (served as /theme.css; the fallback copy lives in style.css)."""
    decl = " ".join(f"--{k}: {v};" for k, v in theme["tokens"].items())
    return f":root {{ {decl} --tint: {theme['tint']}; color-scheme: {theme['mode']}; }}\n"


def fallback_theme() -> dict:
    palette = dict(TOKYO_NIGHT, name="tokyo-night", missing=[])
    return _theme(palette, {}, "built-in Tokyo Night")


def _theme(palette: dict, status: dict, source: str) -> dict:
    t, tint = build(palette, status)
    tint = f"{round(tint * 100)}%"
    key = hashlib.sha1(json.dumps([t, tint, palette["mode"]], sort_keys=True).encode()).hexdigest()[:12]
    return {"key": key, "name": palette["name"], "mode": palette["mode"], "source": source, "tokens": t,
            "tint": tint, "missing": palette["missing"]}


_cache: dict = {}


def current(config_path: Path | None = None, theme_dir: Path | None = None) -> dict:
    """The dashboard theme now: {"key", "name", "mode", "source", "tokens", "missing"}.

    Re-read when ghostty.conf, colors.toml, theme.name or Herdr's config.toml change; never raises."""
    config_path = config_path or Path(os.path.expanduser("~/.config/herdr/config.toml"))
    theme_dir = theme_dir or Path(os.path.expanduser("~/.local/state/omarchy/current/theme"))
    stamp = []
    for path in (*(theme_dir / n for n in PALETTE_FILES), theme_dir.parent / "theme.name", config_path):
        try:
            st = path.stat()
            stamp.append((str(path), st.st_mtime_ns, st.st_size))
        except OSError:
            stamp.append((str(path), None, None))
    if _cache.get("stamp") == stamp:
        return _cache["theme"]
    try:
        palette = read_palette(theme_dir)
        herdr = resolve(config_path, theme_dir)
        if len(palette["missing"]) >= 18:
            source = "Omarchy theme files not found: built-in Tokyo Night"
        else:
            source = f"Omarchy theme '{palette['name']}' (live); status colours: {herdr['source']}"
            if palette["missing"]:
                source += f"; built-in Tokyo Night for {', '.join(palette['missing'])}"
        theme = _theme(palette, herdr.get("colors") or {}, source)
    except Exception:   # never let theme reading break the dashboard
        theme = fallback_theme()
        theme["source"] = "could not read the Omarchy theme: built-in Tokyo Night"
    _cache.update(stamp=stamp, theme=theme)
    return theme
