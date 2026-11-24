"""Settings explains delivery, keeps editing surfaces, and confirms real saves."""
import unittest
from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER, run
from tests.test_typing_survives_refresh import reading
from tests.test_notify import Rig


class PhoneStatus(unittest.TestCase):
    def test_status_explains_master_mode_delay_and_quiet_range(self):
        rig = Rig(presence={'mode': 'auto', 'phone_delay_minutes': 7},
                  notify_tiers={'quiet_hours': True, 'quiet_start': '23:00', 'quiet_end': '07:00'})
        self.addCleanup(rig.store.close)
        status = rig.notifier.status()['status_line']
        self.assertIn('after 7 min away', status)
        self.assertIn('quiet 23:00–07:00', status)
        rig.settings.set_override('presence.mode', 'never')
        self.assertIn('paused', rig.notifier.status()['status_line'])


@unittest.skipUnless(BROWSER, 'no Chromium found')
class SettingsExplanation(unittest.TestCase):
    def test_master_status_sections_quiet_times_and_one_collapsed_ai_section(self):
        r = reading(run(page_fixture(), '/settings', 412, 915, script="""
          const advanced=document.querySelector('.ai-advanced');
          document.body.dataset.reading=JSON.stringify({
            sections:[...document.querySelectorAll('section[id^="settings-"]')].map(e=>e.id),
            status:document.querySelector('.phone-status').innerText,
            master:!!document.querySelector('.phone-status [aria-label="Phone"]'),
            open:advanced.open,
            gates:document.querySelectorAll('.ai-advanced select[data-setting$="_gate"]').length,
            outside:document.querySelectorAll('select[data-setting$="_gate"]:not(.ai-advanced *)').length,
            times:document.querySelectorAll('input[type="time"]').length,
            lock:document.querySelector('input[aria-label="Waiting on you (always on)"]').checked});
        """))
        # v0.9.7 adds Account icons before Phone setup.
        # v0.9.8.9 adds Phone alerts first.
        self.assertEqual(r['sections'], ['settings-phone_alerts','settings-alerts','settings-workspaces','settings-timing','settings-ai','settings-icons','settings-phone'])
        self.assertIn('Phone alerts', r['status'])
        self.assertTrue(r['master'])
        self.assertFalse(r['open'])
        self.assertEqual(r['gates'], 6)
        self.assertEqual(r['outside'], 0)
        self.assertEqual(r['times'], 2)
        self.assertTrue(r['lock'])

    def test_save_feedback_is_success_only_and_preserves_open_advanced(self):
        r = reading(run(page_fixture(), '/settings', 412, 915, script="""
          const real=window.fetch;
          let fail=false;
          window.fetch=async (path,init)=>{
            if(path==='/api/settings' && init?.method==='POST') {
              if(fail) return {ok:false,status:500,json:async()=>({error:'fixture save failure'})};
              Object.assign(FIXTURE.view.settings, JSON.parse(init.body));FIXTURE.view.version++;
            }
            return real(path,init);
          };
          const advanced=document.querySelector('.ai-advanced');advanced.open=true;
          await saveSetting('notify_gate.enabled',false);
          const saved=!!document.querySelector('.save-feedback');
          const current=advanced===document.querySelector('.ai-advanced') && advanced.open;
          const changed=document.querySelector('[data-setting="notify_gate.enabled"]').closest('.setting').classList.contains('just-changed');
          document.querySelectorAll('.save-feedback').forEach(e=>e.remove());
          fail=true;await saveSetting('notify_gate.enabled',true);
          document.body.dataset.reading=JSON.stringify({saved,current,changed,
            error:document.querySelector('#banner').textContent,
            falseSaved:!!document.querySelector('.save-feedback')});
        """))
        for key in ('saved','current','changed'):
            self.assertTrue(r[key],key)
        self.assertFalse(r['falseSaved'])
        self.assertIn('Could not save',r['error'])

    def test_preset_highlights_changed_tiers_and_keeps_waiting_on_you_locked(self):
        from herdr_attention.notify_tiers import apply_preset
        data=page_fixture();data['presetPatch']=apply_preset('quiet')
        r=reading(run(data,'/settings',412,915,script="""
          const real=window.fetch;
          window.fetch=async(path,init)=>{
            if(path==='/api/settings' && init?.method==='POST') {
              Object.assign(FIXTURE.view.settings,FIXTURE.presetPatch);FIXTURE.view.version++;
            }
            return real(path,init);
          };
          await saveSetting('notify_tiers.preset','quiet');
          document.body.dataset.reading=JSON.stringify({
            changed:document.querySelectorAll('.tier-row.just-changed').length,
            lock:document.querySelector('input[aria-label="Waiting on you (always on)"]').checked,
            saved:!!document.querySelector('[data-setting="notify_tiers.preset"]').closest('.setting').querySelector('.save-feedback')});
        """))
        self.assertGreater(r['changed'],0)
        self.assertTrue(r['lock'])
        self.assertTrue(r['saved'])

    def test_watching_list_stops_and_clears_gone_pins_and_badges_list_and_graph(self):
        data=page_fixture();data['view']['notify_tiers']['watch_closely']=[data['ids']['ui'],'gone-fixture']
        r=reading(run(data,'/settings',412,915,script="""
          const wait=()=>new Promise(r=>setTimeout(r,120));const real=window.fetch;
          window.fetch=async(path,init)=>{
            if(path==='/api/settings' && init?.method==='POST') {
              const body=JSON.parse(init.body);
              if(body['notify_tiers.watch_closely']) FIXTURE.view.notify_tiers.watch_closely=body['notify_tiers.watch_closely'];
              FIXTURE.view.version++;
            }
            return real(path,init);
          };
          const card=document.querySelector('.watching-closely');
          const listed=card.textContent.includes('Watching closely · 2') && card.textContent.includes('Gone');
          [...card.querySelectorAll('button')].find(b=>b.textContent.startsWith('Clear gone')).click();await wait();
          const cleared=watchCloselyList().length===1 && watchCloselyList()[0]===FIXTURE.ids.ui;
          location.hash='#/list';await wait();const listBadge=!!document.querySelector('.watch-badge');
          location.hash='#/';await wait();const graphBadge=!![...document.querySelectorAll('.watch-eye')].find(e=>e.getAttribute('visibility')==='visible');
          location.hash='#/settings';await wait();document.querySelector('.watching-row button').click();await wait();
          document.body.dataset.reading=JSON.stringify({listed,cleared,listBadge,graphBadge,stopped:watchCloselyList().length===0});
        """))
        for key in ('listed','cleared','listBadge','graphBadge','stopped'):
            self.assertTrue(r[key],key)

    def test_graph_visibility_is_local_and_watch_chooser_is_separate(self):
        r=reading(run(page_fixture(),'',412,915,script="""
          const wait=()=>new Promise(r=>setTimeout(r,100));
          graph.panel='workspaces';graphRefresh(true);await wait();
          const chooser=document.querySelector('.watched-chooser');const closed=!chooser.open;
          const box=document.querySelector('.workspace-visibility input[role="switch"]');
          const before=window.__calls.filter(c=>c[1]==='POST').length;
          box.click();await wait();
          const local=graph.hiddenWorkspaces.size===1;
          const posts=window.__calls.filter(c=>c[1]==='POST').length-before;
          chooser.open=true;FIXTURE.view.version++;await refresh();
          document.body.dataset.reading=JSON.stringify({closed,local,posts,
            open:document.querySelector('.watched-chooser').open,
            explanation:document.querySelector('.watched-chooser').innerText.includes('every device')});
        """))
        self.assertTrue(r['closed'])
        self.assertTrue(r['local'])
        self.assertEqual(r['posts'],0)
        self.assertTrue(r['open'])
        self.assertTrue(r['explanation'])

    def test_plugin_reads_shared_status_and_links_to_full_settings(self):
        from tests.test_tokens_js import PLUGIN
        source=(PLUGIN/'Panel.qml').read_text()
        self.assertIn('root.view.notify.status_line',source)
        self.assertIn('Open full settings ↗',source)
        self.assertIn('root.service.openOverview("settings")',source)
        self.assertIn('value !== "custom"',source)
