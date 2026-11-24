"""Notifications view (#/n, #/n/<id>), v0.8.2 slice B. FIXTURE-BASED, headless Chromium."""

import copy
import json
import unittest

from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER, run

PROBE = """
await new Promise(res => setTimeout(res, 500));
const cards = [...document.querySelectorAll('article.notif')];
const out = {hash: location.hash, ids: cards.map(c => c.id),
  focused: cards.filter(c => c.classList.contains('is-focused')).map(c => c.id),
  unreadCards: cards.filter(c => c.classList.contains('is-unread')).length,
  heads: [...document.querySelectorAll('.notif-group .attention-tier')].map(e => e.textContent),
  text: (document.querySelector('main') || document.body).innerText,
  badge: document.getElementById('notif-badge').textContent, badgeHidden: document.getElementById('notif-badge').hidden,
  urgent: document.getElementById('notif-badge').classList.contains('urgent'),
  links: [...document.querySelectorAll('.notif.is-focused a')].map(a => a.getAttribute('href')),
  buttons: [...document.querySelectorAll('.notif.is-focused button')].map(b => b.textContent)};
STEP
document.body.dataset.result = JSON.stringify(out);
"""


def result(page):
    raw = page["dom"].split('data-result="', 1)[1].split('"', 1)[0]
    return json.loads(raw.replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">"))


