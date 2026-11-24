"""v0.9.3: one final tier per event, decided once, used by the phone, Notifications and the agent tier."""
import unittest

from herdr_attention import notify_tiers as nt
from herdr_attention.attention import awaiting_user, classify_tier
from herdr_attention.config import Settings
from herdr_attention.content import decorate_subagents
from herdr_attention.engine import Engine
from herdr_attention.notifications import build_items
from herdr_attention.notify import Notifier
from herdr_attention.presence import Presence
from herdr_attention.questions import build_questions
from herdr_attention.store import Store
from tests.helpers import Clock, RecordingSender, agent, settings, workspaces

ASK = {'decision': 'send', 'tier': 'waiting_on_you', 'title': 'Approve the 1.2.7 release for tomorrow'}
CONF = Settings({})


def link(registered=False):
    return [{'kind': 'reports_to', 'from': 'w', 'to': 'codex:lead', 'to_name': 'lead', 'inferred': not registered}]


class Helper(unittest.TestCase):
    def event(self, **evidence):
        return {'id': 1, 'type': 'completion', 'identity': 'w', 'evidence': dict({'notify_tier': 'waiting_on_coordinator'}, **evidence)}

    def test_old_event_without_field_computes_with_the_gate(self):
        self.assertEqual(nt.final_tier(self.event(), ASK, link(), settings=CONF), 'waiting_on_you')
        self.assertEqual(nt.final_tier(self.event(), None, link(), settings=CONF), 'waiting_on_coordinator')

    def test_stored_tier_wins_over_changed_links(self):
        event = self.event(final_tier='waiting_on_you')
        # The link is now registered; a fresh decision would hand it to the coordinator.
        self.assertEqual(nt.final_tier(self.event(), ASK, link(registered=True), settings=CONF), 'waiting_on_coordinator')
        self.assertEqual(nt.final_tier(event, ASK, link(registered=True), settings=CONF), 'waiting_on_you')
        self.assertEqual(nt.final_tier(event, None, [], settings=CONF), 'waiting_on_you')

    def test_notice_tier_only_for_open_unread_sent_events(self):
        base = dict(self.event(final_tier='waiting_on_you'), notify_state='sent', acked_at=None, resolved_at=None)
        self.assertEqual(nt.notice_tier(base), 'waiting_on_you')
        self.assertEqual(nt.notice_tier(dict(base, notify_state='dashboard')), 'waiting_on_you')
        self.assertIsNone(nt.notice_tier(dict(base, acked_at=5.0)))
        self.assertIsNone(nt.notice_tier(dict(base, resolved_at=5.0)))
        for state in ('pending', 'held', 'gated_skip', 'off'):
            self.assertIsNone(nt.notice_tier(dict(base, notify_state=state)))
        self.assertIsNone(nt.notice_tier(dict(self.event(), notify_state='sent', acked_at=None, resolved_at=None)))
        self.assertIsNone(nt.notice_tier(None))

    def test_closed_row_label_does_not_claim_it_waits(self):
        self.assertEqual(nt.tier_label('waiting_on_you'), 'Waiting on you')
        self.assertEqual(nt.tier_label('waiting_on_you', is_open=False), 'Was waiting on you')
        self.assertEqual(nt.tier_label('fyi_finished', is_open=False), nt.TIER_LABELS['fyi_finished'])


