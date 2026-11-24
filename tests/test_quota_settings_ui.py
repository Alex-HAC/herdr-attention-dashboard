"""v0.9.5 quota view settings on the Quotas page, at 390 x 844."""
import json
import unittest

from herdr_attention import quotas
from tests.test_quota_gauges_ui import FACTS, facts, with_accounts
from tests.web_harness import BROWSER, run

# Keep POSTed settings in the stubbed view, as the server does, and record every write.
CAPTURE = """
  window.__posts=[];
  const stubFetch=window.fetch;
  window.fetch=async(path,init)=>{
    if(init?.method==='POST'){
      const body=JSON.parse(init.body||'{}');window.__posts.push(body);
      for(const [key,value] of Object.entries(body)){
        FIXTURE.view.settings[key]=value;
        if(key.startsWith('layout.')) FIXTURE.view.layout[key.slice(7)]=value;
      }
    }
    return stubFetch(path,init);
  };
  const wait=ms=>new Promise(r=>setTimeout(r,ms));
  const panel=()=>document.getElementById('quota-settings');
  const posted=()=>Object.assign({},...window.__posts);
"""


def with_view(fixture, layout=None, **view):
    """The fixture as the server would send it with these quota view settings saved."""
    v = fixture["view"]
    v["layout"].update(layout or {})
    full = {**quotas.DEFAULT_VIEW, **view}
    quotas.enrich(v["quotas"], v["agents"], [], v["layout"], full)
    v["settings"].update({f"quotas.{k}": value for k, value in full.items()})
    v["settings"].update({f"layout.{k}": v["layout"].get(k, []) for k in ("account_order", "account_pinned", "account_hidden")})
    return fixture


def page(fixture, extra="", route="/quotas"):
    return facts(run(fixture, route, 390, 844, script=CAPTURE + FACTS.replace("EXTRA", extra)))


