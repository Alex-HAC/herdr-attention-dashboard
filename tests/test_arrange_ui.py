"""v0.9.4 Arrange sheet and block order on List and Notifications (390 px frame, desktop drag)."""
import json
import unittest

from tests.test_notif_row_390 import run390
from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER, run

ACCOUNTS = [{"id": i, "provider": i, "provider_name": name, "label": name, "windows": []}
            for i, name in (("claude:personal", "Claude Personal"), ("codex", "Codex Work"),
                            ("agy", "Antigravity"), ("grokbot", "Grok Bot"))]

# Keep POSTed settings in the stubbed view, as the server does, and record every write.
CAPTURE = """
  window.__posts=[];
  const stubFetch=window.fetch;
  window.fetch=async(path,init)=>{
    if(init?.method==='POST'){
      const body=JSON.parse(init.body||'{}');window.__posts.push([String(path),body]);
      for(const [key,value] of Object.entries(body)) if(key.startsWith('layout.')) FIXTURE.view.layout[key.slice(7)]=value;
    }
    return stubFetch(path,init);
  };
  const wait=ms=>new Promise(r=>setTimeout(r,ms));
  const out={};
  const done=()=>{(window.parent!==window ? window.parent.document.body : document.body).dataset.reading=JSON.stringify(out);};
  const sections=()=>[...new Set([...document.querySelectorAll('main [data-section]')].map(e=>e.dataset.section))];
  const rows=kind=>[...document.querySelectorAll(`.arrange-list[data-reading-key="arrange-${kind}"] .arrange-row`)];
  const ids=kind=>rows(kind).map(r=>r.dataset.arrangeId);
  const press=(kind,id,focus)=>document.querySelector(`[data-arrange-focus="${kind}:${id}:${focus}"]`).click();
"""


def fixture(layout=None, fyi=True):
    data = page_fixture()
    view = data["view"]
    view["quotas"]["accounts"] = ACCOUNTS
    view["layout"].update({"account_order": [], "account_pinned": [], "account_hidden": [],
                           "section_order": ["needs_action", "quotas", "status_reports", "latest_visuals", "other_updates"],
                           "section_hidden": []}, **(layout or {}))
    if fyi:   # one unread update that needs no action
        n = next(n for n in data["notifications"] if n["tier"] == "waiting_on_coordinator")
        n.update(actionable=False, read=False)
        view["notifications"]["items"] = [n]
    return data


@unittest.skipUnless(BROWSER, "no Chromium")
class BlockOrder(unittest.TestCase):
    def test_list_follows_saved_order_and_names_hidden_blocks(self):
        data = fixture({"section_order": ["quotas", "other_updates", "needs_action"], "section_hidden": ["latest_visuals"]})
        r = run390(data, "/list", CAPTURE + """
          await wait(300);
          out.sections=sections();
          out.visuals=!!document.querySelector('main .latest-visuals');
          out.hidden=document.querySelector('.arrange-hidden')?.textContent || '';
          out.arrange=!!document.querySelector('.arrange-bar .arrange-open');
          out.scroll=document.documentElement.scrollWidth;
          done();""")
        self.assertEqual(r["sections"][:3], ["quotas", "other_updates", "needs_action"])
        self.assertIn("status_reports", r["sections"], "web-lead is a coordinator")
        self.assertNotIn("latest_visuals", r["sections"])
        self.assertFalse(r["visuals"])
        self.assertIn("Latest visuals", r["hidden"])
        self.assertTrue(r["arrange"])
        self.assertLessEqual(r["scroll"], 390)

    def test_notifications_follow_saved_order_and_count_hidden_updates(self):
        data = fixture({"section_order": ["other_updates", "needs_action"]})
        script = CAPTURE + """
          await wait(400);
          const other=document.querySelector('.notif-other'), need=document.querySelector('[data-reading-key="needs-action"]');
          out.otherFirst=!!(other && need && (other.compareDocumentPosition(need) & Node.DOCUMENT_POSITION_FOLLOWING));
          out.hidden=document.querySelector('.arrange-hidden')?.textContent || '';
          out.arrange=!!document.querySelector('.notif-top .arrange-open');
          done();"""
        r = run390(data, "/n", script)
        self.assertTrue(r["otherFirst"])
        self.assertTrue(r["arrange"])
        self.assertEqual(r["hidden"], "")
        data = fixture({"section_hidden": ["other_updates"]})
        r = run390(data, "/n", script)
        self.assertRegex(r["hidden"], r"Hidden: Other updates \(\d+\)")


