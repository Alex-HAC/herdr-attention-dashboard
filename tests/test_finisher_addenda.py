import json
import unittest
from pathlib import Path
from tests.helpers import settings
from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER, run
from tests.test_typing_survives_refresh import reading
from herdr_attention.attention import classify_tier

class FalseNeedsYou(unittest.TestCase):
    def test_reported_1719_fable_wrapup_is_finished_even_when_pinned(self):
        agent=json.loads((Path(__file__).parent/'fixtures/attention/uiux-coord-fable-wrapup.json').read_text())
        for pins in ([],[agent['identity']]):
            tier=classify_tier(agent,settings=settings(notify_tiers={'watch_closely':pins}))
            self.assertEqual(tier.tier,'fyi_finished')
            self.assertFalse(tier.evidence['question'])

    @unittest.skipUnless(BROWSER,'no Chromium')
    def test_question_quote_reason_actions_and_header_destination(self):
        r=reading(run(page_fixture(),'/list',412,915,script="""
          const q=FIXTURE.view.questions[0];
          const header=document.querySelector('.status-header');
          const card=document.querySelector('.questions-for-you');
          const quote=card.textContent.includes(q.text),reason=card.textContent.includes(q.reason);
          header.click();await new Promise(r=>setTimeout(r,150));
          document.body.dataset.reading=JSON.stringify({quote,reason,
            opened:route().name==='questions',action:!!document.querySelector('.question-card .reply-open')});
        """))
        self.assertTrue(all(r.values()),r)

