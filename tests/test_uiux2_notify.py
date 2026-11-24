"""uiux2: attention rows use the phone notification title; hints fit the listener."""
import unittest

from herdr_attention.content import LIVE_COMPLETION_HINT_REMOTE, read_action
from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER, run
from tests.test_typing_survives_refresh import reading


class ReadHint(unittest.TestCase):
    def test_live_completion_has_a_phone_hint(self):
        action = read_action({'present': True, 'kind': 'completion', 'status': 'done', 'event_id': 7})
        self.assertFalse(action['available'])
        self.assertEqual(action['remote_reason'], LIVE_COMPLETION_HINT_REMOTE)
        self.assertIn('laptop', action['remote_reason'])
        self.assertIsNone(read_action({'present': True, 'kind': 'failure', 'event_id': 7})['remote_reason'])


def coordinator_fixture(desktop):
    data = page_fixture()
    view = data['view']
    a = next(x for x in view['agents'] if x.get('tier'))
    a.update(tier='waiting_on_coordinator', tier_label='Waiting on a coordinator', reason=None,
             label='Finished, not reviewed', kind='completion', status='done', present=True, event_id=4242, acked=False,
             read_action={'available': False, 'reason': 'Clears when the pane is viewed in Herdr or the agent moves on.',
                          'remote_reason': LIVE_COMPLETION_HINT_REMOTE})
    view['notifications'] = {'unread': 1, 'items': [{'id': 4242, 'group_ids': [4242], 'what': 'Approve publishing for tomorrow'}]}
    view['features']['desktop_focus'] = desktop
    return data, a


@unittest.skipUnless(BROWSER, 'no Chromium')
class AttentionRowUi(unittest.TestCase):
    def check(self, desktop):
        data, a = coordinator_fixture(desktop)
        r = reading(run(data, '/list', 390, 844, script=f"""
          const li=document.querySelector('[data-reading-key^="attention:{a['identity']}"]');
          document.body.dataset.reading=JSON.stringify({{head:li.querySelector('.att-headline')?.textContent,
            reason:li.querySelector('.reason').textContent, hint:(li.querySelector('.read-state-hint')||li.closest('.attention-group')?.querySelector('.section-hint'))?.textContent,  // 0.9.8.6: once per section when shared
           
            text:li.textContent}});"""))
        self.assertEqual(r['head'], 'Approve publishing for tomorrow')
        self.assertNotIn('Finished, not reviewed', r['text'])
        return r

    def test_phone_listener_names_the_laptop(self):
        self.assertEqual(self.check(False)['hint'], LIVE_COMPLETION_HINT_REMOTE)

    def test_desktop_listener_keeps_herdr_hint(self):
        self.assertIn('viewed in Herdr', self.check(True)['hint'])


if __name__ == '__main__':
    unittest.main()


@unittest.skipUnless(BROWSER, 'no Chromium')
class NotificationRowsUi(unittest.TestCase):
    def test_rows_show_tier_pill_no_zero_question_card_and_fyi_open_when_nothing_actionable(self):
        data = page_fixture()
        data['view']['questions'] = []
        for n in data.get('notifications', []):
            n['tier'], n['tier_label'], n['actionable'], n['read'] = 'fyi_finished', 'Finished', False, False
        r = reading(run(data, '/n', 390, 844, script="""
          await new Promise(r=>setTimeout(r,300));
          const rows=[...document.querySelectorAll('article.notif:not(.is-focused)')];
          document.body.dataset.reading=JSON.stringify({rows:rows.length,
            pills:rows.filter(a=>a.querySelector('.notif-what .pill')).length,
            accent:rows.filter(a=>a.classList.contains('is-actionable')).length,
            questions:!!document.querySelector('.notifications .questions-for-you'),
            open:document.querySelector('.notif-other')?.open ?? null});"""))
        self.assertGreater(r['rows'], 0)
        self.assertEqual(r['pills'], r['rows'])
        self.assertEqual(r['accent'], 0)
        self.assertFalse(r['questions'])
        self.assertTrue(r['open'])
