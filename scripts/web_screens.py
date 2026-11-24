#!/usr/bin/env python3
"""Render the phone page with fixture data at phone and desktop width.

Writes docs/screenshots/{phone,desktop}-{overview,workspace,agent,settings,empty}.png.
Uses the shipped page and a stubbed API (tests/web_harness.py); no collector,
no Herdr, a throwaway browser profile.
"""

import sys
import urllib.parse
from pathlib import Path

repo = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo))

from tests.web_fixture import page_fixture  # noqa: E402
from tests.web_harness import BROWSER, run  # noqa: E402


def main() -> int:
    if not BROWSER:
        print("no Chromium found", file=sys.stderr)
        return 2
    out = repo / "docs" / "screenshots"
    out.mkdir(parents=True, exist_ok=True)
    data = page_fixture()
    empty = page_fixture(watched=())
    stalled = next(a for a in data["view"]["agents"] if a["name"] == "web-lead")
    routes = {
        "graph": (data, ""),
        "graph-selected": (data, "/g/" + urllib.parse.quote(data["ids"]["ui"], safe="")),
        "list": (data, "/list"),
        "workspace": (data, "/w/w1"),
        "agent": (data, "/a/" + urllib.parse.quote(stalled["identity"], safe="")),
        "settings": (data, "/settings"),
        "empty": (empty, ""),
    }
    for name, (fixture, route) in routes.items():
        for device, width, height in (("phone", 390, 844 if name.startswith(("graph", "empty")) else 1500), ("desktop", 1280, 800)):
            path = out / f"{device}-{name}.png"
            result = run(fixture, route, width, height, path)
            probe = result["probe"]
            print(f"{path.name}: width {probe.get('innerWidth')}, page {probe.get('scrollWidth')}, "
                  f"overflow {probe.get('overflowing') or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
