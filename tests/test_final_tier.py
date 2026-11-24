"""uiux2 F1/F2: one tier for phone text and app; readable coordinator names."""
import unittest

from herdr_attention.attention import coordinator_of
from herdr_attention.content import agent_name, presentation
from herdr_attention.notify_gate import format_message
from herdr_attention.notify_tiers import gate_tier

ASK = {'decision': 'send', 'tier': 'waiting_on_you', 'title': 'Approve experimental 1.2.7 publishing'}


class FinalTier(unittest.TestCase):
    def test_owner_ask_with_inferred_coordinator_is_waiting_on_you(self):
        coord = {'identity': 'codex:lead', 'name': 'lead', 'inferred': True}
        self.assertEqual(gate_tier('waiting_on_coordinator', ASK, coord), 'waiting_on_you')

    def test_owner_ask_with_registered_coordinator_stays_with_coordinator(self):
        coord = {'identity': 'codex:lead', 'name': 'lead', 'inferred': False}
        self.assertEqual(gate_tier('waiting_on_coordinator', ASK, coord), 'waiting_on_coordinator')

    def test_please_line_only_for_actionable_tiers(self):
        self.assertIn('Please: Approve it', format_message('T', 'Body.', 'Approve it', [], 'waiting_on_you')['message'])
        self.assertNotIn('Please:', format_message('T', 'Body.', 'Approve it', [], 'waiting_on_coordinator')['message'])
        self.assertIn('Please:', format_message('T', 'Body.', 'Approve it', [])['message'])

    def test_presentation_never_says_nothing_to_do_when_waiting_on_you(self):
        p = presentation({'identity': 'a', 'tier': 'waiting_on_you', 'kind': 'completion'})
        self.assertEqual(p['kind'], 'open_agent')
        self.assertNotIn('Nothing to do', p['text'])
        self.assertIn('Nothing to do', presentation({'identity': 'a', 'tier': 'fyi_finished', 'kind': 'completion'})['text'])


class AppAgrees(unittest.TestCase):
    def test_notice_tier_waiting_on_you_beats_the_coordinator_branch(self):
        from herdr_attention.attention import classify_tier
        from herdr_attention.config import Settings
        agent = {'identity': 'w', 'kind': 'completion', 'status': 'done', 'present': True}
        links = [{'kind': 'reports_to', 'from': 'w', 'to': 'codex:lead', 'to_name': 'lead'}]
        settings = Settings({})
        self.assertEqual(classify_tier(agent, settings=settings, links=links).tier, 'waiting_on_coordinator')
        found = classify_tier(agent, settings=settings, links=links, notice_tier='waiting_on_you')
        self.assertEqual(found.tier, 'waiting_on_you')
        self.assertIsNone(classify_tier(dict(agent, acked=True), settings=settings, links=links,
                                        notice_tier='waiting_on_you'))


class BatchTitle(unittest.TestCase):
    def test_batch_title_uses_status_header_words(self):
        from herdr_attention.notify_tiers import batch_title
        self.assertEqual(batch_title(['fyi_finished', 'might_look']), 'Herdr: 2 to look at')
        self.assertEqual(batch_title(['waiting_on_you', 'fyi_finished']), 'Herdr: 1 waiting on you · 1 to look at')
        self.assertEqual(batch_title(['waiting_on_you'] * 2), 'Herdr: 2 waiting on you')


class CoordinatorName(unittest.TestCase):
    def link(self, **kw):
        return [dict({'kind': 'reports_to', 'from': 'w', 'to': 'codex:01a1d74c-2543-7440-aa23-72c77885ed4a'}, **kw)]

    def test_status_word_is_not_a_name(self):
        self.assertEqual(coordinator_of('w', self.link(to_name='Idle'))['name'], 'its coordinator')

    def test_raw_session_id_is_not_a_name(self):
        self.assertEqual(coordinator_of('w', self.link())['name'], 'its coordinator')

    def test_task_title_is_shortened(self):
        c = coordinator_of('w', self.link(to_name='Coordinate Fable recovery | release-refactor-1.1', inferred=True))
        self.assertEqual(c['name'], 'Coordinate Fable recovery')
        self.assertTrue(c['inferred'])
        self.assertEqual(agent_name({'title': 'Idle', 'name': None, 'pane_id': 'w4:p4B'}), 'Agent w4:p4B')
        # agent_name keeps the full name (team links match on it); only short_name cuts.
        self.assertEqual(agent_name({'name': 'A | b'}), 'A | b')


if __name__ == '__main__':
    unittest.main()
