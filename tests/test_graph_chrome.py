"""Graph chrome: the status header survives polls, and Quotas has its own icon."""
import unittest
from pathlib import Path

from tests.test_graph_readability import busy_fixture
from tests.test_typing_survives_refresh import reading
from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER, run

WEB = Path(__file__).parent.parent / 'herdr_attention' / 'web'


@unittest.skipUnless(BROWSER, 'no Chromium found')
class GraphChrome(unittest.TestCase):
    def test_status_header_is_not_replaced_by_an_unchanged_poll(self):
        r = reading(run(busy_fixture(), '', 390, 844, script="""
          const before = document.querySelector('.graph-top .status-header');
          FIXTURE.view.version++; await refresh(true);
          const after = document.querySelector('.graph-top .status-header');
          document.body.dataset.reading = JSON.stringify({same: before === after,
            height: after.getBoundingClientRect().height});
        """))
        self.assertTrue(r['same'])          # a tap on it is never lost to a redraw
        self.assertGreaterEqual(r['height'], 64)

    def test_cut_region_titles_keep_their_last_word(self):
        data = busy_fixture()
        view = data['view']
        for ws, label in zip(view['workspaces'][:2], ('herdr-dashboard', 'herdr-dash-notif-read')):
            ws['label'] = label
        r = reading(run(data, '', 390, 844, script="""
          document.body.dataset.reading = JSON.stringify([...document.querySelectorAll('svg.graph .region-label')].map(e => e.textContent));
        """))
        self.assertEqual(len(r), len(set(r)))   # no two regions look the same

    def test_desktop_toolbar_never_covers_open_questions(self):
        # page_fixture() has one open question; with none the Questions block is hidden.
        r = reading(run(page_fixture(), '', 1280, 800, script="""
          const q = document.querySelector('.graph-questions');
          q.open = true;
          await new Promise(r => setTimeout(r, 50));
          const a = q.getBoundingClientRect(), t = document.querySelector('.graph-toolbar').getBoundingClientRect();
          document.body.dataset.reading = JSON.stringify({grew: a.height > 60, clear: t.top >= a.bottom});
        """))
        self.assertTrue(r['grew'])
        self.assertTrue(r['clear'])


class Icons(unittest.TestCase):
    def test_quotas_tab_and_view_tool_use_different_icons(self):
        html = (WEB / 'index.html').read_text()
        quotas = html.split('data-tab="quotas"', 1)[1].split('</a>', 1)[0]
        self.assertNotIn('#i-eye', quotas)   # the eye belongs to the graph View tool
        self.assertIn('<symbol id="i-gauge"', html)


if __name__ == '__main__':
    unittest.main()