@unittest.skipUnless(BROWSER, "no Chromium")
class ArrangeSheet(unittest.TestCase):
    def test_phone_sheet_moves_pins_hides_saves_and_survives_refresh(self):
        data = fixture({"account_order": ["codex", "claude:personal"], "account_pinned": ["agy"]})
        r = run390(data, "/list", CAPTURE + """
          await wait(300);
          document.querySelector('.arrange-bar .arrange-open').click();await wait(50);
          const sheet=document.querySelector('.arrange');
          out.open=!document.getElementById('arrange-sheet').hidden;
          out.sectionRows=ids('sections');
          const need=rows('sections')[0];
          out.needHide=!!need.querySelector('.arrange-hide');
          out.needText=need.textContent;
          out.accounts=ids('accounts');
          out.disabled=rows('accounts').map(r=>[r.dataset.arrangeId,
            r.querySelector('[data-arrange-focus$=":up"]').disabled,r.querySelector('[data-arrange-focus$=":down"]').disabled]);
          out.draggable=rows('sections').some(r=>r.draggable);
          // Move Needs action down one place; focus follows the row.
          press('section','needs_action','down');await wait(600);
          out.afterMove=ids('sections');
          out.focus=document.activeElement?.dataset.arrangeFocus || null;
          out.listAfterSave=sections();
          // A live refresh does not rebuild the open sheet or move focus.
          await refresh(true);render(true);await wait(50);
          out.sameSheet=document.querySelector('.arrange')===sheet && !document.getElementById('arrange-sheet').hidden;
          out.focusAfterRefresh=document.activeElement?.dataset.arrangeFocus || null;
          // Pin Codex Work: it joins the pinned group at its end. Hide Claude Personal.
          press('account','codex','account_pinned');await wait(50);
          press('account','claude:personal','account_hidden');await wait(600);
          out.accountsAfter=ids('accounts');
          out.tags=rows('accounts').map(r=>r.querySelector('.arrange-name').textContent);
          out.status=document.querySelector('.arrange-status').textContent;
          const box=sheet.getBoundingClientRect();
          out.fits=[...sheet.querySelectorAll('button')].every(b=>{const r=b.getBoundingClientRect();return r.right<=box.right+0.5 && r.right<=innerWidth;});
          out.small=[...sheet.querySelectorAll('button')].filter(b=>Math.min(b.getBoundingClientRect().width,b.getBoundingClientRect().height)<44).map(b=>b.textContent);
          out.sheetScroll=sheet.scrollWidth<=sheet.clientWidth;
          out.last=window.__posts.at(-1);
          out.paths=[...new Set(window.__posts.map(p=>p[0]))];
          document.querySelector('.arrange .actions .btn.primary').click();await wait(50);
          out.closed=document.getElementById('arrange-sheet').hidden;
          out.focusBack=document.activeElement?.classList.contains('arrange-open') || false;
          done();""")
        self.assertTrue(r["open"])
        self.assertEqual(r["sectionRows"], ["needs_action", "quotas", "status_reports", "latest_visuals", "other_updates"])
        self.assertFalse(r["needHide"], "Needs action can never be hidden")
        self.assertIn("Always shown", r["needText"])
        self.assertEqual(r["accounts"], ["agy", "codex", "claude:personal", "grokbot"], "pinned first, then the saved order")
        self.assertEqual(r["disabled"][0], ["agy", True, True], "a pinned row does not move below unpinned rows")
        self.assertEqual(r["disabled"][1], ["codex", True, False])
        self.assertFalse(r["draggable"], "phones move rows with up and down")
        self.assertEqual(r["afterMove"][:2], ["quotas", "needs_action"])
        self.assertEqual(r["focus"], "section:needs_action:down")
        self.assertEqual(r["listAfterSave"][:2], ["quotas", "needs_action"], "List follows the saved order")
        self.assertTrue(r["sameSheet"])
        self.assertEqual(r["focusAfterRefresh"], "section:needs_action:down")
        self.assertEqual(r["accountsAfter"], ["agy", "codex", "claude:personal", "grokbot"])
        self.assertIn("Pinned", r["tags"][1])
        self.assertIn("Hidden", r["tags"][2])
        self.assertEqual(r["status"], "Saved")
        self.assertTrue(r["fits"])
        self.assertEqual(r["small"], [])
        self.assertTrue(r["sheetScroll"])
        self.assertEqual(r["paths"], ["/api/settings"], "Arrange writes settings only")
        body = r["last"][1]
        self.assertEqual(body["layout.account_pinned"], ["agy", "codex"])
        self.assertEqual(body["layout.account_hidden"], ["claude:personal"])
        self.assertEqual(body["layout.account_order"][:3], ["agy", "codex", "claude:personal"])
        self.assertEqual(body["layout.section_order"][:2], ["quotas", "needs_action"])
        self.assertTrue(r["closed"])
        self.assertTrue(r["focusBack"])

    def test_escape_and_a_tap_beside_the_sheet_close_it_and_keep_the_last_change(self):
        r = run390(fixture(), "/n", CAPTURE + """
          await wait(300);
          document.querySelector('.notif-top .arrange-open').click();await wait(50);
          press('section','other_updates','section_hidden');
          document.querySelector('.arrange').dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
          await wait(400);
          out.closedByEscape=document.getElementById('arrange-sheet').hidden;
          out.saved=window.__posts.at(-1)?.[1]['layout.section_hidden'] || null;
          document.querySelector('.notif-top .arrange-open').click();await wait(50);
          document.querySelector('.arrange-backdrop').click();await wait(50);
          out.closedByTap=document.getElementById('arrange-sheet').hidden;
          done();""")
        self.assertTrue(r["closedByEscape"])
        self.assertEqual(r["saved"], ["other_updates"], "closing right after a change still saves it")
        self.assertTrue(r["closedByTap"])


