"""v0.9.7 accountIcon(icon, {size}): the one renderer, at 390 x 844 with the icon fixture."""
import json
import unittest

from tests.icon_fixture import icon_fixture
from tests.web_harness import BROWSER, run

RENDER = """
  await new Promise(r=>setTimeout(r,300));
  const box=document.createElement('div'); box.id='icon-test'; document.querySelector('main').prepend(box);
  const facts={accounts:{}, agents:{}};
  const read=(node)=>{
    if(!node) return null;
    const glyph=node.querySelector('.acct-glyph'), cs=getComputedStyle(node);
    return {kind:node.querySelector('img')?'image':glyph?'glyph':'text', cls:node.className, badge:node.querySelector('.acct-badge')?.textContent||null,
      label:node.getAttribute('aria-label')||node.textContent, size:Math.round(node.getBoundingClientRect().width),
      stroke:glyph?getComputedStyle(glyph).stroke:null, fill:glyph?getComputedStyle(glyph).fill:null, colour:cs.color,
      glyph:node.dataset.glyph||null, viewBox:glyph?.getAttribute('viewBox')||null, filled:!!glyph?.classList.contains('is-fill'),
      mark:glyph?Math.round(glyph.getBoundingClientRect().width*10)/10:null, pathStroke:glyph?getComputedStyle(glyph.querySelector('path')).strokeWidth:null};
  };
  for(const a of FIXTURE.view.quotas.accounts){const n=accountIcon(a.icon,{size:28}); box.append(n); facts.accounts[a.id]=read(n);}
  const tokens=getComputedStyle(document.documentElement);
  facts.tokens=Object.fromEntries(UI.account_icons.colors.map(c=>[c.id,tokens.getPropertyValue('--icon-'+c.id).trim()]));
  for(const a of FIXTURE.view.agents) facts.agents[a.name||a.identity]=a.icon?read(accountIcon(a.icon)):null;
  facts.none=accountIcon(undefined)===null && accountIcon(null)===null;
  facts.noText=accountIcon(FIXTURE.view.quotas.accounts.find(a=>a.id==='copilot').icon,{text:false});
  const broken=accountIcon({...FIXTURE.view.quotas.accounts.find(a=>a.id==='cursor').icon, image:'file:///nowhere/x.png'});
  box.append(broken); await new Promise(r=>setTimeout(r,300));
  facts.broken=read(box.querySelector('[data-account="cursor"]:last-child'));
  // v0.9.8.5: every mark, at 14 px (List, graph) and 24 px, in a dark and a light colour.
  facts.marks={};
  for(const g of UI.account_icons.glyphs) for(const size of [14,24]){
    const n=accountIcon({account:'t-'+g.id, label:g.label, glyph:g.id, color:'blue', badge:null, image:null},{size}); box.append(n);
    facts.marks[g.id+'@'+size]=read(n);
  }
  window.__calls.push(['__facts', JSON.stringify(facts)]);
"""


def facts(page):
    for path, value in page["probe"].get("calls", []):
        if path == "__facts":
            return json.loads(value)
    raise AssertionError("no facts from the 390 px frame")


@unittest.skipUnless(BROWSER, "no Chromium")
class AccountIconRenderer(unittest.TestCase):
    def test_marks_colours_badges_images_and_the_text_fallback(self):
        data = icon_fixture()
        page = run(data, "/list", 390, 844, script=RENDER)
        f = facts(page)
        a = f["accounts"]
        self.assertEqual(a["claude:main"]["kind"], "glyph")
        self.assertEqual((a["claude:main"]["badge"], a["claude:personal"]["badge"]), ("M", "P"))
        self.assertIn("acct-red", a["codex"]["cls"])
        self.assertEqual(a["codex"]["badge"], "Wk")
        self.assertEqual(a["cursor"]["kind"], "image")
        self.assertEqual((a["copilot"]["kind"], a["copilot"]["label"]), ("text", "GitHub Copilot"))
        self.assertEqual(a["claude:main"]["label"], "Claude Code Main")
        self.assertEqual(a["agy"]["size"], 28)
        # The mark is drawn in the live theme colour of its name.
        rgb = lambda hex_: "rgb({}, {}, {})".format(*(int(hex_[i:i + 2], 16) for i in (1, 3, 5)))
        # v0.9.8.5: Claude and Codex are Omarchy's filled marks, filled in that colour, no outline.
        self.assertEqual((a["claude:main"]["fill"], a["claude:main"]["stroke"]), (rgb(f["tokens"]["yellow"]), "none"))
        self.assertEqual(a["codex"]["fill"], rgb(f["tokens"]["red"]))
        self.assertEqual((a["claude:main"]["glyph"], a["claude:main"]["viewBox"]), ("claude", "0 0 256 257"))
        self.assertEqual(a["agy"]["glyph"], "agy")
        # Agents: an icon only with a recorded account.
        self.assertEqual(f["agents"]["web-lead"]["badge"], "M")
        self.assertIsNone(f["agents"]["docs-writer"])
        self.assertTrue(f["none"])
        self.assertIsNone(f["noText"])
        self.assertEqual(f["broken"]["kind"], "glyph")          # a broken image falls back to the mark
        self.assertEqual(page["probe"]["overflowing"], [])
        blue = rgb(f["tokens"]["blue"])
        for key, m in f["marks"].items():
            gid, size = key.split("@")
            self.assertEqual((m["kind"], m["size"]), ("glyph", int(size)), key)
            if m["filled"]:
                self.assertEqual(m["fill"], blue, key)
                # A filled mark takes 80% of a small tile, 68% of a larger one.
                self.assertAlmostEqual(m["mark"], int(size) * (0.8 if int(size) <= 16 else 0.68), delta=0.6, msg=key)
            else:
                self.assertEqual(m["stroke"], blue, key)
        self.assertTrue(f["marks"]["claude@14"]["filled"])
        self.assertFalse(f["marks"]["cursor@14"]["filled"])   # Omarchy's Cursor cube is a line mark
        self.assertEqual(f["marks"]["cursor@14"]["pathStroke"], "5.33333px")   # at least 2 per 24 units


if __name__ == "__main__":
    unittest.main()
