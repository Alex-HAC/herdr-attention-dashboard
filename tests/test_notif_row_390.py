"""v0.9.4: a compact notification row stays readable at 390 px with all actions present."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from herdr_attention.ui_contract import FEATURES
from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER, build_page

LONG = "Approve experimental 1.2.7 publishing for tomorrow and keep the default source install"

MEASURE = """
  await new Promise(r=>setTimeout(r,300));
  const rows=[...document.querySelectorAll('article.notif:not(.is-focused)')].map(row=>{
    const box=row.getBoundingClientRect(), pill=row.querySelector('.notif-what .pill'), head=row.querySelector('.notif-headline') || row.querySelector('.notif-what');
    const p=pill.getBoundingClientRect(), lines=[...head.getClientRects()], open=row.querySelector('.notif-open').getBoundingClientRect();
    const acts=row.querySelector('.notif-row-actions'), a=acts?.getBoundingClientRect(), what=row.querySelector('.notif-what').getBoundingClientRect();
    const buttons=[...(acts?.querySelectorAll('button') || [])];
    return {id:row.id, stacked:row.classList.contains('notif-stacked'),
      pillCut:pill.scrollWidth>pill.clientWidth+1 || p.right>box.right+0.5 || p.left<box.left-0.5,
      pillText:pill.textContent, room:what.width, headRight:Math.max(...lines.map(r=>r.right)), whatRight:what.right,
      boxRight:box.right, samelinePill:lines.length ? Math.abs(lines[0].top-p.top)<14 : false,
      actionsBelow:a ? a.top>=open.bottom-1 : null, actionsBeside:a ? a.left>=open.right-1 : null,
      reply:!!acts?.querySelector('.reply-open'), herdr:!!acts?.querySelector('.open-in-herdr'), read:!!acts?.querySelector('.notif-mark'),
      primary:buttons.filter(b=>b.classList.contains('primary')).map(b=>b.className),
      loud:buttons.filter(b=>!b.classList.contains('primary') && !b.classList.contains('quiet')).map(b=>b.textContent),
      fits:buttons.every(b=>b.getBoundingClientRect().right<=innerWidth+0.5)};
  });
  // Inside the 390 px frame: hand the facts to the outer page, whose DOM is dumped.
  (window.parent!==window ? window.parent.document.body : document.body).dataset.reading=
    JSON.stringify({rows,scroll:document.documentElement.scrollWidth,width:innerWidth});
"""


def run390(data, route, script, width=390, height=844):
    """Chromium will not open a window under 500 px, so measure inside a frame of the exact width."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        page = build_page(tmp, data, script)
        frame = tmp / "frame.html"
        frame.write_text(f'<!doctype html><body style="margin:0"><iframe src="{page.as_uri()}#{route}" '
                         f'width="{width}" height="{height}" style="border:0"></iframe></body>')
        outer = subprocess.run([BROWSER, "--headless=new", "--disable-gpu", "--no-first-run", "--hide-scrollbars",
                                f"--user-data-dir={tmp / 'profile'}", "--allow-file-access-from-files",
                                "--virtual-time-budget=6000", f"--window-size=800,{height}", "--dump-dom", frame.as_uri()],
                               capture_output=True, text=True, timeout=90).stdout
    raw = outer.split('data-reading="', 1)[1].split('"', 1)[0]
    return json.loads(raw.replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">"))


def fixture(desktop):
    data = page_fixture()
    data['view']['features'][FEATURES['desktop_focus']] = desktop
    data['view']['questions'] = []
    for n in data['notifications']:
        n['read'] = False
        if n['tier'] == 'waiting_on_you':
            n['what'], n['actionable'] = LONG, True
        if n['tier'] == 'waiting_on_coordinator':   # the longest tier pill
            n['what'], n['tier_label'], n['actionable'] = LONG, 'Waiting on a coordinator', False
    return data


@unittest.skipUnless(BROWSER, 'no Chromium')
class NotificationRow390(unittest.TestCase):
    def measure(self, desktop):
        r = run390(fixture(desktop), '/n', MEASURE)
        self.assertEqual(r['width'], 390)
        self.assertLessEqual(r['scroll'], r['width'])
        return r['rows']

    def test_desktop_listener_row_with_reply_open_in_herdr_and_read(self):
        rows = self.measure(True)
        full = [r for r in rows if r['reply'] and r['herdr'] and r['read']]
        self.assertTrue(full, 'a row with all three buttons')
        for r in rows:
            with self.subTest(row=r['id']):
                self.assertFalse(r['pillCut'], r)
                self.assertTrue(r['samelinePill'], r)
                self.assertGreaterEqual(r['room'], 250, r)
                self.assertLessEqual(r['headRight'], r['whatRight'] + 0.5, r)
                self.assertLessEqual(r['whatRight'], r['boxRight'], r)
                self.assertTrue(r['fits'], r)
                self.assertEqual(r['loud'], [], 'everything but the main button is quiet')
                self.assertLessEqual(len(r['primary']), 1)
        for r in full:
            self.assertTrue(r['stacked'])
            self.assertTrue(r['actionsBelow'], 'actions on their own line')
        urgent = next(r for r in full if r['primary'])
        self.assertIn('reply-open', urgent['primary'][0])
        coord = next(r for r in rows if r['pillText'] == 'Waiting on a coordinator')
        self.assertEqual(coord['primary'], [], 'no main button on a row that is not actionable')

    def test_phone_listener_rows_are_readable_too(self):
        rows = self.measure(False)
        self.assertTrue(rows)
        for r in rows:
            with self.subTest(row=r['id']):
                self.assertFalse(r['herdr'])
                self.assertFalse(r['pillCut'], r)
                self.assertGreaterEqual(r['room'], 250, r)


if __name__ == '__main__':
    unittest.main()
