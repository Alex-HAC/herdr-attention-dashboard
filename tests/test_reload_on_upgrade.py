"""v0.9.7.5: an open page reloads once after an upgrade, so it never keeps drawing old marks.

Alex's laptop window still ran pre-0.9.7 code after the install and showed CC / CX pills on every
node, although the server sent account icons. The renderer itself was fine (checked with the live
view at 571 px: icons on every node with a recorded account)."""

import unittest

from tests.icon_fixture import icon_fixture
from tests.test_typing_survives_refresh import reading
from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER, run

CHECK = """
  await new Promise(r => setTimeout(r, 300));
  const store = {}; const storage = {getItem: k => store[k] ?? null, setItem: (k, v) => { store[k] = v; }};
  let reloads = 0; const reload = () => { reloads++; };
  const v = (ver) => ({collector: {version: ver}});
  const started = app.loadedVersion;
  const same = reloadIfUpgraded(v(started), {reload, storage});
  const newer = reloadIfUpgraded(v('9.9.9'), {reload, storage});
  const again = reloadIfUpgraded(v('9.9.9'), {reload, storage});     // loop guard
  const input = document.createElement('input'); document.body.appendChild(input); input.focus();
  const typing = reloadIfUpgraded(v('9.9.10'), {reload, storage});
  input.blur(); input.remove();
  const later = reloadIfUpgraded(v('9.9.10'), {reload, storage});
  document.body.dataset.reading = JSON.stringify({started, same, newer, again, typing, later, reloads});
"""

NODES = """
  await new Promise(r => setTimeout(r, 500));
  const nodes = [...document.querySelectorAll('svg.graph .node')].filter(g => !g.classList.contains('filtered'));
  document.body.dataset.reading = JSON.stringify(nodes.map(g => ({
    id: g.dataset.id || null,
    icon: !!g.children[9].firstChild && g.children[9].getAttribute('visibility') === 'visible',
    pill: g.children[7].getAttribute('visibility') === 'visible', pillText: g.children[7].textContent})));
"""


@unittest.skipUnless(BROWSER, "no Chromium found")
class ReloadOnUpgrade(unittest.TestCase):
    def test_reloads_once_for_a_new_version_and_never_while_typing(self):
        data = page_fixture()
        r = reading(run(data, "/list", 390, 844, script=CHECK))
        self.assertEqual(r["started"], data["view"]["collector"]["version"])
        self.assertEqual((r["same"], r["newer"], r["again"], r["typing"], r["later"]), (False, True, False, False, True))
        self.assertEqual(r["reloads"], 2)


@unittest.skipUnless(BROWSER, "no Chromium found")
class IconsAtLaptopWidth(unittest.TestCase):
    def test_a_recorded_account_shows_its_icon_not_a_pill_at_571_px(self):
        data = icon_fixture()
        with_icon = {a["identity"] for a in data["view"]["agents"] if (a.get("icon") or {}).get("glyph")}
        self.assertTrue(with_icon)
        nodes = reading(run(data, "", 571, 1024, script=NODES))
        for n in nodes:
            with self.subTest(pill=n["pillText"]):
                self.assertFalse(n["icon"] and n["pill"], "one mark per node")
        self.assertGreaterEqual(sum(n["icon"] for n in nodes), len(with_icon) - 1)
        self.assertNotIn("CC", [n["pillText"] for n in nodes])
        self.assertNotIn("CX", [n["pillText"] for n in nodes])


if __name__ == "__main__":
    unittest.main()
