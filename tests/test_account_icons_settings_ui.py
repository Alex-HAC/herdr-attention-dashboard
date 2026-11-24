"""v0.9.7 account icon settings: Settings > Account icons and the Quotas page panel, at 390 x 844."""
import base64
import json
import unittest

from tests.icon_fixture import icon_fixture, tiny_png
from tests.web_harness import BROWSER, run

# Record every write; keep saved settings in the stubbed view as the server does.
CAPTURE = """
  window.__writes=[];
  const stubFetch=window.fetch;
  window.fetch=async(path,init)=>{
    const method=init?.method||'GET';
    if(method!=='GET'){
      const body=init.body instanceof Blob ? {blob:init.body.type, size:init.body.size} : JSON.parse(init.body||'{}');
      window.__writes.push({path:String(path), method, csrf:!!init.headers?.['X-CSRF-Token'], type:init.headers?.['Content-Type'], body});
      if(String(path).endsWith('/api/settings')) Object.assign(FIXTURE.view.settings, body);
    }
    return stubFetch(path,init);
  };
  const wait=ms=>new Promise(r=>setTimeout(r,ms));
  const row=id=>document.querySelector(`.acct-row[data-account-id="${id}"]`);
  const open=async id=>{row(id).querySelector('.acct-row-head button').click(); await wait(60);};
  const saved=()=>window.__writes.filter(w=>w.path.endsWith('/api/settings')).map(w=>w.body['accounts.icons']).at(-1);
  const facts={};
  const done=()=>window.__calls.push(['__facts', JSON.stringify(facts)]);
"""


def facts(page):
    for path, value in page["probe"].get("calls", []):
        if path == "__facts":
            return json.loads(value)
    raise AssertionError("no facts from the frame")


def page(script, route="/settings", width=390, height=844):
    p = run(icon_fixture(), route, width, height,
            script=CAPTURE + "try {" + script + "} catch (e) { facts.error = String(e); }\ndone();")
    return p, facts(p)


