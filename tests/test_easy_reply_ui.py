"""Real browser checks at phone width; only the API transport is stubbed."""
from pathlib import Path
import unittest
from urllib.parse import quote

from tests.test_typing_survives_refresh import reading
from tests.test_watch_questions_ui import fixture as watched_fixture
from tests.web_harness import BROWSER, run


def fixture():
    data=watched_fixture()
    root=next(a for a in data['view']['agents'] if a['identity']==data['root'])
    root['reply_target']={'identity':root['identity'],'name':root['name'],'workspace_label':root['workspace_label'],
                          'tab_label':root.get('tab_label'),'status':'working'}
    data['view']['questions']=data['view']['questions'][:1]
    data['view']['questions'][0].update(text='Which approach should I use?',options=['1. Keep the draft','2. Start again'])
    return data


TRANSPORT="""
const realFetch=window.fetch;window.sends=[];
window.fetch=async(path,init)=>{
  if(String(path)==='/api/replies' && init?.method==='POST') {
    const b=JSON.parse(init.body);window.sends.push(b);
    if(!b.confirm) throw new Error('Plain replies must not wait for a preview');
    const target=FIXTURE.view.agents.find(a=>a.identity===b.recipient_identity);
    const item={id:123,identity:target.identity,agent_name:target.name,recipient:{identity:target.identity,name:target.name},
      status:FIXTURE.delivery || 'queued',at:1700000000,sent_at:1700000000};
    // v0.9.6 contract: last_reply is {text, at, to}; the reply line reads only these.
    target.last_reply={text:b.text,at:Date.now()/1000,to:target.name};
    FIXTURE.view.questions=FIXTURE.view.questions.filter(q=>q.id!==b.question_id);
    return {ok:true,status:200,json:async()=>item};
  }
  return realFetch(path,init);
};
const wait=()=>new Promise(r=>setTimeout(r,80));
"""


