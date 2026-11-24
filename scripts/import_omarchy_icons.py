#!/usr/bin/env python3
"""v0.9.8.5: turn the vendored Omarchy agent marks (third_party/omarchy-agents) into account-icon glyphs.

Each mark becomes one glyph in ui_contract.json (account_icons.glyphs): all of the file's path data
joined into one "d", its viewBox, and how it is painted ("fill" with its fill rule, or "stroke" with
its width), plus "source" naming the file. The page and the bar panel draw it in the account's theme
colour, so a mark that ships white (for dark bars) or in brand colours looks right on every theme.
Glyphs that are not from Omarchy (gemini, the plain shapes) are kept as they are.

    python3 scripts/import_omarchy_icons.py          # rewrite the glyphs, then run gen_ui_contract.py
    python3 scripts/import_omarchy_icons.py --check  # fail if ui_contract.json differs from the files
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "third_party/omarchy-agents"
CONTRACT = ROOT / "herdr_attention/ui_contract.json"

# glyph id -> (file, label). Order is the order in Settings > Account icons.
MARKS = [
    ("claude", "claude.svg", "Claude"),
    ("codex", "codex.svg", "Codex"),
    ("cursor", "cursor.svg", "Cursor"),
    ("agy", "agy.svg", "Antigravity"),
    ("grok", "grok.svg", "Grok"),
    ("grokbot", "grokbot.svg", "Grok Bot"),
    ("copilot", "copilot.svg", "GitHub Copilot"),
    ("opencode", "opencode.svg", "OpenCode"),
    ("kimi", "kimi.svg", "Kimi"),
    ("qwen", "qwen.svg", "Qwen"),
    ("deepseek", "deepseek.svg", "DeepSeek"),
    ("ollama", "ollama.svg", "Ollama"),
    ("zai", "zai.svg", "Z.ai"),
    ("openrouter", "openrouter.svg", "OpenRouter"),
    ("minimax", "minimax.svg", "MiniMax"),
    ("muse", "muse.svg", "Muse"),
    ("devin", "devin.svg", "Devin"),
]

ATTR = r"""\b{}\s*=\s*["']([^"']*)["']"""


def attr(text: str, name: str) -> str | None:
    m = re.search(ATTR.format(re.escape(name)), text)
    return m.group(1) if m else None


def glyph(gid: str, file: str, label: str) -> dict:
    svg = (ASSETS / file).read_text()
    root = re.search(r"<svg\b[^>]*>", svg).group(0)
    vb = " ".join(attr(root, "viewBox").replace(",", " ").split())
    # Only the drawn paths; a mask's or clip's own rectangle (OpenCode) is not part of the mark.
    body = re.sub(r"<(mask|clipPath|defs)\b.*?</\1>", "", svg, flags=re.S)
    ds = [" ".join(d.split()) for d in re.findall(r"<path\b[^>]*?\bd\s*=\s*[\"']([^\"']+)[\"']", body, re.S)]
    if not ds:
        raise SystemExit(f"{file}: no path")
    out = {"id": gid, "label": label, "d": " ".join(ds), "view_box": vb}
    stroke = attr(body, "stroke")
    if stroke and stroke != "none" and "fill=\"none\"" in body.replace("'", '"'):
        out["paint"] = "stroke"
        out["stroke_width"] = float(attr(body, "stroke-width") or 1)
    else:
        out["paint"] = "fill"
        # OpenCode's frame and inner block are two fills; drawn in one colour they need even-odd
        # so the frame keeps its hole.
        out["fill_rule"] = "evenodd" if ("evenodd" in svg or gid == "opencode") else "nonzero"
    out["source"] = f"omarchy.agents/assets/{file}"
    return out


def build(contract: dict) -> dict:
    spec = contract["account_icons"]
    ours = [glyph(*m) for m in MARKS]
    ids = {g["id"] for g in ours}
    spec["glyphs"] = ours + [g for g in spec["glyphs"] if g["id"] not in ids]
    return contract


def main() -> int:
    text = CONTRACT.read_text()
    new = json.dumps(build(json.loads(text)), indent=2) + "\n"
    if "--check" in sys.argv:
        if new != text:
            print("account icon marks differ from third_party/omarchy-agents: run scripts/import_omarchy_icons.py")
            return 1
        return 0
    CONTRACT.write_text(new)
    return 0


if __name__ == "__main__":
    sys.exit(main())