@unittest.skipUnless(BROWSER, "no Chromium")
class QuotaSettingsPanel(unittest.TestCase):
    def test_one_button_at_the_top_opens_the_settings_on_the_page(self):
        extra = """
          const button=document.querySelector('.quota-head .quota-settings-open');
          facts.button={text:button.textContent.trim(), top:button.getBoundingClientRect().top,
            firstGauge:document.querySelector('.quota-main [data-quota-id]').getBoundingClientRect().top};
          button.click(); await wait(120);
          facts.open={expanded:button.getAttribute('aria-expanded'), panel:!!panel(),
            rows:[...panel().querySelectorAll('.quota-arrange [data-account-id]')].map(r=>r.dataset.accountId),
            parts:[...panel().querySelectorAll('.quota-parts input')].map(i=>i.checked),
            levels:[...panel().querySelectorAll('.quota-levels input')].map(i=>i.value),
            size:[...panel().querySelectorAll('.segmented [aria-pressed]')].map(b=>b.getAttribute('aria-pressed')),
            dials:[...panel().querySelectorAll('[data-quota-focus^="dials:"]')].map(b=>b.getAttribute('aria-pressed')),
            dialsOn:panel().querySelector('[data-reading-key="qs-dials"] input').checked};
          panel().querySelector('.quota-open-head button').click(); await wait(80);
          facts.closed=!panel() && document.activeElement===document.querySelector('.quota-settings-open');
        """
        fixture = with_accounts()
        f = page(fixture, extra)
        self.assertEqual(f["button"]["text"], "Arrange and settings")
        self.assertLess(f["button"]["top"], f["button"]["firstGauge"])
        o = f["open"]
        self.assertEqual((o["expanded"], o["panel"]), ("true", True))
        self.assertEqual(o["rows"], fixture["view"]["quotas"]["account_ids"])
        self.assertEqual(o["parts"], [True] * 5)
        self.assertEqual(o["levels"], ["70", "90"])
        self.assertEqual(o["size"][:2], ["true", "false"])
        # v0.9.7.4: header mini dials, on by default, 3 of 1-6.
        self.assertEqual((o["dialsOn"], o["dials"]), (True, ["false", "false", "true", "false", "false", "false"]))
        self.assertTrue(f["closed"])

    def test_arrange_on_the_quotas_page_saves_the_same_order_as_arrange(self):
        extra = """
          document.querySelector('.quota-settings-open').click(); await wait(80);
          const press=(id,what)=>panel().querySelector(`[data-quota-focus="${id}:${what}"]`).click();
          press('grokbot','account_pinned'); await wait(400);
          press('codex','up'); await wait(400);
          press('agy','account_hidden'); await wait(400);
          facts.saved=posted();
          facts.rows=[...panel().querySelectorAll('.quota-arrange [data-account-id]')].map(r=>r.dataset.accountId);
          facts.status=panel().querySelector('.quota-settings-status').textContent;
          facts.focus=document.activeElement?.dataset.quotaFocus;
          facts.grid=[...document.querySelectorAll('.quota-main [data-quota-id]')].map(g=>g.dataset.quotaId);
          facts.folds=[...document.querySelectorAll('.quota-fold > summary')].map(s=>s.textContent);
        """
        fixture = with_accounts()
        fixed = fixture["view"]["quotas"]["account_ids"]
        f = page(fixture, extra)
        saved = f["saved"]
        self.assertEqual(saved["layout.account_pinned"], ["grokbot"])
        self.assertEqual(saved["layout.account_hidden"], ["agy"])
        expected = ["grokbot"] + [i for i in fixed if i != "grokbot"]
        expected.remove("codex")
        expected.insert(expected.index("claude:personal"), "codex")
        self.assertEqual(f["rows"], expected)
        self.assertEqual(saved["layout.account_order"][:len(expected)], expected)
        self.assertEqual(f["status"], "Saved")
        self.assertEqual(f["focus"], "agy:account_hidden")
        # The grid follows the saved order; the hidden account is in its fold.
        self.assertEqual(f["grid"], [i for i in expected if i != "agy"])
        self.assertEqual(f["folds"], ["1 hidden"])

    def test_parts_and_levels_for_all_accounts_and_for_one_account(self):
        extra = """
          document.querySelector('.quota-settings-open').click(); await wait(80);
          const part=id=>panel().querySelector(`.quota-parts input[value="${id}"]`);
          part('agents').click(); await wait(400);
          facts.all=posted()['quotas.gauge_show'];
          const scope=panel().querySelector('.quota-scope select'); scope.value='cursor';
          scope.dispatchEvent(new Event('change')); await wait(80);
          facts.sameBefore=!!panel().querySelector('.quota-parts .btn');
          part('weekly').click(); await wait(400);
          facts.cursor=posted()['quotas.gauge_accounts'];
          facts.sameAfter=!!panel().querySelector('.quota-parts .btn');
          const level=name=>panel().querySelector(`.quota-levels input[name="${name}"]`);
          level('warning').value='95'; level('critical').value='90'; level('critical').dispatchEvent(new Event('change'));
          await wait(400);
          facts.error=panel().querySelector('.quota-levels [role=alert]')?.textContent;
          facts.badSaved='quotas.account_levels' in posted();
          level('warning').value='60'; level('critical').value='80'; level('critical').dispatchEvent(new Event('change'));
          await wait(400);
          facts.levels=posted()['quotas.account_levels'];
          facts.globalLevels='quotas.levels' in posted();
        """
        f = page(with_accounts(), extra)
        self.assertEqual(f["all"], ["weekly", "session", "percent", "reset"])
        self.assertFalse(f["sameBefore"])
        self.assertEqual(f["cursor"], {"cursor": ["session", "percent", "reset"]})
        self.assertTrue(f["sameAfter"])
        self.assertEqual(f["error"], "The warning level must be below nearly full.")
        self.assertFalse(f["badSaved"])
        self.assertEqual(f["levels"], {"cursor": {"warning": 60, "critical": 80}})
        self.assertFalse(f["globalLevels"])

    def test_gauges_show_only_the_chosen_parts(self):
        extra = """
          const g=id=>document.querySelector(`.quota-main [data-quota-id="${id}"]`);
          facts.cursor={rings:g('cursor').querySelectorAll('.ring-track').length, centre:g('cursor').querySelector('.quota-center .qc-num')?.textContent,
            lines:[...g('cursor').querySelectorAll('.quota-gline:not(.quota-old)')].map(l=>l.textContent), flag:g('cursor').querySelector('.quota-flag')?.textContent,
            spoken:g('cursor').getAttribute('aria-label')};
          facts.main={rings:g('claude:main').querySelectorAll('.ring-track').length, centre:!!g('claude:main').querySelector('.quota-center')};
        """
        fixture = with_view(with_accounts(), gauge_show=["weekly", "agents"], gauge_accounts={"cursor": ["percent", "reset"]})
        f = page(fixture, extra)
        cursor = next(a for a in fixture["view"]["quotas"]["accounts"] if a["id"] == "cursor")
        self.assertEqual((f["cursor"]["rings"], f["cursor"]["centre"]), (0, "93%"))   # 0.9.8.7: Monthly, not the Named models pool
        self.assertEqual(f["cursor"]["lines"], [cursor["reset_label"]])
        self.assertEqual(f["cursor"]["flag"], "Nearly full")       # the flag always shows (0.9.8.8: Monthly 93 %; the full Named models pool is its own line)
        self.assertIn("Named models: 100%", f["cursor"]["spoken"])   # model-only limits are read out too
        self.assertEqual(f["main"], {"rings": 1, "centre": False})


