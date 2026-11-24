"""Watched coordinator questions stay visible and keep drafts during report updates."""
import copy
import unittest
from urllib.parse import quote

from herdr_attention.ui_contract import CONTRACT
from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER,run
from tests.test_typing_survives_refresh import reading


def fixture():
    data=page_fixture();root=next(a for a in data['view']['agents'] if a['name']=='web-lead')
    root['is_coordinator']=True;root['watch_closely']=True
    root['status_report']={'id':10,'status':'complete','finished_at':1700000000,'out_of_date':False,
        'summary':{'status':'waiting on you','members':[root['identity']],'done':['Built page'],'in_progress':[],'left':['Review'],'stuck':[]}}
    questions=[]
    for index in range(CONTRACT['watch_closely']['compact_question_limit']+1):
        question={'id':f'watched-{index}','identity':root['identity'],'source_identity':root['identity'],
            'text':f'Choose option {index}?','reason':'Needs your decision.','at':index,'terminal':False,'options':[],'keep_open':True}
        questions.append(question)
    data['view']['questions']=questions;data['root']=root['identity']
    data['view']['notify_tiers']['watch_closely']=[root['identity']]
    return data


@unittest.skipUnless(BROWSER,'no Chromium')
class WatchedQuestionsUi(unittest.TestCase):
    def test_overview_shows_every_watched_question_and_preserves_draft_caret(self):
        data=fixture()
        r=reading(run(data,'/list',390,844,script="""
          const block=document.querySelector('.questions-for-you'), count=block.querySelectorAll('.question-card').length;
          const input=block.querySelector('input');input.focus();input.value='draft answer';input.dispatchEvent(new Event('input'));input.setSelectionRange(2,7,'backward');
          const root=FIXTURE.view.agents.find(a=>a.identity===FIXTURE.root);
          root.status_report.refresh_status='queued';root.status_report.out_of_date=true;
          FIXTURE.view.version++;await refresh(true);FIXTURE.view.version++;await refresh(true);
          document.body.dataset.reading=JSON.stringify({count,same:document.contains(input),focused:document.activeElement===input,
            value:input.value,start:input.selectionStart,end:input.selectionEnd,direction:input.selectionDirection});
        """))
        self.assertEqual(r['count'],len(data['view']['questions']))
        self.assertTrue(r['same']);self.assertTrue(r['focused'])
        self.assertEqual((r['value'],r['start'],r['end'],r['direction']),('draft answer',2,7,'backward'))

    def test_own_page_keeps_question_without_any_report_and_removes_answered_question(self):
        data=fixture();root=next(a for a in data['view']['agents'] if a['identity']==data['root']);root['status_report']=None
        r=reading(run(data,'/a/'+quote(data['root'],safe=''),390,844,script="""
          const block=document.querySelector('.watched-questions');
          const count=block.querySelectorAll('.question-card').length;
          FIXTURE.view.questions=[];FIXTURE.view.version++;await refresh(true);
          document.body.dataset.reading=JSON.stringify({count,removed:!document.querySelector('.watched-questions')});
        """))
        self.assertEqual(r['count'],len(data['view']['questions']))
        self.assertTrue(r['removed'])

    def test_graph_questions_stay_expanded_until_no_longer_needed(self):
        r=reading(run(fixture(),'/',390,844,script="""
          const panel=document.querySelector('.graph-questions');
          const initially=panel.open;panel.open=false;await new Promise(r=>setTimeout(r,50));
          const reopened=panel.open;
          const input=panel.querySelector('input');input.focus();input.value='draft';input.dispatchEvent(new Event('input'));input.setSelectionRange(1,3);
          FIXTURE.view.version++;await refresh(true);
          const kept=panel.open && panel.contains(input) && document.activeElement===input && input.selectionStart===1;
          input.blur();FIXTURE.view.questions=[];FIXTURE.view.version++;await refresh(true);
          document.body.dataset.reading=JSON.stringify({initially,reopened,kept,hidden:panel.hidden});
        """))
        self.assertTrue(all(r.values()),r)

    def test_report_shows_updated_time_stale_and_refresh_progress_while_draft_stays(self):
        data=fixture()
        # Wide page: the Status screen (report) beside the main view with its Needs you block.
        r=reading(run(data,'/a/'+quote(data['root'],safe='')+'/report',1280,900,script="""
          const report=document.querySelector('.coordinator-report');
          const text=report.textContent;
          const input=document.querySelector('.watched-questions input');input.focus();input.value='half an answer';input.dispatchEvent(new Event('input'));input.setSelectionRange(1,5);
          const root=FIXTURE.view.agents.find(a=>a.identity===FIXTURE.root);
          root.status_report.out_of_date=true;root.status_report.refresh_status='running';FIXTURE.view.version++;await refresh(true);
          const stale=document.querySelector('.coordinator-report').textContent;
          root.status_report.out_of_date=false;delete root.status_report.refresh_status;root.status_report.finished_at=1700001000;FIXTURE.view.version++;await refresh(true);
          document.body.dataset.reading=JSON.stringify({updated:text.includes(UI.watch_closely.labels.updated),
            stale:stale.includes(UI.watch_closely.labels.out_of_date),updating:stale.includes(UI.watch_closely.labels.refreshing),
            same:document.contains(input) && document.activeElement===input && input.selectionStart===1,value:input.value,
            fresh:!document.querySelector('.coordinator-report').textContent.includes(UI.watch_closely.labels.out_of_date)});
        """))
        for key in ('updated','stale','updating','same','fresh'):self.assertTrue(r[key],key)
        self.assertEqual(r['value'],'half an answer')

    def test_watching_alone_does_not_add_red_attention(self):
        data=fixture();data['view']['questions']=[]
        root=next(a for a in data['view']['agents'] if a['identity']==data['root']);root.update(tier=None,state='working',question=None)
        r=reading(run(data,'/a/'+quote(data['root'],safe=''),390,844,script="""
          const root=agentById(FIXTURE.root);
          document.body.dataset.reading=JSON.stringify({tier:agentTier(root),red:document.querySelector('.status-card .avatar').classList.contains('s-needs_you')});
        """))
        self.assertIsNone(r['tier']);self.assertFalse(r['red'])