class QuotasDestination(unittest.TestCase):
    def test_sorting_reset_wording_and_stale_marking_share_one_snapshot(self):
        from herdr_attention.quotas import enrich
        from tests.test_quotas import snapshot
        q=enrich(snapshot(),[])
        # Fixed panel order; urgency is a flag, never a reorder.
        self.assertEqual(q['account_ids'][:2],['claude:main','claude:personal'])
        cursor=next(a for a in q['accounts'] if a['id']=='cursor')
        # v0.9.8.8: only "Named models Monthly" is at 100 %; that limit is for some models only (Auto
        # still runs), so the account is nearly full at its own 93 %, with the named-model line full.
        self.assertFalse(cursor['out_of_quota'])
        self.assertEqual(cursor['flag']['id'],'nearly_full')
        self.assertEqual([(w['scope'],w['pct']) for w in cursor['scoped'] if w['pct']>=100],[('Named models',100)])
        main=next(a for a in q['accounts'] if a['id']=='claude:main')
        self.assertIn('resets Wed 05:59, in ',main['reset_text'])
        self.assertTrue(main['stale'])
        # An active account at the wall beats the pin; the pin shows when nothing is hot.
        pinned=enrich(snapshot(),[],['claude:personal'])
        self.assertIn('Cursor',pinned['summary']['label'])
        # (v0.9.8.8: Cursor is hot at its own 93 %, "high"; the full named-model limit is a line.)
        self.assertEqual((pinned['summary']['level'],pinned['header']['urgent']),('high',True))
        calm=snapshot()
        q=enrich(calm,[],['claude:personal'])
        for a in q['accounts']: a['worst_pct']=min(a['worst_pct'],50)
        from herdr_attention.quotas import summary
        self.assertIn('Personal',summary(q['accounts'],['claude:personal'])['label'])

    @unittest.skipUnless(BROWSER,'no Chromium')
    def test_quotas_are_one_tap_away_and_never_refresh_on_load(self):
        r=reading(run(page_fixture(),'/quotas',412,915,script="""
          document.body.dataset.reading=JSON.stringify({
            selected:document.querySelector('[data-tab="quotas"]').getAttribute('aria-current'),
            rows:document.querySelectorAll('.quota-gauge').length,
            resets:[...document.querySelectorAll('.quota-gline')].some(e=>e.textContent.startsWith('resets ')),
            posts:window.__calls.filter(c=>c[1]==='POST').length});
        """))
        self.assertEqual(r['selected'],'page')
        self.assertGreater(r['rows'],3)
        self.assertTrue(r['resets'])
        self.assertEqual(r['posts'],0)

    def test_refresh_calls_collectors_directly_and_never_the_autoswitch_wrapper(self):
        import tempfile
        from unittest.mock import patch
        from types import SimpleNamespace
        from herdr_attention.quotas import refresh_records
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'codex.json';path.write_text('{"id":"codex"}')
            with patch('subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=b'{"id":"codex","limits":[]}')) as call:
                result=refresh_records(folder)
            self.assertTrue(result['ok'])
            args=call.call_args.args[0]
            self.assertEqual(Path(args[0]).name,'omarchy-agent-usage-codex')
            self.assertIn('--limits-only',args)
            self.assertNotIn('autoswitch',str(args))


class NotificationText(unittest.TestCase):
    def test_sent_phone_payload_is_exactly_the_api_text(self):
        from tests.test_notify import Rig
        from herdr_attention.notifications import build_items
        from herdr_attention.notify_gate import NotifyGate
        rig=Rig(presence={'mode':'always'},notify_gate={'enabled':True})
        self.addCleanup(rig.store.close)
        rig.notifier.gate=NotifyGate(rig.store,rig.settings,rig.clock)
        rig.finish('w1:p1','a');rig.notifier.tick()
        gate=rig.notifier.gate;job=gate.next_job()
        gate._finish(job,'send','Useful progress',title='Cart is ready',body='Review the result.\nOpen: https://example.invalid/#/n/1')
        rig.store.set_notify_state([e['id'] for e in rig.store.recent_events(5)],'pending')
        rig.notifier.tick()
        event=rig.store.recent_events(1)[0]
        agents={a['identity']:a for a in rig.engine.view()['agents']}
        item,=build_items([event],agents=agents,links=[],settings=rig.settings,now=rig.clock(),texts={event['id']:rig.store.notification_text(event['id'])})
        self.assertEqual(item['what'],rig.sender.sent[0]['title'])
        self.assertEqual(item['body'],rig.sender.sent[0]['message'])

    def test_repeats_are_grouped_once_and_action_decision_is_from_tier_table(self):
        from herdr_attention.notifications import group_items
        from herdr_attention.notify_tiers import actionable
        notes=[dict(id=i,at=i,agent={'identity':'one'}) for i in range(4)]
        grouped=group_items(notes)
        self.assertEqual(sum(n['is_group_latest'] for n in grouped),1)
        self.assertEqual(grouped[-1]['group_count'],4)
        self.assertTrue(actionable('waiting_on_you'))
        self.assertFalse(actionable('waiting_on_coordinator'))


class CurrentActions(unittest.TestCase):
    def test_old_decision_is_outdated_after_wrapup_and_has_no_pending_action(self):
        from herdr_attention.content import presentation
        agent={'identity':'a','kind':'completion','status':'done','event_id':2,'question':None,'last_activity_at':300}
        old={'status':'complete','event_id':1,'finished_at':200,'summary':{'needs_you':{'kind':'decision','ask':'Pick five decisions'}}}
        action=presentation(agent,old)
        self.assertTrue(action['out_of_date'])
        self.assertEqual(action['kind'],'none')
        self.assertIn('Nothing to do',action['text'])
        agent['acked']=True
        self.assertEqual(presentation(agent,old)['text'],'Already handled')

class QuotaStopEpisodes(unittest.TestCase):
    def test_detect_group_ack_restart_reset_and_resume(self):
        from herdr_attention.store import Store
        from herdr_attention.quota_stops import QuotaStops
        from tests.helpers import Clock
        from datetime import datetime,timezone
        store=Store(':memory:');self.addCleanup(store.close);clock=Clock(100)
        stops=QuotaStops(store,clock)
        account={'id':'codex-personal:personal','provider':'codex-personal','provider_name':'Codex','label':'Personal',
                 'windows':[{'used_pct':100,'resets_at':datetime.fromtimestamp(200,timezone.utc).isoformat()}]}
        agents=[{'identity':f'codex:{i}','name':f'worker-{i}','status':'done','kind':'completion','workspace_id':'w1',
                 'pane_id':f'w1:p{i}','present':True,'launch':{'account':'codex-personal'},'state':'done'} for i in (1,2)]
        def view():
            return {'agents':[dict(a) for a in agents],'workspaces':[{'id':'w1','counts':{}}],'counts':{},'questions':[]}
        for a in agents:
            stops.observe(a,'Usage limit reached',[account])
            stops.observe(a,'Usage limit reached',[account])
        self.assertEqual(len(store.recent_events(10)),2)
        # v0.9.8.10: quota cards are dashboard-only (they never push the phone).
        self.assertEqual(sum(e['notify_state']=='dashboard' for e in store.recent_events(10)),2)
        self.assertEqual(sum(e['notify_state']=='pending' for e in store.recent_events(10)),0)
        data=view();stops.decorate(data)
        self.assertEqual(len(data['quota_stops']['groups']),1)
        self.assertEqual(len(data['quota_stops']['groups'][0]['agents']),2)
        self.assertTrue(all(a['state']=='out_of_quota' and a['tier']=='might_look' for a in data['agents']))
        for a in agents: stops.ack(a['identity'])
        restarted=QuotaStops(store,clock);data=view();restarted.decorate(data)
        self.assertEqual(data['quota_stops']['groups'],[])
        clock.advance(101);restarted.observe(agents[0],'Usage limit reached',[account])
        data=view();restarted.decorate(data)
        self.assertEqual(len(data['quota_stops']['groups'][0]['agents']),1)
        restarted.observe(agents[0],'Usage limit reached',[account])
        self.assertEqual(len(store.recent_events(10)),3)
        agents[0].update(status='working',last_activity_at=clock()+1)
        restarted.observe(agents[0],'Running tests',[account]);data=view();restarted.decorate(data)
        self.assertEqual(data['quota_stops']['groups'],[])

class QuestionsRouting(unittest.TestCase):
    def fixture(self):
        root={'identity':'root','name':'coordinator','question':None,'status':'idle','state':'waiting','tier':None}
        worker={'identity':'worker','name':'builder','tier':'waiting_on_coordinator','reports_to':{'identity':'root'},
                'tier_evidence':{'question':'Should I merge the PR?','options':['1. Yes','2. No']},'event_at':100,'question':None}
        return {'agents':[worker,root]}

    def test_worker_default_hidden_coordinator_relay_once_and_timeout_routes_to_coordinator(self):
        from herdr_attention.questions import build_questions
        view=self.fixture()
        self.assertEqual(build_questions(view,settings(),1000),[])
        timed=build_questions(self.fixture(),settings(attention={'coordinator_wait_minutes':5}),1000)
        self.assertEqual(timed[0]['identity'],'root')
        self.assertEqual(timed[0]['about'],'worker')
        view=self.fixture();view['agents'][1]['question']={'id':'q','identity':'root','text':'Should I merge the PR?', 'reason':'A direct ask','at':200,'terminal':False,'options':['1. Yes','2. No']}
        rows=build_questions(view,settings(attention={'coordinator_wait_minutes':5}),1000)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['id'],'q')
        self.assertEqual(rows[0]['about'],'worker')
        self.assertEqual(build_questions(view,settings(),1000,answered={'q'}),[])

    @unittest.skipUnless(BROWSER,'no Chromium')
    def test_inline_question_draft_survives_poll_and_opens_composer_for_target(self):
        data=page_fixture();q=data['view']['questions'][0];q['terminal']=False;q['options']=[]
        for a in data['view']['agents']:
            if a['identity']==q['identity']:a['status']='idle';a['question']=q
        r=reading(run(data,'/questions',412,915,script="""
          const field=document.querySelector('.question-reply input');field.focus();field.value='Use the safe option';field.dispatchEvent(new Event('input'));field.setSelectionRange(2,6);
          FIXTURE.view.version++;await refresh();
          const kept=document.activeElement===field&&field.selectionStart===2&&field.selectionEnd===6;
          document.querySelector('.question-card .reply-open').click();
          document.body.dataset.reading=JSON.stringify({kept,target:app.composer.identity,expected:FIXTURE.view.questions[0].identity,
            text:app.composer.text,question:app.composer.questionId,composer:!!document.querySelector('.easy-composer .reply-confirm'),sends:window.__calls.filter(c=>c[0]==='/api/replies'&&c[1]==='POST').length});
        """))
        self.assertTrue(r['kept']);self.assertTrue(r['composer']);self.assertEqual(r['sends'],0)
        self.assertEqual(r['target'],r['expected']);self.assertEqual(r['text'],'Use the safe option')
        self.assertTrue(r['question'])

    def test_confirmed_timeout_reply_goes_to_coordinator_and_retires_question(self):
        from herdr_attention.collector import Collector
        from tests.helpers import FakeHerdr,Clock,agent
        clock=Clock(100)
        herdr=FakeHerdr([agent('w1:p1','done',session='root',name='coordinator'),agent('w1:p2','idle',session='worker',name='builder')],
                        [{'workspace_id':'w1','label':'Work'}])
        c=Collector(db_path=':memory:',client=herdr,clock=clock,settings=settings(attention={'coordinator_wait_minutes':1}))
        self.addCleanup(c.store.close);c.handle_snapshot()
        c.relations.link('claude:worker','reports_to','claude:root',at=clock(),provenance='manual',actor='test')
        c.engine.set_last_assistant('claude:worker','Should I merge the PR?',clock(),'assistant');clock.advance(120)
        question=c.view(local=False)['questions'][0]
        self.assertEqual(question['identity'],'claude:root')
        sent=[];c.replies._sender=lambda target,text:sent.append((target,text)) or {}
        kwargs=dict(text='Merge the PR',quote=None,when='now',interrupt=False,question_id=question['id'])
        with self.assertRaises(ValueError):c.submit_reply('claude:root',confirm=False,**kwargs)
        self.assertEqual(sent,[])
        c.submit_reply('claude:root',confirm=True,**kwargs)
        self.assertEqual(sent[0][0],'w1:p1')
        self.assertEqual(c.view(local=False)['questions'],[])