@unittest.skipUnless(BROWSER,'no Chromium')
class EasyReplyUi(unittest.TestCase):
    def test_attention_row_opens_inline_focuses_and_routes_enter_to_coordinator(self):
        data=fixture()
        r=reading(run(data,'/list',390,844,script=TRANSPORT+"""
          const id=FIXTURE.ids.ui, row=document.querySelector(`[data-reply-anchor="attention:${id}"]`);
          const before=location.hash;row.querySelector('.reply-open').click();
          const box=row.querySelector('textarea'),target=replyRecipient(agentById(id));
          const inline=!!box,focused=document.activeElement===box,chip=row.querySelector('.recipient-chip').textContent;
          box.value='Use the smaller change';box.dispatchEvent(new Event('input'));
          const noReview=!row.textContent.includes('Confirm and send');
          box.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));await wait();
          document.body.dataset.reading=JSON.stringify({inline,focused,chip,noReview,hash:location.hash,before,
            sends:window.sends,target:target.identity,receipt:document.querySelector('#reply-deliveries').textContent,
            banner:document.querySelector('#banner').textContent});
        """))
        self.assertTrue(r['inline']);self.assertTrue(r['focused']);self.assertTrue(r['noReview'])
        self.assertEqual(r['hash'],r['before']);self.assertIn('To: web-lead',r['chip'])
        self.assertEqual(len(r['sends']),1);self.assertEqual(r['sends'][0]['recipient_identity'],r['target'])
        self.assertEqual(r['sends'][0]['when'],'auto');self.assertFalse(r['sends'][0]['interrupt'])
        self.assertIn('Queued, agent busy · To: web-lead',r['banner'])
        self.assertRegex(r['receipt'],r'You replied “Use the smaller change” at \d')

    def test_quick_option_is_one_tap_and_edit_other_send_nothing(self):
        data=fixture();data['delivery']='sent'
        r=reading(run(data,'/list',390,844,script=TRANSPORT+"""
          const card=document.querySelector('.question-card'),buttons=[...card.querySelectorAll('.quick-send')];
          const labels=buttons.map(b=>b.textContent);
          card.querySelector('.quick-edit').click();
          const edited=card.querySelector('textarea').value,editNoSend=window.sends.length===0;
          closeComposer();
          [...document.querySelectorAll('.question-card button')].find(b=>b.textContent===UI.easy_reply.labels.other).click();
          const context=document.querySelector('.easy-composer').textContent.includes('Which approach');closeComposer();
          document.querySelector('.question-card .quick-send').click();await wait();
          document.body.dataset.reading=JSON.stringify({labels,edited,editNoSend,context,sends:window.sends,
            receipt:document.querySelector('#reply-deliveries').textContent,banner:document.querySelector('#banner').textContent});
        """))
        self.assertTrue(r['editNoSend']);self.assertTrue(r['context'])
        self.assertEqual(r['edited'],'1. Keep the draft');self.assertIn('Yes',r['labels']);self.assertIn('No',r['labels'])
        self.assertEqual(len(r['sends']),1);self.assertEqual(r['sends'][0]['text'],'1. Keep the draft')
        self.assertEqual(r['banner'],'Sent to web-lead')
        self.assertRegex(r['receipt'],r'You replied “1. Keep the draft” at \d')

    def test_terminal_option_sends_mapping_request_and_queued_state_updates(self):
        data=fixture();data['view']['questions'][0]['terminal']=True
        r=reading(run(data,'/list',390,844,script=TRANSPORT+"""
          document.querySelector('.question-card .quick-send').click();await wait();
          FIXTURE.view.version++;await refresh(true);
          document.body.dataset.reading=JSON.stringify({option:window.sends[0].option,
            text:document.querySelector('#reply-deliveries').textContent});
        """))
        self.assertEqual(r['option'],'1. Keep the draft');self.assertRegex(r['text'],r'You replied “1. Keep the draft” at \d')

    def test_free_text_terminal_question_requests_literal_delivery_only_on_send(self):
        data=fixture();data['view']['questions'][0].update(terminal=True,options=[])
        r=reading(run(data,'/list',390,844,script=TRANSPORT+"""
          const q=FIXTURE.view.questions[0];draftQuestionReply(q,'Use the existing branch');
          const before=window.sends.length;document.querySelector('.easy-composer .reply-confirm').click();await wait();
          document.body.dataset.reading=JSON.stringify({before,sends:window.sends});
        """))
        self.assertEqual(r['before'],0);self.assertEqual(len(r['sends']),1)
        self.assertTrue(r['sends'][0]['terminal_text']);self.assertEqual(r['sends'][0]['text'],'Use the existing branch')

    def test_notification_rows_have_reply_without_opening_details(self):
        data=fixture()
        r=reading(run(data,'/n',390,844,script=TRANSPORT+"""
          await wait();const rows=[...document.querySelectorAll('.notif:not(.is-focused)')];
          const row=rows.find(row=>row.querySelector('.reply-open:not([disabled])'));
          const before=location.hash;row.querySelector('.reply-open').click();
          document.body.dataset.reading=JSON.stringify({buttons:rows.filter(r=>r.querySelector('.reply-open')).length,
            inline:!!row.querySelector('textarea'),same:before===location.hash,focused:document.activeElement===row.querySelector('textarea')});
        """))
        self.assertGreater(r['buttons'],0);self.assertTrue(r['inline']);self.assertTrue(r['same']);self.assertTrue(r['focused'])

    def test_draft_survives_navigation_and_forced_refresh_with_selection(self):
        data=fixture()
        r=reading(run(data,'/list',390,844,script=TRANSPORT+"""
          const q=FIXTURE.view.questions[0];draftQuestionReply(q,'A half-written reply');
          const box=document.querySelector('.easy-composer textarea');box.focus();box.setSelectionRange(2,7,'backward');
          FIXTURE.view.version++;await refresh(true);renderComposer();
          const kept=document.activeElement===box && box.selectionStart===2 && box.selectionEnd===7 && box.selectionDirection==='backward';
          location.hash=hashLink('notifications');await wait();
          location.hash=hashLink('list');await wait();
          document.querySelector('.question-card .reply-open').click();
          document.body.dataset.reading=JSON.stringify({kept,value:document.querySelector('.easy-composer textarea').value,sends:window.sends.length});
        """))
        self.assertTrue(r['kept']);self.assertEqual(r['value'],'A half-written reply');self.assertEqual(r['sends'],0)

    def test_phone_layout_has_large_controls_and_keyboard_clearance(self):
        data=fixture()
        r=reading(run(data,'/list',390,844,script="""
          draftQuestionReply(FIXTURE.view.questions[0],'Please use the smaller change.');
          const panel=document.querySelector('.easy-composer'),box=panel.querySelector('textarea'),send=panel.querySelector('.reply-confirm');
          box.blur();panel.scrollIntoView({block:'center'});
          const b=box.getBoundingClientRect(),s=send.getBoundingClientRect();
          const taps=[...document.querySelectorAll('.question-card button')].map(x=>x.getBoundingClientRect()).filter(r=>r.width>0);
          const before=location.hash;
          document.body.dataset.reading=JSON.stringify({width:innerWidth,overflow:document.documentElement.scrollWidth>innerWidth,
            height:b.height,font:parseFloat(getComputedStyle(box).fontSize),sendHeight:s.height,
            minTap:Math.min(...taps.map(r=>r.height)),sendVisible:s.top>=0 && s.bottom<=innerHeight,
            sticky:getComputedStyle(panel.querySelector('.reply-send-bar')).position,chip:panel.querySelector('.recipient-chip').textContent});
        """))
        self.assertFalse(r['overflow']);self.assertGreaterEqual(r['height'],120);self.assertGreaterEqual(r['font'],16)
        self.assertGreaterEqual(r['sendHeight'],44);self.assertGreaterEqual(r['minTap'],44)
        self.assertEqual(r['sticky'],'sticky');self.assertTrue(r['sendVisible']);self.assertIn('To: web-lead',r['chip'])


if __name__=='__main__':unittest.main()
