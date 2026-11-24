"""Workspace/tab layout suggestions in the real browser, using observed fixture data."""
import unittest
from tests.web_fixture import build
from tests.web_harness import BROWSER, run


@unittest.skipUnless(BROWSER, 'no Chromium found')
class LayoutUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = build()
        c = cls.data.pop('collector')
        original_tabs = c.client.tabs()
        for tab in original_tabs:
            if tab['workspace_id'] == 'w1':
                tab['label'] = '1'
        c.client.tabs = lambda: original_tabs
        info = c.client.agent('w1:p2')
        info.update(name=None, terminal_title_stripped='Continue the task')
        c.client.set(info)
        c.handle_snapshot()
        cls.data['view'] = c.view(local=False)
        c.store.close()

    def test_workspace_and_tab_hints_show_commands_without_execution(self):
        for width in (390, 1280):
            page = run(self.data, '/w/w1', width, 900)
            self.assertIn('tidy-up tip', page['dom'])
            self.assertIn('herdr agent rename w1:p2', page['dom'])
            self.assertIn('herdr tab rename', page['dom'])
            self.assertIn('layout: ', page['dom'])
            self.assertEqual(page['probe']['overflowing'], [])
            self.assertFalse(any(call[1] == 'POST' for call in page['probe']['calls']))
        graph = run(self.data, '/g', 390, 844, script="""
            graph.panel = 'workspaces'; graphRefresh(true);
        """)
        self.assertIn('tidy-up tip', graph['dom'])
        self.assertIn('herdr agent rename w1:p2', graph['dom'])