@unittest.skipUnless(BROWSER, "no Chromium")
class QuotaSizeAndGroups(unittest.TestCase):
    def test_compact_keeps_sixteen_accounts_on_one_phone_screen(self):
        fixture = with_accounts(extra=8)
        order = [a["id"] for a in fixture["view"]["quotas"]["accounts"]]
        fixture = with_view(fixture, layout={"account_order": order}, gauge_size="compact")
        page_ = run(fixture, "/quotas", 390, 844, script=FACTS.replace("EXTRA", ""))
        f = facts(page_)
        self.assertEqual(len(f["order"]), 16)
        self.assertLessEqual(f["lowest"], f["tabTop"])
        self.assertEqual(page_["probe"]["overflowing"], [])

    def test_size_toggle_shows_at_once_and_saves(self):
        extra = """
          document.querySelector('.quota-settings-open').click(); await wait(80);
          panel().querySelector('[data-quota-focus="size:compact"]').click(); await wait(30);
          facts.compactNow=document.querySelector('.quota-view').classList.contains('q-size-compact');
          await wait(400);
          facts.saved=posted()['quotas.gauge_size'];
        """
        f = page(with_accounts(), extra)
        self.assertTrue(f["compactNow"])
        self.assertEqual(f["saved"], "compact")

    def test_group_by_provider_keeps_pins_and_order_and_a_full_account_never_moves(self):
        extra = """
          facts.groups=[...document.querySelectorAll('.quota-group')].map(s=>[s.querySelector('h2').textContent,
            [...s.querySelectorAll('[data-quota-id]')].map(g=>g.dataset.quotaId)]);
        """
        fixture = with_accounts()
        fixed = fixture["view"]["quotas"]["account_ids"]
        layout = {"account_order": ["codex-personal:personal"] + fixed, "account_pinned": ["claude:personal"]}
        fixture = with_view(fixture, layout=layout, group_by_provider=True)
        f = page(fixture, extra)
        groups = dict(f["groups"])
        self.assertEqual(f["groups"][0][0], "Claude Code")                      # holds the pinned account
        self.assertEqual(groups["Claude Code"], ["claude:personal", "claude:main"])
        self.assertEqual(groups["Codex"], ["codex-personal:personal", "codex"])
        self.assertEqual(groups["Cursor"], ["cursor"])                          # full, flagged, in its place
        self.assertEqual([g[0] for g in f["groups"]][1:3], ["Codex", "Grok Bot"])


@unittest.skipUnless(BROWSER, "no Chromium")
class UnassignedLine(unittest.TestCase):
    def test_line_counts_running_agents_once_and_lists_them(self):
        extra = """
          const line=document.querySelector('.quota-unassigned');
          facts.line=line?.querySelector('summary').textContent; line.open=true;
          facts.chips=[...line.querySelectorAll('a.chip')].length;
        """
        fixture = with_accounts()
        q = fixture["view"]["quotas"]
        f = page(fixture, extra)
        self.assertEqual(f["line"], f"{q['unassigned_agents']} running agents have no recorded account, so no gauge counts them.")
        self.assertEqual(f["chips"], q["unassigned_agents"])
        self.assertLessEqual(q["unassigned_agents"], len({a["pane_id"] for a in fixture["view"]["agents"] if a.get("pane_id")}))

    def test_no_line_when_every_agent_is_known(self):
        fixture = with_accounts()
        fixture["view"]["quotas"].update(unassigned_agents=0, unassigned=[])
        f = page(fixture, "facts.line=!!document.querySelector('.quota-unassigned');")
        self.assertFalse(f["line"])


if __name__ == "__main__":
    unittest.main()
