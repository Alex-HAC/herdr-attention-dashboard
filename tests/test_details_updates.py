"""Reading agent details across polling/content/discovery updates, real Chromium."""
import json
import unittest
import urllib.parse
from tests.web_fixture import build
from tests.helpers import agent
from tests.web_harness import BROWSER, run


def result(page):
    raw = page['dom'].split('data-reading="', 1)[1].split('"', 1)[0]
    return json.loads(raw.replace('&quot;', '"').replace('&amp;', '&'))


@unittest.skipUnless(BROWSER, 'no Chromium found')
class DetailsUpdates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = build()
        c = cls.data.pop('collector')
        identity = cls.data['ids']['ui']
        cls.data['updates'] = []
        for i in range(8):
            info = c.client.agent('w1:p2')
            info.update(terminal_title_stripped=f'Live task update {i}', state_change_seq=100000+i,
                        agent_status='working' if i % 2 else 'blocked')
            c.client.set(info)
            if i == 3:
                c.client.set(agent('w1:p99', session='arrival', name='arrival'))
            if i == 5:
                c.client.remove('w1:p99')
            c.clock.advance(1)
            c.handle_snapshot()  # real discovery reconciliation -> engine -> browser
            cls.data['updates'].append({'view': c.view(local=False), 'detail': c.agent_detail(identity)})
        c.store.close()

    def test_full_details_stay_mounted_open_and_scrolled(self):
        identity = self.data['ids']['ui']
        for width in (390, 1280):
            with self.subTest(width=width):
                # The Status screen of the details view, its "Why this state" fold open.
                page = run(self.data, '/a/'+urllib.parse.quote(identity, safe='')+'/status', width, 400, script="""
                    const id = FIXTURE.ids.ui;
                    await loadDetail(id);
                    const root = document.querySelector('#app > div');
                    const more = document.querySelector('details[data-reading-key=why-state]'); more.open = true;
                    window.scrollTo(0, 150);   // well inside the page even when the content gets shorter
                    // 0.9.9.2's phone header can change height with the view and the browser then moves scrollY
                    // by the same amount, so the reading position is the scroll below the top bar.
                    const below = () => window.scrollY - document.querySelector('.topbar').offsetHeight;
                    const y = window.scrollY, spot = below();
                    let stable = true, open = true, scroll = true;
                    for (const update of FIXTURE.updates) {
                      FIXTURE.view = update.view; FIXTURE.details[id] = update.detail;
                      await refresh(); await loadDetail(id); render(true);
                      stable &&= root === document.querySelector('#app > div') && more === document.querySelector('details[data-reading-key=why-state]');
                      open &&= document.querySelector('details[data-reading-key=why-state]').open;
                      scroll &&= Math.abs(below() - spot) < 2;
                    }
                    document.body.dataset.reading = JSON.stringify({stable, open, scroll, y, title: document.querySelector('.sc-task').textContent});
                """)
                r = result(page)
                self.assertGreater(r['y'], 100)
                self.assertTrue(r['stable'], 'same agent root and details DOM survive refresh')
                self.assertTrue(r['open'], 'expanded details survive refresh')
                self.assertTrue(r['scroll'], 'reading scroll survives refresh')
                self.assertEqual(r['title'], 'Live task update 7')

    def test_inspector_stays_mounted_open_scrolled_and_zoomed(self):
        identity = self.data['ids']['ui']
        for width in (390, 1280):
            with self.subTest(width=width):
                page = run(self.data, '/g/'+urllib.parse.quote(identity, safe=''), width, 844, script="""
                    const id = FIXTURE.ids.ui; await loadDetail(id);
                    document.querySelector('[data-door=links]').click();
                    const body = graph.shell.sheetBody, root = body.firstChild;
                    const editor = body.querySelector('details.editor'); editor.open = true;
                    graph.sheetExpanded = true; graph.shell.sheet.classList.add('expanded');
                    body.scrollTop = 200;
                    const y = body.scrollTop, transform = JSON.stringify(graph.transform);
                    let stable = true, open = true, scroll = true, zoom = true;
                    for (const update of FIXTURE.updates) {
                      FIXTURE.view = update.view; FIXTURE.details[id] = update.detail;
                      await refresh(); await loadDetail(id); render(true);
                      stable &&= root === body.firstChild && editor === body.querySelector('details.editor');
                      open &&= body.querySelector('details.editor').open;
                      scroll &&= Math.abs(body.scrollTop-y)<2;
                      zoom &&= JSON.stringify(graph.transform) === transform;
                    }
                    document.body.dataset.reading = JSON.stringify({stable, open, scroll, zoom, y, selected: graph.selected});
                """)
                r = result(page)
                self.assertGreater(r['y'], 50)
                for key in ('stable', 'open', 'scroll', 'zoom'):
                    self.assertTrue(r[key], key)
                self.assertEqual(r['selected'], identity)

    def test_controls_keep_drafts_focus_and_current_action_handlers(self):
        identity = self.data['ids']['ui']
        page = run(self.data, '/g/'+urllib.parse.quote(identity, safe=''), 390, 844, script="""
            const id = FIXTURE.ids.ui; await loadDetail(id);
            const body = graph.shell.sheetBody;
            const updates = async (list) => { for (const update of list) {
              FIXTURE.view = update.view; FIXTURE.details[id] = update.detail;
              await refresh(); await loadDetail(id); render(true);
            } };
            // Activity screen: the message picker keeps its choice and its button the current handler.
            document.querySelector('[data-door=activity]').click();
            const picker = body.querySelector('select[aria-label="How many messages"]'); picker.value = '25';
            await updates(FIXTURE.updates.slice(0, 4));
            let saved, limit;
            loadConversation = async (id, count) => { limit = count; };
            [...body.querySelectorAll('button')].find(b => b.textContent === 'Show messages').click();
            // Links screen: the relations editor keeps its draft, focus and selection.
            document.querySelector('.screen-back').click();
            document.querySelector('[data-door=links]').click();
            const editor = body.querySelector('details.editor'); editor.open = true;
            const input = editor.querySelector('input[aria-label="Team"]');
            input.value = 'draft-team'; input.focus(); input.setSelectionRange(2, 5);
            await updates(FIXTURE.updates.slice(4));
            const focus = document.activeElement === input;
            const selection = input.selectionStart === 2 && input.selectionEnd === 5;
            const draft = input.value;
            relate = async (body) => { saved = body; };
            [...editor.querySelectorAll('button')].find(b => b.textContent === 'Set team').click();
            editor.open = false; render(true);
            const closed = editor === body.querySelector('details.editor') && !editor.open;
            input.blur();   // a real tap on Back moves the focus first
            document.querySelector('.screen-back').click();
            document.body.dataset.reading = JSON.stringify({focus, selection, draft, saved, limit, closed,
                latest: body.querySelector('.sc-task').textContent});
        """)
        r = result(page)
        self.assertTrue(r['focus'])
        self.assertTrue(r['selection'])
        self.assertEqual(r['draft'], 'draft-team')
        self.assertEqual(r['saved']['value'], 'draft-team')
        self.assertEqual(r['saved']['subject'], identity)
        self.assertEqual(r['limit'], 25)
        self.assertTrue(r['closed'], 'an explicitly collapsed section stays collapsed')
        self.assertEqual(r['latest'], 'Live task update 7')

    def test_session_switch_and_unwatched_removal_do_not_reuse_private_details(self):
        identity = self.data['ids']['ui']
        page = run(self.data, '/a/'+urllib.parse.quote(identity, safe='')+'/status', 390, 700, script="""
            const id = FIXTURE.ids.ui; await loadDetail(id);
            const fold = () => document.querySelector('details[data-reading-key=why-state]');
            const more = fold(); more.open = true;
            location.hash = '#/a/' + encodeURIComponent(FIXTURE.ids.docs) + '/status';
            await new Promise(r => setTimeout(r, 100));
            const separate = more !== fold() && !fold().open;
            FIXTURE.view.agents = FIXTURE.view.agents.filter(a => a.identity !== FIXTURE.ids.docs);
            FIXTURE.view.version++; await refresh(true);
            document.body.dataset.reading = JSON.stringify({separate, unavailable: document.querySelector('#app').textContent.includes('not in a watched workspace'),
                hidden: !document.querySelector('.details')});
        """)
        r = result(page)
        self.assertTrue(r['separate'])
        self.assertTrue(r['unavailable'])
        self.assertTrue(r['hidden'])