class CoordinatorReports(unittest.TestCase):
    def test_assembly_caches_with_summary_settings_without_prompting_agents(self):
        from tests.test_summaries import Rig, reply
        from herdr_attention.team_reports import facts, prepare, verify_report
        rig=Rig(runner=lambda command,prompt:reply({'done':['Built the page'],'in_progress':['Review'],'left':['Ship']}))
        self.addCleanup(rig.tmp.cleanup);self.addCleanup(rig.collector.store.close)
        c=rig.collector
        view={'agents':[{'identity':'root','name':'lead','workspace_id':'w1','last_activity_at':1},
                        {'identity':'worker','name':'builder','workspace_id':'w1','tier':'waiting_on_coordinator','reason':'Needs review','last_activity_at':2},
                        {'identity':'hidden','workspace_id':'w2'}],
              'links':[{'from':'worker','to':'root','kind':'reports_to'}],'questions':[], 'workspaces':[{'id':'w1'}]}
        prepared=prepare(view,'root',lambda i:{'text':f'{i} report','info':{}},lambda p:'',[],10)
        self.assertIn('worker report',prepared['text']);self.assertNotIn('hidden report',prepared['text'])
        self.assertEqual(prepared['report']['status'],'stuck')
        checked=verify_report({'status':'invented','questions':['invented'],'done':['a']*8},prepared['report'])
        self.assertEqual(checked['status'],'stuck');self.assertEqual(checked['questions'],[]);self.assertEqual(len(checked['done']),3)
        before=facts(view,'root')['revision'];view['agents'][1]['last_activity_at']=3
        self.assertNotEqual(before,facts(view,'root')['revision'])
        before=facts(view,'root')['revision'];view['questions']=[{'id':'new','identity':'root','text':'Which option?'}]
        self.assertNotEqual(before,facts(view,'root')['revision'])
        self.assertEqual(facts(view,'root')['status'],'waiting on you')
        job=c.request_summary(rig.identity,'report');rig.run_all()
        row=c.summarizer.latest(rig.identity,'report')
        self.assertEqual(row['status'],'complete',row)
        self.assertEqual(row['summary']['done'],['Built the page'])
        self.assertEqual(len(rig.calls),1)
        self.assertEqual(c.store._exec('SELECT count(*) FROM replies').fetchone()[0],0)
        self.assertIsNone(c.summarizer.latest(rig.identity))

    @unittest.skipUnless(BROWSER,'no Chromium')
    def test_status_and_steering_only_draft_until_confirm(self):
        f=page_fixture();a=f['view']['agents'][0];a['is_coordinator']=True
        a['status_report']={'status':'complete','finished_at':100,'out_of_date':True,'summary':{'status':'on track','members':[a['identity']],'done':['Built page'],'in_progress':[],'left':[],'stuck':[]}}
        r=reading(run(f,'/a/'+a['identity']+'/report',412,915,script="""
          const report=document.querySelector('.coordinator-report');
          const before=window.__calls.filter(c=>c[1]==='POST').length;
          [...report.querySelectorAll('button')].find(b=>b.textContent==='Steer').click();
          const draft=app.composer.text,identity=app.composer.identity;
          document.body.dataset.reading=JSON.stringify({status:report.textContent.includes('Out of date'),draft,identity,
            sent:window.__calls.filter(c=>c[1]==='POST'&&c[0].includes('/send')).length,before});
        """))
        self.assertTrue(r['status']);self.assertIn('focus the team',r['draft']);self.assertEqual(r['identity'],a['identity']);self.assertEqual(r['sent'],0);self.assertEqual(r['before'],0)