@unittest.skipUnless(BROWSER, "no Chromium")
class DesktopDrag(unittest.TestCase):
    def test_drag_a_block_above_another(self):
        page = run(fixture(), "/list", 1280, 900, script=CAPTURE.replace(
            "window.parent!==window ? window.parent.document.body : document.body", "document.body") + """
          await wait(300);
          document.querySelector('.arrange-open').click();await wait(50);
          const [need,,,,other]=rows('sections');
          out.draggable=rows('sections').every(r=>r.draggable);
          const dt=new DataTransfer(), top=need.getBoundingClientRect().top+2;
          other.dispatchEvent(new DragEvent('dragstart',{bubbles:true,dataTransfer:dt}));
          need.dispatchEvent(new DragEvent('dragover',{bubbles:true,cancelable:true,dataTransfer:dt,clientY:top}));
          need.dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:dt,clientY:top}));
          await wait(600);
          out.order=ids('sections');
          out.saved=window.__posts.at(-1)?.[1]['layout.section_order'] || null;
          done();""")
        raw = page["dom"].split('data-reading="', 1)[1].split('"', 1)[0]
        r = json.loads(raw.replace("&quot;", '"').replace("&amp;", "&"))
        self.assertTrue(r["draggable"])
        self.assertEqual(r["order"][:2], ["other_updates", "needs_action"])
        self.assertEqual(r["saved"][:2], ["other_updates", "needs_action"])


if __name__ == "__main__":
    unittest.main()