class AgentTier(unittest.TestCase):
    AGENT = {'identity': 'w', 'kind': 'completion', 'status': 'done', 'present': True}

    def test_lift_has_no_quote_and_is_not_a_question(self):
        found = classify_tier(dict(self.AGENT), settings=CONF, links=link(), notice_tier='waiting_on_you')
        self.assertEqual(found.tier, 'waiting_on_you')
        self.assertEqual(found.evidence['question'], '')
        self.assertTrue(found.evidence['notice'])
        self.assertEqual(found.evidence['rule_tier'], 'waiting_on_coordinator')
        self.assertFalse(awaiting_user(found))
        # Without any rule tier (no evidence to copy) it still builds.
        found = classify_tier({'identity': 'x', 'kind': 'idle', 'status': 'idle', 'present': True},
                              settings=CONF, links=[], notice_tier='waiting_on_you')
        self.assertEqual((found.tier, found.snippet), ('waiting_on_you', ''))
        self.assertFalse(awaiting_user(found))

    def test_only_waiting_on_you_lifts(self):
        for tier in ('might_look', 'fyi_finished', 'waiting_on_coordinator', 'coordinator_milestone', None):
            found = classify_tier(dict(self.AGENT), settings=CONF, links=link(), notice_tier=tier)
            self.assertEqual(found.tier, 'waiting_on_coordinator', tier)

    def test_no_lift_when_read_gone_or_working(self):
        for extra in ({'acked': True}, {'present': False}, {'status': 'working', 'kind': 'working'}):
            found = classify_tier(dict(self.AGENT, **extra), settings=CONF, links=link(), notice_tier='waiting_on_you')
            self.assertNotEqual(getattr(found, 'tier', None), 'waiting_on_you', extra)

    def test_a_real_question_keeps_its_question(self):
        agent = dict(self.AGENT, status='idle', kind='idle',
                     last_assistant={'text': 'Should I merge the release branch now?', 'last_role': 'assistant'})
        found = classify_tier(agent, settings=CONF, links=[], notice_tier='waiting_on_you')
        self.assertTrue(awaiting_user(found))
        self.assertIn('merge the release branch', found.snippet)


class FakeGate:
    """The AI gate said: Alex must act. Disabled, so the plain text is sent."""
    enabled = False

    def decisions_by_event(self, ids, scan=500):
        return {i: dict(ASK, event_ids=list(ids)) for i in ids}

    def status(self):
        return {'enabled': False, 'recent': []}


class Rig:
    def __init__(self):
        self.clock = Clock()
        self.settings = settings(presence={'mode': 'always'}, notify_gate={'enabled': False})
        self.store = Store(':memory:')
        self.engine = Engine(self.settings, self.store, self.clock)
        self.links = []
        self.sender = RecordingSender()
        self.notifier = Notifier(self.settings, self.store, Presence(self.settings, self.clock), self.sender,
                                 lambda: {}, self.clock, gate=FakeGate(),
                                 agent_of=self.engine.agent_by_identity, links_of=lambda: self.links)
        self.engine.apply_snapshot([agent('w1:p1', 'working', session='a', name='alpha')], workspaces('w1'))
        self.identity = self.engine.view()['agents'][0]['identity']

    def finish(self):
        self.engine.apply_agent(agent('w1:p1', 'done', session='a', name='alpha'))

    def work(self):
        self.engine.apply_agent(agent('w1:p1', 'working', session='a', name='alpha'))

    def view(self):
        view = self.engine.view()
        decorate_subagents(view)
        return view

    def items(self, view):
        events = self.store.notification_events(nt.NOTICE_STATES, ['w1'])
        gates = FakeGate().decisions_by_event([e['id'] for e in events])
        return build_items(events, agents={a['identity']: a for a in view['agents']}, links=self.links,
                           gates=gates, settings=self.settings, now=self.clock())

    def me(self, view):
        return next(a for a in view['agents'] if a['identity'] == self.identity)