@unittest.skipUnless(BROWSER, "no Chromium")
class AccountIconSettings(unittest.TestCase):
    def test_settings_lists_every_account_with_its_icon(self):
        p, f = page("""
          facts.nav=[...document.querySelectorAll('.settings-sections .chip')].map(c=>c.textContent);
          facts.rows=[...document.querySelectorAll('#settings-icons .acct-row')].map(r=>[r.dataset.accountId,
            r.querySelector('.acct-icon')?.className.split(' ').filter(c=>c.startsWith('acct-')).join(' '),
            r.querySelector('.acct-row-name small').textContent]);
        """)
        self.assertIn("Account icons", f["nav"])
        rows = {r[0]: r for r in f["rows"]}
        self.assertEqual(len(rows), 8)
        self.assertEqual(rows["codex"][1:], ["acct-icon acct-red", "Changed"])
        self.assertEqual(rows["claude:main"][2], "Default")
        self.assertIn("acct-image", rows["cursor"][1])
        self.assertIn("acct-text", rows["copilot"][1])
        self.assertEqual(p["probe"]["overflowing"], [])

    def test_colour_badge_mark_and_reset_save_the_setting(self):
        _, f = page("""
          await open('claude:personal');
          row('claude:personal').querySelector('.acct-swatch.acct-red').click(); await wait(20);
          facts.preview=row('claude:personal').querySelector('.acct-row-head .acct-icon').className;
          await wait(400);
          facts.colour=saved()['claude:personal'];
          const badge=row('claude:personal').querySelector('.acct-badge-input'); badge.value=' PP '; badge.dispatchEvent(new Event('change'));
          await wait(400);
          facts.badge=saved()['claude:personal'];
          row('claude:personal').querySelector('.acct-choice-text').click(); await wait(400);
          facts.text=saved()['claude:personal'];
          facts.status=row('claude:personal').querySelector('.acct-actions [role=status]').textContent;
          await open('codex');
          row('codex').querySelector('.acct-actions .btn').click(); await wait(400);
          facts.reset='codex' in saved();
          facts.keep=Object.keys(saved()).sort();
        """)
        self.assertIn("acct-red", f["preview"])                      # the preview follows at once
        self.assertEqual(f["colour"], {"color": "red"})
        self.assertEqual(f["badge"], {"color": "red", "badge": "PP"})
        self.assertEqual(f["text"], {"color": "red", "badge": "PP", "glyph": None})
        self.assertEqual(f["status"], "Saved")
        self.assertFalse(f["reset"])
        self.assertEqual(f["keep"], ["claude:personal", "copilot", "cursor"])

    def test_upload_sends_a_small_image_and_remove_uses_delete_with_csrf(self):
        # The headless test browser never finishes decoding an image in virtual time, so only the
        # downscale step is replaced here (it returns what the real one makes: a small PNG).
        _, f = page("""
          await open('agy');
          const svg=new File(['<svg/>'],'x.svg',{type:'image/svg+xml'}), dt2=new DataTransfer(); dt2.items.add(svg);
          const input=()=>row('agy').querySelector('.acct-file');
          input().files=dt2.files; input().dispatchEvent(new Event('change')); await wait(200);
          facts.svgStatus=row('agy').querySelector('.acct-actions [role=status]').textContent;
          facts.svgSent=window.__writes.filter(w=>w.path.includes('/api/account-icons/')).length;
          const small=new Blob([Uint8Array.from(atob('PNG_B64'), ch=>ch.charCodeAt(0))], {type:'image/png'});
          shrinkAccountImage=async(file)=>{facts.given=file.name; return small;};
          const dt=new DataTransfer(); dt.items.add(new File([small],'photo.png',{type:'image/png'}));
          input().files=dt.files; input().dispatchEvent(new Event('change')); await wait(300);
          facts.upload=window.__writes.find(w=>w.method==='POST' && w.path.includes('/api/account-icons/'));
          await open('cursor');
          row('cursor').querySelector('.acct-field .btn.quiet').click(); await wait(300);
          facts.remove=window.__writes.find(w=>w.method==='DELETE');
        """.replace("PNG_B64", base64.b64encode(tiny_png(96)).decode()))
        up = f["upload"]
        self.assertEqual(f["given"], "photo.png")
        self.assertEqual((up["path"], up["csrf"], up["type"]), ("/api/account-icons/agy", True, "image/png"))
        self.assertLessEqual(up["body"]["size"], 61440)
        self.assertEqual(f["svgSent"], 0)                             # an SVG never leaves the page
        self.assertIn("not a PNG, JPEG or WebP", f["svgStatus"])
        self.assertEqual((f["remove"]["path"], f["remove"]["csrf"]), ("/api/account-icons/cursor", True))

    def test_desktop_width_has_no_overflow(self):
        p, f = page("await open('codex'); facts.ok=!!row('codex').querySelector('.acct-editor');", width=1280, height=900)
        self.assertTrue(f["ok"])
        self.assertEqual(p["probe"]["overflowing"], [])


@unittest.skipUnless(BROWSER, "no Chromium")
class QuotasPageIcons(unittest.TestCase):
    def test_gauges_details_and_the_panel_show_the_icons(self):
        p, f = page("""
          facts.gauges=Object.fromEntries([...document.querySelectorAll('.quota-view [data-quota-id]')].map(g=>
            [g.dataset.quotaId, g.querySelector('.quota-gtitle .acct-icon')?.dataset.account||null]));
          document.querySelector('[data-quota-id="codex"]').click(); await wait(80);
          facts.detail=document.querySelector('#quota-open .quota-open-title .acct-icon')?.dataset.account;
          document.querySelector('.quota-settings-open').click(); await wait(80);
          facts.rows=[...document.querySelectorAll('.quota-arrange .arrange-row')].map(r=>r.querySelector('.acct-icon')?.dataset.account||null);
          facts.editor=document.querySelectorAll('#quota-settings .quota-icons .acct-row').length;
        """, route="/quotas")
        self.assertEqual(f["gauges"]["claude:main"], "claude:main")
        self.assertEqual(f["gauges"]["cursor"], "cursor")
        self.assertIsNone(f["gauges"]["copilot"])                    # text only: the name is already there
        self.assertEqual(f["detail"], "codex")
        self.assertEqual(f["rows"].count(None), 1)
        self.assertEqual(f["editor"], 8)
        self.assertEqual(p["probe"]["overflowing"], [])


if __name__ == "__main__":
    unittest.main()