@unittest.skipUnless(BROWSER, "no Chromium found")
class NotificationsView(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = page_fixture()
        cls.urgent_id = next(n["id"] for n in cls.data["notifications"] if n["tier"] == "waiting_on_you")

    def probe(self, route, step="", data=None):
        return result(run(copy.deepcopy(data or self.data), route, 390, 844, script=PROBE.replace("STEP", step)))

    def test_focus_by_id_expands_one_and_groups_by_tier(self):
        r = self.probe(f"/n/{self.urgent_id}")
        self.assertEqual(r["focused"], [f"n-{self.urgent_id}"])
        self.assertEqual(len(r["ids"]), len(self.data["notifications"]))
        self.assertEqual(r["heads"][0].split(" · ")[0], "Needs action")
        self.assertIn("Why it matters", r["text"])
        self.assertIn("What to do", r["text"])
        ident = next(n["agent"]["identity"] for n in self.data["notifications"] if n["id"] == self.urgent_id)
        for suffix in ("", "/conversation", "/output"):
            self.assertIn(f"#/a/{ident.replace(':', '%3A')}{suffix}", r["links"])
        self.assertIn("Read", r["buttons"])
        self.assertIn("Reply", r["buttons"], "reply is available without opening another page")
        self.assertIn("Reply", r["text"])
        self.assertTrue(any("Watch" in b for b in r["buttons"]))
        # Opening a focused item does not mark anything read.
        self.assertEqual(r["unreadCards"], len(self.data["notifications"]))
        self.assertEqual(r["badge"], str(len(self.data["notifications"])))
        self.assertTrue(r["urgent"])

    def test_unknown_id_falls_back_to_the_list(self):
        r = self.probe("/n/99999")
        self.assertIn("That notification is no longer available.", r["text"])
        self.assertEqual(r["focused"], [])
        self.assertEqual(len(r["ids"]), len(self.data["notifications"]))

    def test_legacy_event_link_redirects(self):
        r = result(run(copy.deepcopy(self.data), "", 390, 844, script=(
            f"location.hash = '#event-{self.urgent_id}';" + PROBE.replace("STEP", ""))))
        self.assertEqual(r["hash"], f"#/n/{self.urgent_id}")
        self.assertEqual(r["focused"], [f"n-{self.urgent_id}"])

    def test_mark_read_and_mark_all_read(self):
        step = """
        [...document.querySelectorAll('.notif.is-focused button')].find(b => b.textContent === 'Read').click();
        await new Promise(res => setTimeout(res, 400));
        out.afterOne = document.querySelectorAll('article.notif.is-unread').length;
        out.badgeAfterOne = document.getElementById('notif-badge').textContent;
        [...document.querySelectorAll('.notif-top button')].find(b => b.textContent === 'Read all').click();
        await new Promise(res => setTimeout(res, 400));
        out.afterAll = document.querySelectorAll('article.notif.is-unread').length;
        out.badgeHiddenAfterAll = document.getElementById('notif-badge').hidden;
        """
        r = self.probe(f"/n/{self.urgent_id}", step)
        n = len(self.data["notifications"])
        self.assertEqual(r["afterOne"], n - 1)
        self.assertEqual(r["badgeAfterOne"], str(n - 1))
        self.assertEqual(r["afterAll"], 0)
        self.assertTrue(r["badgeHiddenAfterAll"])

    def test_badge_neutral_without_waiting_on_you_and_calm_empty_state(self):
        data = copy.deepcopy(self.data)
        data["notifications"] = [n for n in data["notifications"] if n["tier"] != "waiting_on_you"]
        for a in data["view"]["agents"]:
            if a.get("tier") == "waiting_on_you":
                a["tier"] = "might_look"
        r = self.probe("/n", data=data)
        self.assertFalse(r["badgeHidden"])
        self.assertFalse(r["urgent"])
        data["notifications"] = []
        r = self.probe("/n", data=data)
        self.assertIn("No notifications yet. You are all caught up.", r["text"])

    def test_gate_reason_and_graph_entry_point(self):
        data = copy.deepcopy(self.data)
        target = next(n for n in data["notifications"] if n["tier"] != "waiting_on_you")
        target["gate"] = {"decision": "held", "reason": "Routine finish, nothing to do"}
        target["delivery"]["state"] = "held"
        r = self.probe(f"/n/{target['id']}", data=data)
        self.assertIn("Technical details", r["text"])
        self.assertNotIn("Held back by the AI check: Routine finish, nothing to do", r["text"], "technical reasoning is collapsed")
        self.assertIn("https://example.invalid/pr/12", json.dumps(self.probe(f"/n/{next(n['id'] for n in data['notifications'] if n['links'])}")["links"]))
        page = run(copy.deepcopy(self.data), "", 390, 844)
        self.assertIn('data-tab="notifications" href="#/n"', page["dom"])
        self.assertIn('href="#/n"', page["dom"])

    def test_read_hidden_by_default_toggle_remembered_and_focus_always_shown(self):
        data = copy.deepcopy(self.data)
        read = data["notifications"][-1]
        read["read"] = True
        step = """
        out.before = [...document.querySelectorAll('article.notif')].map(c => c.id);
        const t = [...document.querySelectorAll('main button')].find(b => b.textContent.startsWith('Show read'));
        out.toggle = t && t.textContent; t.click();
        await new Promise(res => setTimeout(res, 200));
        out.after = [...document.querySelectorAll('article.notif')].map(c => c.id);
        out.stored = localStorage.getItem('hat.notif.showRead');
        localStorage.removeItem('hat.notif.showRead');
        """
        r = self.probe("/n", step, data=data)
        self.assertNotIn(f"n-{read['id']}", r["before"])
        self.assertEqual(r["toggle"], "Show read (1)")
        self.assertIn(f"n-{read['id']}", r["after"])
        self.assertEqual(r["stored"], "1")
        r = self.probe(f"/n/{read['id']}", data=data)
        self.assertEqual(r["focused"], [f"n-{read['id']}"], "a deep-linked read item is still shown")
        for n in data["notifications"]:
            n["read"] = True
        r = self.probe("/n", data=data)
        self.assertIn(f"All caught up. {len(data['notifications'])} read updates hidden.", r["text"])
        self.assertIn(f"Show read ({len(data['notifications'])})", r["text"])

    def test_read_section_counts_grouped_rows_and_dimmed_rows(self):
        from herdr_attention.notifications import group_items
        data = copy.deepcopy(self.data)
        first = data['notifications'][0]
        second = copy.deepcopy(first)
        second['id'] = 98765
        second['at'] -= 60
        second['what'] = 'Earlier useful result'
        data['notifications'].append(second)
        for n in data['notifications']:
            n['read'] = True
        data['notifications'] = group_items(data['notifications'])
        expected = sum(n['is_group_latest'] for n in data['notifications'])
        step = """
        const readSwitch = [...document.querySelectorAll('main button')].find(b=>b.textContent.startsWith('Show read'));
        out.toggle = readSwitch.textContent; readSwitch.click();
        await new Promise(res=>setTimeout(res,200));
        out.heading = document.querySelector('.notif-read h2')?.textContent;
        out.read = document.querySelectorAll('.notif-read article.notif.is-read').length;
        out.folded = !!document.querySelector('.notif-read details.notif-other');
        out.idsAfter = [...document.querySelectorAll('.notif-read article')].map(n=>n.id);
        localStorage.removeItem('hat.notif.showRead');
        """
        r = self.probe('/n', step, data=data)
        self.assertIn(f'All caught up. {expected} read updates hidden.', r['text'])
        self.assertEqual(r['toggle'], f'Show read ({expected})')
        self.assertEqual(r['heading'], f'Read · {expected}')
        self.assertEqual(r['read'], expected)
        self.assertFalse(r['folded'])
        self.assertNotIn('n-98765', r['idsAfter'])

    def test_open_group_shows_older_updates_and_failure_details(self):
        from herdr_attention.notifications import group_items
        data = copy.deepcopy(self.data)
        first = data['notifications'][0]
        first['gate'] = {'decision':'unavailable', 'reason':'timeout'}
        second = copy.deepcopy(first)
        second.update(id=98765, at=first['at']-10, what='Earlier useful result')
        data['notifications'] = group_items([first, second])
        r = self.probe(f"/n/{first['id']}", """
          const group=[...document.querySelectorAll('article details')].find(d=>d.querySelector('summary').textContent==='2 updates');
          group.open=true;
          [...document.querySelectorAll('article details')].find(d=>d.querySelector('summary').textContent==='Technical details').open=true;
          out.expanded=document.querySelector('main').innerText;
        """, data=data)
        self.assertIn('Earlier useful result', r['expanded'])
        self.assertIn('AI check unavailable: timeout', r['expanded'])

    def test_gate_account_health_is_visible_in_settings_and_quotas(self):
        data=copy.deepcopy(self.data)
        data['view']['notify_gate'].update(account='codex-personal',account_label='Codex personal',health={'state':'limited','since':1700000000})
        for route in ('/settings','/quotas'):
            with self.subTest(route=route):
                r=self.probe(route, data=data)
                self.assertIn('Codex personal: limited since',r['text'])

    def test_mark_read_handles_every_repeat_in_group(self):
        from herdr_attention.notifications import group_items
        data=copy.deepcopy(self.data)
        first=data['notifications'][0]
        second=copy.deepcopy(first)
        second.update(id=98765,at=first['at']-60)
        data['notifications']=group_items([first,second])
        r=self.probe(f"/n/{first['id']}", """
          await markNotificationRead(FIXTURE.notifications[0].id);
          out.unreadAfter=FIXTURE.notifications.filter(n=>!n.read).length;
        """,data=data)
        self.assertEqual(r['unreadAfter'],0)

    def test_expired_completion_has_clear_state_in_agent_history(self):
        from urllib.parse import quote
        from herdr_attention.content import event_label
        data=copy.deepcopy(self.data)
        identity=data['ids']['ui']
        event={'id':98765,'at':1700000000,'type':'completion','notify_state':'expired','title':'Choose the network','resolved_at':1700000100}
        event['state_label']=event['label']=event_label(event)
        data['details'][identity]['events'].append(event)
        r=self.probe('/a/'+quote(identity,safe='')+'/activity', """
          document.querySelector('.activity-filter button:nth-child(2)').click(); await new Promise(r=>setTimeout(r,50));
          out.expanded=document.querySelector('main').innerText;
        """,data=data)
        self.assertIn('No longer needed',r['expanded'])

    def test_inline_read_controls_on_unread_rows_only_and_phone_targets(self):
        from herdr_attention.ui_contract import FEATURES
        for desktop in (False, True):
            with self.subTest(desktop=desktop):
                data=copy.deepcopy(self.data)
                data['view']['features'][FEATURES['desktop_focus']]=desktop
                data['notifications'][-1]['read']=True
                r=self.probe('/n', """
                  localStorage.setItem('hat.notif.showRead','1');render(true);
                  document.querySelectorAll('.notif-other').forEach(d=>d.open=true);
                  out.unread=document.querySelectorAll('article.notif.is-unread').length;
                  out.controls=[...document.querySelectorAll('article.notif .notif-mark')].map(b=>{
                    const box=b.getBoundingClientRect(), main=b.closest('article').querySelector('.notif-open').getBoundingClientRect();
                    return {type:b.type,label:b.getAttribute('aria-label'),primary:b.classList.contains('primary'),
                      outside:!b.closest('a'),size:Math.min(box.width,box.height),
                      overlaps:box.left<main.right && main.left<box.right && box.top<main.bottom && main.top<box.bottom,
                      fits:box.right<=innerWidth};
                  });
                  out.readControls=document.querySelectorAll('.notif-read .notif-mark').length;
                  localStorage.removeItem('hat.notif.showRead');
                """,data=data)
                self.assertEqual(len(r['controls']),r['unread'])
                self.assertEqual(r['readControls'],0)
                for b in r['controls']:
                    self.assertEqual(b['type'],'button')
                    self.assertEqual(b['label'],'Mark read')
                    self.assertFalse(b['primary'])
                    self.assertTrue(b['outside'])
                    self.assertGreaterEqual(b['size'],44)
                    self.assertFalse(b['overlaps'])
                    self.assertTrue(b['fits'])
        r=self.probe(f'/n/{self.urgent_id}', "out.compactFocused=document.querySelectorAll('.is-focused .notif-mark').length;")
        self.assertEqual(r['compactFocused'],0)
        self.assertEqual(r['buttons'].count('Read'),1)

    def test_inline_read_from_action_and_other_rows_updates_counts_without_navigation(self):
        from herdr_attention.notify_tiers import actionable
        for needs_action in (True,False):
            with self.subTest(needs_action=needs_action):
                data=copy.deepcopy(self.data)
                target=next(n for n in data['notifications'] if actionable(n['tier'])==needs_action)
                data['targetId']=target['id']
                expected=len(data['notifications'])-1
                r=self.probe('/n', """
                  localStorage.setItem('hat.notif.showRead','1');render(true);
                  document.querySelectorAll('.notif-other').forEach(d=>d.open=true);
                  document.querySelector(`#n-${FIXTURE.targetId} .notif-mark`).click();
                  await new Promise(r=>setTimeout(r,250));
                  out.afterHash=location.hash;
                  out.focusedAfter=document.querySelectorAll('.notif.is-focused').length;
                  out.unreadAfter=document.querySelectorAll('.notif.is-unread').length;
                  out.badgeAfter=document.getElementById('notif-badge').textContent;
                  out.inRead=!!document.querySelector(`.notif-read #n-${FIXTURE.targetId}`);
                  out.readHeading=document.querySelector('.notif-read h2').textContent;
                  out.readControls=document.querySelectorAll('.notif-read .notif-mark').length;
                  out.headsAfter=[...document.querySelectorAll('.notif-group h2, .notif-other > summary')].map(n=>n.textContent);
                  [...document.querySelectorAll('main button')].find(b=>b.textContent==='Hide read').click();
                  out.showRead=[...document.querySelectorAll('main button')].find(b=>b.textContent.startsWith('Show read')).textContent;
                  localStorage.removeItem('hat.notif.showRead');
                """,data=data)
                self.assertEqual(r['afterHash'],'#/n')
                self.assertEqual(r['focusedAfter'],0)
                self.assertEqual(r['unreadAfter'],expected)
                self.assertEqual(r['badgeAfter'],str(expected))
                self.assertTrue(r['inRead'])
                self.assertEqual(r['readHeading'],'Read · 1')
                self.assertEqual(r['showRead'],'Show read (1)')
                self.assertEqual(r['readControls'],0)
                left=[n for n in data['notifications'] if n['id']!=target['id']]
                action_count=sum(actionable(n['tier']) for n in left)
                other_count=len(left)-action_count
                if action_count: self.assertIn(f'Needs action · {action_count}',r['headsAfter'])
                if other_count: self.assertIn(f'Other updates ({other_count})',r['headsAfter'])

    def test_inline_read_marks_whole_group(self):
        from herdr_attention.notifications import group_items
        data=copy.deepcopy(self.data)
        first=data['notifications'][0]
        second=copy.deepcopy(first);second.update(id=98765,at=first['at']-60)
        data['notifications']=group_items([first,second])
        r=self.probe('/n', """
          document.querySelectorAll('.notif-other').forEach(d=>d.open=true);
          document.querySelector('article.notif .notif-mark').click();
          await new Promise(r=>setTimeout(r,200));
          out.unreadAfter=FIXTURE.notifications.filter(n=>!n.read).length;
          out.acks=window.__calls.filter(([path,method])=>method==='POST' && path.endsWith('/ack')).map(([path])=>path);
          out.afterHash=location.hash;
          out.hiddenBadge=document.getElementById('notif-badge').hidden;
          out.readToggle=[...document.querySelectorAll('main button')].find(b=>b.textContent.startsWith('Show read')).textContent;
        """,data=data)
        self.assertEqual(r['unreadAfter'],0)
        self.assertEqual(set(r['acks']),{f"/api/events/{n['id']}/ack" for n in data['notifications']})
        self.assertEqual(r['afterHash'],'#/n')
        self.assertTrue(r['hiddenBadge'])
        self.assertEqual(r['readToggle'],'Show read (1)')

    def test_inline_read_failure_stays_unread_with_visible_error(self):
        data=copy.deepcopy(self.data);data['ackFails']=True
        r=self.probe('/n', """
          const row=document.querySelector('article.notif.is-unread');
          const id=row.id;row.querySelector('.notif-mark').click();
          await new Promise(r=>setTimeout(r,200));
          out.afterHash=location.hash;
          out.stillUnread=document.getElementById(id).classList.contains('is-unread');
          out.enabled=!document.getElementById(id).querySelector('.notif-mark').disabled;
          out.error=document.getElementById('banner').textContent;
          out.errorVisible=!document.getElementById('banner').hidden;
        """,data=data)
        self.assertEqual(r['afterHash'],'#/n')
        self.assertTrue(r['stillUnread'])
        self.assertTrue(r['enabled'])
        self.assertTrue(r['errorVisible'])
        self.assertIn('Not marked read: database is locked',r['error'])

    def test_inline_read_inflight_survives_poll_without_duplicate_ack(self):
        r=self.probe('/n', """
          const realFetch=window.fetch;
          let releaseAck, ackCalls=0;
          window.fetch=async(path,init)=>{
            if(init?.method==='POST' && String(path).endsWith('/ack')) {
              ackCalls++;await new Promise(resolve=>releaseAck=resolve);
            }
            return realFetch(path,init);
          };
          const row=document.querySelector('article.notif.is-unread'), id=row.id;
          const button=row.querySelector('.notif-mark');button.click();
          FIXTURE.view.version++;await refresh(true);
          out.same=document.getElementById(id).querySelector('.notif-mark')===button;
          out.disabled=button.disabled;button.click();
          releaseAck();await new Promise(r=>setTimeout(r,200));
          out.acks=ackCalls;
          out.afterHash=location.hash;
          out.leftUnread=!document.getElementById(id);
        """)
        self.assertTrue(r['same'])
        self.assertTrue(r['disabled'])
        self.assertEqual(r['acks'],1)
        self.assertEqual(r['afterHash'],'#/n')
        self.assertTrue(r['leftUnread'])

    def test_mixed_gone_resolved_group_continues_after_missing_member(self):
        from herdr_attention.notifications import group_items
        data=copy.deepcopy(self.data)
        template=data['notifications'][0]
        notes=[]
        ids=[2389,2383,2381,2378,2370,2366,2364]
        for index,id in enumerate(ids):
            note=copy.deepcopy(template)
            note.update(id=id,at=template['at']-index,read=False,tier='waiting_on_you' if id==2383 else 'fyi_finished',
                        actionable=False,text_source='ai',task='Network setup',pane_id='w4:p3B',
                        what='Approve server status viewing over Tailscale?' if id==2383 else 'Firewall steps added to guides',
                        next_action={'kind':'none','text':'Already handled'})
            note['agent'].update(identity='claude:gone',present=False)
            notes.append(note)
        data['notifications']=group_items(notes)
        r=self.probe('/n', """
          const realFetch=window.fetch;
          window.fetch=async(path,init)=>{
            if(init?.method==='POST' && String(path).endsWith('/2389/ack')) {
              FIXTURE.notifications=FIXTURE.notifications.filter(n=>n.id!==2389);
              return {ok:false,status:404,json:async()=>({error:'no such open event'})};
            }
            return realFetch(path,init);
          };
          document.querySelector('.notif-other').open=true;
          document.querySelector('.notif-mark').click();await new Promise(r=>setTimeout(r,250));
          out.unreadAfter=FIXTURE.notifications.filter(n=>!n.read).length;
          out.calls=window.__calls.filter(([path,method])=>method==='POST' && path.endsWith('/ack')).length;
          out.hashAfter=location.hash;
        """,data=data)
        self.assertEqual(r['unreadAfter'],0)
        self.assertEqual(r['calls'],6)
        self.assertEqual(r['hashAfter'],'#/n')

    def test_read_retries_once_with_fresh_csrf_after_restart(self):
        r=self.probe('/n', """
          const realFetch=window.fetch;let posts=0,sessions=0;
          window.fetch=async(path,init)=>{
            if(init?.method==='POST' && String(path).endsWith('/ack')) {
              posts++;
              if(posts===1) return {ok:false,status:403,json:async()=>({error:'missing or wrong CSRF token'})};
              out.fresh=init.headers['X-CSRF-Token']==='new-fixture-csrf';
            }
            if(String(path)===UI.api.session){sessions++;return {ok:true,status:200,json:async()=>({csrf:'new-fixture-csrf'})};}
            return realFetch(path,init);
          };
          document.querySelector('.notif-mark').click();await new Promise(r=>setTimeout(r,250));
          out.posts=posts;out.sessions=sessions;out.hashAfter=location.hash;
        """)
        self.assertTrue(r['fresh'])
        self.assertEqual((r['posts'],r['sessions']),(2,1))
        self.assertEqual(r['hashAfter'],'#/n')

    def test_deep_link_survives_login(self):
        data = copy.deepcopy(self.data)
        data["loginFirst"] = True
        step = """
        out.loginShown = !document.getElementById('login').hidden;
        document.getElementById('passphrase').value = 'x';
        document.getElementById('login-form').requestSubmit();
        await new Promise(res => setTimeout(res, 700));
        out.hashAfter = location.hash;
        out.focusedAfter = [...document.querySelectorAll('article.notif.is-focused')].map(c => c.id);
        """
        r = self.probe(f"/n/{self.urgent_id}", step, data=data)
        self.assertTrue(r["loginShown"])
        self.assertEqual(r["hashAfter"], f"#/n/{self.urgent_id}")
        self.assertEqual(r["focusedAfter"], [f"n-{self.urgent_id}"])


if __name__ == "__main__":
    unittest.main()
