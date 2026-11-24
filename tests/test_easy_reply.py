"""Explicit replies resolve to the coordinator and report actual delivery state."""
import unittest

from herdr_attention.collector import Collector
from herdr_attention.choice_reply import menu_keys
from herdr_attention.reply_routing import recipient
from tests.helpers import FakeHerdr, agent, settings, Clock
from tests.test_replies import FakePrompt
from tests.test_summaries import Rig


class ReplyRouting(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.herdr = FakeHerdr([agent('w1:p1', 'idle', session='lead', name='Lead'),
                                agent('w1:p2', 'idle', session='worker', name='Worker')])
        self.c = Collector(db_path=':memory:', client=self.herdr, settings=settings(), clock=self.clock)
        self.addCleanup(self.c.store.close)
        self.c.handle_snapshot()
        self.c.relation_action({'action':'link','subject':'claude:worker','kind':'reports_to','object':'claude:lead'},local=True)
        self.sender = FakePrompt(); self.c.replies._sender = self.sender

    def send(self, **extra):
        return self.c.submit_reply('claude:worker',text='Please use option B',quote='Which approach?',
            when='auto',interrupt=False,confirm=True,**extra)

    def test_worker_preview_and_send_use_coordinator_with_receipt(self):
        prev = self.c.preview_reply('claude:worker', 'yes')
        self.assertEqual(prev['recipient']['identity'], 'claude:lead')
        item = self.send(recipient_identity='claude:lead')
        self.assertEqual(item['identity'], 'claude:lead')
        self.assertEqual(item['status'], 'sent');self.assertEqual(item['sent_at'],self.clock())
        self.assertEqual(self.sender.calls, [('w1:p1','> Which approach?\n\nPlease use option B')])
        card = next(a for a in self.c.view(local=False)['agents'] if a['identity']=='claude:worker')
        self.assertEqual(card['last_reply']['status'],'sent')

    def test_fresh_busy_status_queues_then_delivers_without_prompting_worker(self):
        # Collector still thinks Lead is idle. The live read must prevent interruption.
        self.herdr.set(agent('w1:p1','working',session='lead',name='Lead'))
        item=self.send()
        self.assertEqual(item['status'],'queued');self.assertEqual(self.sender.calls,[])
        self.c.handle_snapshot();self.c.flush_replies();self.assertEqual(self.sender.calls,[])
        self.herdr.set(agent('w1:p1','idle',session='lead',name='Lead'))
        self.c.handle_snapshot();self.clock.advance(5);self.c.flush_replies()
        item=self.c.replies.get(item['id'])
        self.assertEqual(item['status'],'sent');self.assertEqual(item['sent_at'],self.clock())
        self.assertEqual(self.sender.calls[0][0],'w1:p1')

    def test_changed_recipient_or_session_refuses_send(self):
        with self.assertRaisesRegex(ValueError,'recipient changed'): self.send(recipient_identity='claude:worker')
        self.herdr.set(agent('w1:p1','idle',session='replacement',name='Other'))
        with self.assertRaisesRegex(ValueError,'recipient changed'): self.send()
        self.assertEqual(self.sender.calls,[])

    def test_hidden_missing_and_cyclic_coordinator_never_fall_back_to_worker(self):
        base={'agents':[{'identity':'worker','present':True,'pane_id':'p'}]}
        for links in ([{'kind':'reports_to','from':'worker','to':'hidden'}],
                      [{'kind':'reports_to','from':'worker','to':'worker','cycle':True}]):
            with self.subTest(links=links),self.assertRaises(ValueError):recipient(dict(base,links=links),'worker')

    def test_transport_failure_is_failed_not_sent(self):
        from herdr_attention.herdr import HerdrError
        self.sender.fail=HerdrError('unavailable')
        item=self.send();self.assertEqual(item['status'],'failed');self.assertIsNone(item['sent_at'])


class ExplicitChoice(unittest.TestCase):
    def setUp(self):
        self.rig=Rig();self.addCleanup(self.rig.tmp.cleanup);self.addCleanup(self.rig.collector.store.close)
        self.c=self.rig.collector;self.rig.block()
        self.q=next(q for q in self.c.view(local=False)['questions'] if q['identity']==self.rig.identity)
        self.keys=[];self.c.replies._key_sender=lambda target,keys:self.keys.append((target,keys))
        self.sender=FakePrompt();self.c.replies._sender=self.sender

    def answer(self, option='2. No', confirm=True):
        return self.c.submit_reply(self.rig.identity,text=option,quote=None,when='auto',interrupt=False,
            confirm=confirm,question_id=self.q['id'],option=option,recipient_identity=self.rig.identity)

    def test_exact_live_option_maps_to_keys_only_after_user_send(self):
        item=self.answer()
        self.assertEqual(item['status'],'sent');self.assertEqual(self.keys,[('w1:p1',['2'])])
        self.assertEqual(self.sender.calls,[])
        with self.assertRaisesRegex(ValueError,'no longer open'):self.answer()
        self.assertEqual(len(self.keys),1)

    def test_stale_screen_model_option_or_missing_confirmation_cannot_send(self):
        for option,confirm in [('3. Invented approval',True),('2. No',False)]:
            with self.subTest(option=option),self.assertRaises(ValueError):self.answer(option,confirm)
        self.rig.herdr.screens['w1:p1']='An unrelated prompt?\n❯ 1. Yes\n  2. No'
        with self.assertRaisesRegex(ValueError,'dialog changed'):self.answer()
        self.assertEqual(self.keys,[]);self.assertEqual(self.sender.calls,[])

    def test_ambiguous_menu_does_not_guess_keys(self):
        for screen in ('Do you want to proceed?\n1. Yes\n2. No',
                       'Do you want to proceed?\n❯ 1. Yes\n❯ 2. No'):
            with self.subTest(screen=screen),self.assertRaises(ValueError):menu_keys(self.q,'2. No',screen)

    def test_claude_uses_arrows_enter_and_codex_submits_only_number(self):
        screen=self.rig.herdr.screens['w1:p1']
        self.assertEqual(menu_keys(self.q,'2. No',screen,'claude'),['down','enter'])
        self.assertEqual(menu_keys(self.q,'2. No',screen,'codex'),['2'])

    def test_queued_codex_question_opens_then_rereads_before_number(self):
        hint='Question queued · shift+left to open'
        self.c.engine.set_last_assistant(self.rig.identity,'Should I proceed?\n1. Yes\n2. No',self.rig.clock())
        self.c.engine.set_blocked_rule(self.rig.identity,'bash_permission_prompt',hint)
        self.rig.herdr.screens['w1:p1']=hint
        self.q=next(q for q in self.c.view(local=False)['questions'] if q['identity']==self.rig.identity)
        def keys(target,sequence):
            self.keys.append((target,sequence))
            if sequence==['shift+left']:
                self.rig.herdr.screens[target]='Should I proceed?\n❯ 1. Yes\n  2. No'
        self.c.replies._key_sender=keys
        self.assertEqual(self.answer()['status'],'sent')
        self.assertEqual(self.keys,[('w1:p1',['shift+left']),('w1:p1',['2'])])
        self.assertEqual(self.sender.calls,[])

    def test_free_text_question_uses_literal_text_then_one_enter(self):
        prompt='Which branch should I use?'
        self.rig.herdr.screens['w1:p1']=prompt
        self.c.engine.set_blocked_rule(self.rig.identity,'bash_permission_prompt',prompt)
        self.q=next(q for q in self.c.view(local=False)['questions'] if q['identity']==self.rig.identity)
        texts=[];self.c.replies._text_sender=lambda pane,text:texts.append((pane,text))
        item=self.c.submit_reply(self.rig.identity,text='feature/easy-reply',quote=None,when='auto',interrupt=False,
            confirm=True,question_id=self.q['id'],terminal_text=True)
        self.assertEqual(item['status'],'sent')
        self.assertEqual(texts,[('w1:p1','feature/easy-reply')]);self.assertEqual(self.keys,[('w1:p1',['enter'])])
        self.assertEqual(self.sender.calls,[])

    def test_same_question_for_a_changed_command_refuses_stale_approval(self):
        self.rig.herdr.screens['w1:p1']='Run a different command\n'+self.rig.herdr.screens['w1:p1']
        with self.assertRaisesRegex(ValueError,'dialog changed'):self.answer()
        self.assertEqual(self.keys,[])

    def test_queued_opener_never_answers_an_unrelated_dialog(self):
        hint='Question queued · shift+left to open'
        self.c.engine.set_last_assistant(self.rig.identity,'Should I proceed?\n1. Yes\n2. No',self.rig.clock())
        self.c.engine.set_blocked_rule(self.rig.identity,'bash_permission_prompt',hint)
        self.rig.herdr.screens['w1:p1']=hint
        self.q=next(q for q in self.c.view(local=False)['questions'] if q['identity']==self.rig.identity)
        def keys(target,sequence):
            self.keys.append((target,sequence))
            self.rig.herdr.screens[target]='Which other command?\n❯ 1. Yes\n2. No'
        self.c.replies._key_sender=keys
        with self.assertRaisesRegex(ValueError,'dialog changed'):self.answer()
        self.assertEqual(self.keys,[('w1:p1',['shift+left'])])

    def test_cancelled_queued_answer_reopens_question(self):
        item=self.c.submit_reply(self.rig.identity,text='More context',quote=None,when='auto',interrupt=False,
            confirm=True,question_id=self.q['id'])
        self.assertEqual(item['status'],'queued')
        self.assertFalse(any(q['id']==self.q['id'] for q in self.c.view(local=False)['questions']))
        self.c.cancel_reply(item['id'],self.rig.identity)
        self.assertTrue(any(q['id']==self.q['id'] for q in self.c.view(local=False)['questions']))


if __name__=='__main__':unittest.main()
