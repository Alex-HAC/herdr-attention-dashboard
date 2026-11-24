"""Watch closely schedules report reads, never agent prompts or attention changes."""
import unittest

from herdr_attention.ui_contract import CONTRACT
from herdr_attention.sanitize import fingerprint
from tests.test_summaries import Rig, reply


class WatchedReports(unittest.TestCase):
    def setUp(self):
        self.rig=Rig(runner=lambda cmd,prompt:reply({'done':['Built page'],'in_progress':['Review'],'left':['Ship']}))
        self.addCleanup(self.rig.tmp.cleanup);self.addCleanup(self.rig.collector.store.close)
        self.c=self.rig.collector;self.id=self.rig.identity
        self.c.relations.set_label(self.id,'role','coordinator',at=self.rig.clock(),provenance='manual',actor='fixture')
        self.c.settings.set_override('notify_tiers.watch_closely',[self.id])
        self.output('First output')

    def output(self,text):
        self.c.engine.set_last_assistant(self.id,text,self.rig.clock())
        self.c.engine.record_report_output(self.id,fingerprint(text))

    def settle(self):
        self.rig.clock.advance(CONTRACT['watch_closely']['report_debounce_seconds'])
        return self.rig.run_all()

    def report(self):return next(a for a in self.c.view(local=False)['agents'] if a['identity']==self.id)['status_report']

    def test_debounce_new_output_and_skip_unchanged_without_auto_summaries(self):
        self.assertFalse(self.c.summarizer.auto)
        self.c.queue_summaries();first=self.c.summarizer.latest(self.id,'report')
        self.assertIsNone(self.c.summarizer.next_job())
        self.rig.clock.advance(10);self.output('Second output');self.c.queue_summaries()
        latest=self.c.summarizer.latest(self.id,'report')
        self.assertEqual(first['id'],latest['id'])
        self.assertNotEqual(first['watch_revision'],latest['watch_revision'])
        self.rig.clock.advance(CONTRACT['watch_closely']['report_debounce_seconds']-1)
        self.assertIsNone(self.c.summarizer.next_job())
        self.rig.clock.advance(1);completed,=self.rig.run_all()
        self.assertEqual(completed['status'],'complete')
        self.assertEqual(len(self.rig.calls),1)
        for _ in range(3):self.c.queue_summaries();self.rig.clock.advance(10)
        self.assertIsNone(self.c.summarizer.next_job())
        self.assertEqual(len(self.rig.calls),1)
        self.assertEqual(self.c.store._exec("SELECT COUNT(*) FROM replies").fetchone()[0],0)
        self.assertFalse(any(call[0] in ('prompt','send_keys','focus','interrupt') for call in self.rig.herdr.calls))

    def test_keep_last_report_with_timestamp_and_outdated_while_refreshing(self):
        self.c.queue_summaries();self.settle()
        old=self.report();self.assertFalse(old['out_of_date'])
        self.rig.clock.advance(10);self.output('New output')
        stale=self.report();self.assertTrue(stale['out_of_date'])
        self.c.queue_summaries()
        pending=self.report()
        self.assertEqual(pending['summary'],old['summary'])
        self.assertEqual(pending['finished_at'],old['finished_at'])
        self.assertEqual(pending['refresh_status'],'queued')
        self.settle();fresh=self.report()
        self.assertFalse(fresh['out_of_date'])
        self.assertGreater(fresh['finished_at'],old['finished_at'])

    def test_output_during_a_run_gets_one_followup_and_stays_stale(self):
        self.c.queue_summaries()
        def runner(*args):
            self.output('Changed while report was running')
            self.c.queue_summaries()
            return reply({'done':['First report']})
        self.c.summarizer.runner=runner
        self.settle()
        self.assertEqual(self.c.store._exec("SELECT COUNT(*) FROM summaries WHERE mode='report'").fetchone()[0],1)
        self.assertTrue(self.report()['out_of_date'])
        self.c.summarizer.runner=lambda *args:reply({'done':['Followup']})
        self.c.queue_summaries();self.settle()
        self.assertFalse(self.report()['out_of_date'])
        self.assertEqual(self.c.store._exec("SELECT COUNT(*) FROM summaries WHERE mode='report'").fetchone()[0],2)

    def test_off_disabled_gone_collision_and_worker_do_not_schedule(self):
        self.c.settings.set_override('notify_tiers.watch_closely',[])
        self.c.queue_summaries();self.assertIsNone(self.c.summarizer.latest(self.id,'report'))
        self.c.settings.set_override('notify_tiers.watch_closely',[self.id]);self.c.queue_summaries()
        self.c.settings.set_override('summaries.enabled',False);self.c.queue_summaries()
        self.assertIsNone(self.c.summarizer.next_job())
        self.c.settings.set_override('summaries.enabled',True)
        self.c.engine.agents[self.id]['session_collision']=True;self.c.queue_summaries()
        self.assertIsNone(self.c.summarizer.next_job())
        self.c.engine.agents[self.id]['session_collision']=False
        self.c.engine.pane_gone('w1:p1','closed');self.c.queue_summaries()
        self.assertIsNone(self.c.summarizer.next_job())
        self.assertEqual(len(self.rig.calls),0)

    def test_worker_pin_is_not_a_coordinator_report_or_red_state(self):
        self.c.relations.clear_label(self.id,'role','coordinator',at=self.rig.clock(),provenance='manual',actor='fixture')
        before=self.c.engine.view()['agents'][0]
        self.c.queue_summaries()
        self.assertIsNone(self.c.summarizer.latest(self.id,'report'))
        after=self.c.engine.view()['agents'][0]
        self.assertEqual((before['state'],before['tier']),(after['state'],after['tier']))
        self.assertNotEqual(after['tier'],'waiting_on_you')

    def test_report_failure_keeps_old_text_and_does_not_retry_unchanged_output(self):
        self.c.queue_summaries();self.settle();old=self.report()
        self.output('New output');self.c.queue_summaries()
        self.c.summarizer.runner=lambda *args:reply({},error=True)
        self.settle();failed=self.report()
        self.assertEqual(failed['summary'],old['summary'])
        self.assertEqual(failed['refresh_status'],'failed')
        self.c.queue_summaries();self.assertIsNone(self.c.summarizer.next_job())
        self.assertTrue(failed['out_of_date'])

    def test_poll_reads_watched_output_at_contract_interval_without_state_change(self):
        self.rig.herdr.screens['w1:p1']='Visible result'
        before=self.c.engine.view()['agents'][0]['state']
        self.c.poll_output();first=self.c.engine.view()['agents'][0]['report_output_revision']
        self.rig.clock.advance(CONTRACT['watch_closely']['output_poll_seconds'])
        self.rig.herdr.screens['w1:p1']='Visible new result';self.c.poll_output()
        after=self.c.engine.view()['agents'][0]
        self.assertNotEqual(first,after['report_output_revision'])
        self.assertEqual(before,after['state'])

    def test_manual_report_is_not_delayed_and_rate_limit_still_applies(self):
        self.c.summarizer.request(self.id,None,'report');self.c.queue_summaries()
        self.assertIsNotNone(self.c.summarizer.next_job())
        self.rig.run_all()
        self.c.settings.set_override('summaries.max_per_hour',1)
        self.output('New output');self.c.queue_summaries();self.rig.clock.advance(30)
        self.assertIsNone(self.c.summarizer.next_job())

    def test_real_questions_stay_open_across_output_refresh_until_answered(self):
        self.rig.block()
        first=self.c.view(local=False)
        question=next(q for q in first['questions'] if q['identity']==self.id)
        self.assertTrue(question['keep_open'])
        before_tier=next(a for a in first['agents'] if a['identity']==self.id)['tier']
        self.output('New coordinator output while the same approval is pending')
        later=self.c.view(local=False)
        same=next(q for q in later['questions'] if q['identity']==self.id)
        self.assertEqual(question['id'],same['id'])
        self.assertTrue(same['keep_open'])
        self.c.settings.set_override('notify_tiers.watch_closely',[])
        unpinned=self.c.view(local=False)
        self.assertEqual(next(a for a in unpinned['agents'] if a['identity']==self.id)['tier'],before_tier)
        self.c.store._exec('INSERT INTO question_answers VALUES (?,?,?)',(question['id'],self.rig.clock(),None))
        self.assertFalse(any(q['identity']==self.id for q in self.c.view(local=False)['questions']))

    def test_unchanged_poll_and_restart_do_not_queue_another_report(self):
        from herdr_attention.summarize import Summarizer
        self.c.queue_summaries();self.settle()
        count=self.c.store._exec("SELECT COUNT(*) FROM summaries WHERE mode='report'").fetchone()[0]
        self.output('First output')
        self.c.summarizer=Summarizer(self.c.store,self.c.settings,self.rig.clock,runner=lambda *args:reply({'done':[]}))
        self.c.queue_summaries()
        self.assertEqual(self.c.store._exec("SELECT COUNT(*) FROM summaries WHERE mode='report'").fetchone()[0],count)
        self.assertIsNone(self.c.summarizer.next_job())
