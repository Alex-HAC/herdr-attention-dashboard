"""Summary-first cards and mark-read navigation in a real browser. FIXTURE-BASED.

Summaries in the fixture come from the real pipeline with a stand-in CLI.
"""

import copy
import unittest
import urllib.parse

from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER, run


@unittest.skipUnless(BROWSER, "no Chromium found")
class SummaryCards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = page_fixture()
        ids = cls.data["ids"]
        q = lambda i: urllib.parse.quote(i, safe="")
        cls.ui_route = f"/a/{q(ids['ui'])}"
        cls.overview = {w: run(cls.data, "/list", w, 900) for w in (390, 1280)}
        cls.ui = {w: run(cls.data, cls.ui_route, w, 900) for w in (390, 1280)}
        qa = next(a for a in cls.data["view"]["agents"] if a["name"] is None)
        cls.failed = run(cls.data, f"/a/{q(qa['identity'])}", 390, 900)
        cls.docs = run(cls.data, f"/a/{q(ids['docs'])}", 390, 900)
        # Mark read from the list: open the agent from the overview (scrolled), mark read, expect to be back.
        cls.read_ok = run(cls.data, "/list", 390, 600, script=f"""
            window.scrollTo(0, 120); await new Promise(r => setTimeout(r, 100));
            location.hash = '#{cls.ui_route}'; await new Promise(r => setTimeout(r, 400));
            [...document.querySelectorAll('button')].find(b => b.textContent === 'Mark read').click();
            await new Promise(r => setTimeout(r, 600)); document.body.dataset.scrollAfter = String(window.scrollY);""")
        failing = copy.deepcopy(cls.data)
        failing["ackFails"] = True
        cls.read_fail = run(failing, cls.ui_route, 390, 900, script="""
            [...document.querySelectorAll('button')].find(b => b.textContent === 'Mark read').click();
            await new Promise(r => setTimeout(r, 500));""")
        cls.graph_read = run(cls.data, f"/graph/{q(ids['ui'])}", 1280, 900, script="""
            [...document.querySelectorAll('button')].find(b => b.textContent === 'Mark read').click();
            await new Promise(r => setTimeout(r, 500));""")

    def main(self, page):
        return page["dom"].split('<main id="app"', 1)[1].split("</main>", 1)[0]

    def test_list_shows_ai_headlines_or_honest_labels(self):
        for width, page in self.overview.items():
            html = self.main(page)
            self.assertIn('class="ai-tag" title="AI summary">AI</span> Cart page scaffolded; wants to run npm test', html)
            self.assertIn("Question waiting", html, "no summary: the plain label, not a made-up one")

    def test_summary_comes_first_and_is_labelled(self):
        for width, page in self.ui.items():
            html = self.main(page)
            self.assertLess(html.index("card summary"), html.index('class="more"'), "summary before the details")
            self.assertNotIn('<details class="more" open', html, "details are folded")
            for text in ("AI summary", "claude-sonnet-test · effort medium", "Run npm test in ./cart?",
                         "<q>1. Yes</q>", "<q>2. Yes, and don't ask again for npm test</q>",
                         "left out because the source does not contain them", "Summarize", "Summarize longer"):
                self.assertIn(text, html, text)
            self.assertNotIn("Wipe the repo", html)
            self.assertNotIn("Pushed to main", html)
            self.assertEqual(page["probe"]["overflowing"], [])
            self.assertEqual(page["probe"]["smallTargets"], [])

    def test_failed_and_fallback_states(self):
        self.assertIn("Summary failed: timed out", self.main(self.failed))
        self.assertIn("Retry", self.main(self.failed))
        self.assertIn("Not an AI summary: the agent's last message", self.main(self.failed))
        self.assertIn("none yet", self.main(self.docs))

    def test_mark_read_returns_to_the_list_keeping_scroll(self):
        probe = self.read_ok["probe"]
        self.assertEqual(probe["hash"], "#/list", "back on the list it came from")
        self.assertIn(["/api/events/" + str(next(a for a in self.data["view"]["agents"] if a["name"] == "ui-builder")["event_id"]) + "/ack", "POST"],
                      probe["calls"])
        scroll = int(self.read_ok["dom"].split('data-scroll-after="', 1)[1].split('"', 1)[0])
        self.assertGreater(scroll, 60, "scroll position kept")

    def test_failed_mark_read_stays_open_with_the_error(self):
        probe = self.read_fail["probe"]
        self.assertEqual(probe["hash"], f"#{self.ui_route}")
        self.assertIn("Not marked read: database is locked", self.main(self.read_fail))

    def test_mark_read_in_the_graph_closes_the_inspector(self):
        self.assertEqual(self.graph_read["probe"]["hash"], "#/")

    def test_terminal_is_cleaned_unless_raw_is_asked(self):
        calls = [c[0] for page in list(self.ui.values()) + [self.failed] for c in page["probe"]["calls"]]
        self.assertFalse([c for c in calls if c.startswith("/api/output")], "terminal only on request")


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(BROWSER, "no Chromium found")
class InlineAttentionRead(unittest.TestCase):
    def probe(self, route='/list', step='', fail=False):
        from tests.test_typing_survives_refresh import reading
        data=page_fixture();data['ackFails']=fail
        script="""
          const realFetch=window.fetch;
          window.fetch=async(path,init)=>{
            const response=await realFetch(path,init);
            if(response.ok && init?.method==='POST' && String(path).endsWith('/ack')) {
              const id=String(path).split('/').at(-2);
              FIXTURE.view.agents.filter(a=>String(a.event_id)===id).forEach(a=>{a.acked=true;a.tier=null;});
              FIXTURE.view.version++;
            }
            return response;
          };
          const out={};
          STEP
          document.body.dataset.reading=JSON.stringify(out);
        """.replace('STEP',step)
        return reading(run(data,route,390,844,script=script))

    def test_overview_inline_ack_leaves_strip_without_navigation(self):
        r=self.probe(step="""
          const row=document.querySelector('.attention-list li:has(.notif-mark)');
          const key=row.dataset.readingKey, button=row.querySelector('.notif-mark');
          const box=button.getBoundingClientRect();
          out.size=Math.min(box.width,box.height);out.quiet=button.classList.contains('quiet') && !button.classList.contains('primary');
          out.outside=!button.closest('a');
          out.expected=attentionItems(app.view).filter(a=>!a.quota_stop && canMarkAgentRead(a)).length;
          out.controls=document.querySelectorAll('.attention-list .notif-mark').length;
          button.click();await new Promise(r=>setTimeout(r,200));
          out.hash=location.hash;
          out.gone=![...document.querySelectorAll('.attention-list li')].some(li=>li.dataset.readingKey===key);
        """)
        self.assertEqual(r['controls'],r['expected'])
        self.assertGreaterEqual(r['size'],44)
        for key in ('quiet','outside','gone'):self.assertTrue(r[key],key)
        self.assertEqual(r['hash'],'#/list')

    def test_acknowledged_or_eventless_attention_rows_have_no_control(self):
        r=self.probe(step="""
          const agent=attentionItems(app.view).find(a=>!a.quota_stop);
          agent.acked=true;
          out.acked=!!attentionRow(agent).querySelector('.notif-mark');
          agent.acked=false;agent.event_id=null;
          out.eventless=!!attentionRow(agent).querySelector('.notif-mark');
          agent.event_id=123;app.view.features.acknowledge=false;
          out.disabled=!!attentionRow(agent).querySelector('.notif-mark');
        """)
        self.assertFalse(any(r.values()))

    def test_overview_inline_failure_is_visible_and_keeps_row(self):
        r=self.probe(fail=True,step="""
          const row=document.querySelector('.attention-list li:has(.notif-mark)'), key=row.dataset.readingKey;
          row.querySelector('.notif-mark').click();await new Promise(r=>setTimeout(r,200));
          out.hash=location.hash;
          out.kept=[...document.querySelectorAll('.attention-list li')].some(li=>li.dataset.readingKey===key);
          out.error=document.getElementById('banner').textContent;
          out.visible=!document.getElementById('banner').hidden;
        """)
        self.assertTrue(r['kept'])
        self.assertTrue(r['visible'])
        self.assertEqual(r['hash'],'#/list')
        self.assertIn('Not marked read: database is locked',r['error'])

    def test_overview_poll_keeps_inflight_button(self):
        r=self.probe(step="""
          const ackFetch=window.fetch;
          let finishAck;
          window.fetch=async(path,init)=>{
            if(init?.method==='POST' && String(path).endsWith('/ack')) await new Promise(resolve=>finishAck=resolve);
            return ackFetch(path,init);
          };
          const row=document.querySelector('.attention-list li:has(.notif-mark)'), key=row.dataset.readingKey, button=row.querySelector('.notif-mark');
          button.click();FIXTURE.view.version++;await refresh(true);
          out.same=document.contains(button) && [...document.querySelectorAll('.attention-list li')].find(li=>li.dataset.readingKey===key).querySelector('.notif-mark')===button;
          out.disabled=button.disabled;
          finishAck();await new Promise(r=>setTimeout(r,200));
          out.hash=location.hash;out.gone=!document.contains(button);
        """)
        self.assertTrue(r['same'])
        self.assertTrue(r['disabled'])
        self.assertTrue(r['gone'])
        self.assertEqual(r['hash'],'#/list')

    def test_graph_attention_panel_uses_same_control_without_opening_agent(self):
        r=self.probe('/',step="""
          graph.panel='attention';graphRefresh(true);
          const row=document.querySelector('.panel-list li:has(.notif-mark)'), key=row.dataset.readingKey;
          out.outside=!row.querySelector('.notif-mark').closest('.panel-item');
          row.querySelector('.notif-mark').click();await new Promise(r=>setTimeout(r,200));
          out.hash=location.hash;out.panel=graph.panel;out.selected=graph.selected;
          out.gone=![...document.querySelectorAll('.panel-list li')].some(li=>li.dataset.readingKey===key);
        """)
        self.assertTrue(r['outside'])
        self.assertTrue(r['gone'])
        self.assertEqual(r['hash'],'#/')
        self.assertEqual(r['panel'],'attention')
        self.assertFalse(r['selected'])
