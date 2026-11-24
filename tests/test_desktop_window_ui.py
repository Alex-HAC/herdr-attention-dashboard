"""The shipped page never creates focus controls on the phone surface."""
import copy
import unittest

from herdr_attention.ui_contract import FEATURES, hash_path
from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER, run


@unittest.skipUnless(BROWSER, 'no Chromium found')
class DesktopWindowUi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = page_fixture()
        cls.agent = next(a for a in cls.data['view']['agents'] if a['name'] == 'web-lead')

    def fixture(self, desktop):
        data = copy.deepcopy(self.data)
        data['view']['features'][FEATURES['desktop_focus']] = desktop
        return data

    def routes(self):
        return ['/list', '/w/' + self.agent['workspace_id'],
                hash_path('agent', self.agent['identity'])[1:], '/n',
                hash_path('graph', self.agent['identity'])[1:]]

    def test_phone_markup_lacks_focus_buttons_on_every_view(self):
        for route in self.routes():
            with self.subTest(route=route):
                page = run(self.fixture(False), route)
                self.assertNotIn('class="btn open-in-herdr"', page['dom'])
                self.assertFalse(any(path == '/api/focus' for path, _ in page['probe']['calls']))

    def test_desktop_action_present_on_every_view_and_title_is_stable(self):
        for route in self.routes():
            with self.subTest(route=route):
                page = run(self.fixture(True), route, width=1280)
                # The compact notification row shows it as a quiet button (v0.9.4); a List row as a
                # quiet icon button (details review 2026-11-24).
                self.assertRegex(page['dom'], r'class="btn open-in-herdr( quiet)?( icon-only)?"', route)
                self.assertEqual(page['probe']['title'], 'Herdr overview')
                self.assertFalse(any(path == '/api/focus' for path, _ in page['probe']['calls']))

    def test_click_focus_posts_identity_and_stays_on_conversation(self):
        identity = self.agent['identity']
        page = run(self.fixture(True), hash_path('agent', identity)[1:], width=1280, script='''
          document.querySelector('button.open-in-herdr').click();
        ''')
        self.assertIn(['/api/focus', 'POST'], page['probe']['calls'])
        self.assertEqual(page['probe']['hash'], hash_path('conversation', identity))