class Wiring(unittest.TestCase):
    def sent(self):
        rig = Rig()
        rig.finish()
        rig.notifier.tick()
        self.assertEqual(len(rig.sender.sent), 1)
        return rig

    def test_send_stores_the_final_tier_once(self):
        rig = self.sent()
        event = rig.store.recent_events(1)[0]
        self.assertEqual(event['evidence']['final_tier'], 'waiting_on_you')
        self.assertFalse(rig.store.set_final_tier(event['id'], 'fyi_finished'))
        self.assertEqual(rig.store.event(event['id'])['evidence']['final_tier'], 'waiting_on_you')

    def test_header_list_and_notifications_agree(self):
        rig = self.sent()
        view = rig.view()
        me = rig.me(view)
        self.assertEqual((me['tier'], view['counts']['waiting_on_you'], view['counts']['needs_you']), ('waiting_on_you', 1, 1))
        self.assertFalse(me['awaiting_user'])
        self.assertIsNone(me['question'])
        self.assertEqual(me['presentation']['kind'], 'open_agent')
        self.assertTrue(me['read_action']['available'])
        self.assertEqual(sum(w['counts']['waiting_on_you'] for w in view['workspaces']), 1)
        [item] = rig.items(view)
        self.assertEqual((item['tier'], item['tier_label'], item['actionable']), ('waiting_on_you', 'Waiting on you', True))
        self.assertEqual(build_questions(view, rig.settings, rig.clock()), [])

    def test_stored_tier_survives_a_link_change(self):
        rig = self.sent()
        # A registered coordinator appears after the phone said "waiting on you".
        rig.links = [{'kind': 'reports_to', 'from': rig.identity, 'to': 'codex:lead', 'to_name': 'lead'}]
        event = rig.store.recent_events(1)[0]
        self.assertEqual(rig.notifier._tier_of(event), 'waiting_on_you')
        [item] = rig.items(rig.view())
        self.assertEqual(item['tier'], 'waiting_on_you')
        self.assertEqual(rig.me(rig.view())['tier'], 'waiting_on_you')

    def test_read_clears_it_and_it_stays_cleared(self):
        rig = self.sent()
        event_id = rig.me(rig.view())['event_id']
        self.assertTrue(rig.engine.ack(event_id))
        view = rig.view()
        self.assertNotEqual(rig.me(view)['tier'], 'waiting_on_you')
        self.assertEqual(view['counts']['waiting_on_you'], 0)
        [item] = rig.items(view)
        self.assertEqual((item['read'], item['actionable']), (True, False))
        before = len(rig.store.recent_events(50))
        # Same state again, a re-evaluation and a notifier round: nothing comes back.
        rig.finish()
        rig.engine.settings_changed()
        rig.notifier.tick()
        self.assertEqual(len(rig.store.recent_events(50)), before)
        self.assertEqual(len(rig.sender.sent), 1)
        self.assertNotEqual(rig.me(rig.view())['tier'], 'waiting_on_you')
        # New agent activity is a new episode, with a new event.
        rig.work()
        rig.finish()
        self.assertGreater(rig.store.recent_events(1)[0]['id'], event_id)

    def test_episode_end_closes_the_row_everywhere(self):
        rig = self.sent()
        rig.work()
        view = rig.view()
        self.assertNotEqual(rig.me(view)['tier'], 'waiting_on_you')
        [item] = rig.items(view)
        self.assertEqual((item['actionable'], item['tier_label'], item['read']), (False, 'Was waiting on you', False))

    def test_old_open_notice_gets_the_field_once(self):
        rig = Rig()
        rig.finish()
        event = rig.store.recent_events(1)[0]
        rig.store.set_notify_state([event['id']], 'sent', at=rig.clock())
        self.assertNotIn('final_tier', rig.store.event(event['id'])['evidence'])
        self.assertNotEqual(rig.me(rig.view())['tier'], 'waiting_on_you')
        rig.notifier.tick()
        self.assertEqual(rig.store.event(event['id'])['evidence']['final_tier'], 'waiting_on_you')
        self.assertEqual(rig.me(rig.view())['tier'], 'waiting_on_you')
        self.assertEqual(rig.sender.sent, [])

    def test_registered_coordinator_worker_stays_with_the_coordinator(self):
        rig = Rig()
        rig.links = [{'kind': 'reports_to', 'from': rig.identity, 'to': 'codex:lead', 'to_name': 'lead'}]
        rig.finish()
        event = rig.store.recent_events(1)[0]
        self.assertEqual(rig.notifier._tier_of(event), 'waiting_on_coordinator')
        rig.notifier.tick()   # Waiting on a coordinator is off in Normal: not sent, not lifted.
        self.assertEqual(rig.sender.sent, [])
        self.assertNotIn('final_tier', rig.store.event(event['id'])['evidence'])
        self.assertNotEqual(rig.me(rig.view())['tier'], 'waiting_on_you')


if __name__ == '__main__':
    unittest.main()
