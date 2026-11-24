"""v0.9.4 quota overview: radial gauges in Alex's order, at 390 x 844."""
import json
import unittest

from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER, run

# The layout probe runs inside an iframe of the exact phone size. The script
# hands its facts to the probe through window.__calls (the probe copies it).
FACTS = """
  // Gauges in the main grid; folded ones (closed details) are not on screen.
  const visible=[...document.querySelectorAll('.quota-main [data-quota-id]')];
  const tabbar=document.getElementById('tabbar').getBoundingClientRect();
  const facts={width:window.innerWidth,
    order:visible.map(g=>g.dataset.quotaId),
    folds:[...document.querySelectorAll('.quota-fold > summary')].map(s=>s.textContent),
    lowest:Math.max(0,...visible.map(g=>g.getBoundingClientRect().bottom)),
    tabTop:tabbar.top, centre:visible.map(g=>g.querySelector('.quota-center .qc-num')?.textContent ?? null),
    flags:visible.filter(g=>g.querySelector('.quota-flag')).map(g=>g.dataset.quotaId),
    rings:visible.map(g=>g.querySelectorAll('.ring-track').length)};
  EXTRA
  window.__calls.push(['__facts', JSON.stringify(facts)]);
"""


def facts(page):
    for path, value in page["probe"].get("calls", []):
        if path == "__facts":
            return json.loads(value)
    raise AssertionError("no facts from the 390 px frame")


def with_accounts(extra=0, layout=None):
    fixture = page_fixture()
    accounts = fixture["view"]["quotas"]["accounts"]
    accounts += [dict(a, id=f"{a['id']}-copy{i}", name=f"{a['name']} {i}") for i, a in enumerate(accounts[:extra])]
    if layout is not None:
        fixture["view"].setdefault("layout", {}).update(layout(accounts))
    return fixture


@unittest.skipUnless(BROWSER, "no Chromium")
class QuotaGauges(unittest.TestCase):
    def test_default_order_folds_healthy_accounts_and_fits_the_phone(self):
        fixture = with_accounts()
        fixed = fixture["view"]["quotas"]["account_ids"]
        page = run(fixture, "/quotas", 390, 844, script=FACTS.replace("EXTRA", ""))
        f = facts(page)
        self.assertEqual(f["width"], 390)
        healthy = {a["id"] for a in fixture["view"]["quotas"]["accounts"] if a["healthy"]}
        self.assertEqual(f["order"], [i for i in fixed if i not in healthy])
        self.assertEqual(f["folds"], [f"{len(healthy)} accounts fine"])
        self.assertLessEqual(f["lowest"], f["tabTop"])
        self.assertEqual(set(f["flags"]), {"claude:main", "cursor"})
        self.assertEqual(page["probe"]["overflowing"], [])
        self.assertFalse([c for c in page["probe"]["calls"] if c[1] == "POST"])

    def test_saved_order_pins_first_hides_and_never_reorders_by_urgency(self):
        def layout(accounts):
            ids = [a["id"] for a in accounts]
            # account_order misses 'copilot' on purpose: unknown ones follow in fixed order.
            return {"account_order": [i for i in reversed(ids) if i != "copilot"],
                    "account_pinned": ["grokbot"], "account_hidden": ["agy"]}
        fixture = with_accounts(layout=layout)
        order = fixture["view"]["layout"]["account_order"]
        expected = ["grokbot"] + [i for i in order if i not in ("grokbot", "agy")] + ["copilot"]
        f = facts(run(fixture, "/quotas", 390, 844, script=FACTS.replace("EXTRA", "")))
        self.assertEqual(f["order"], expected)
        self.assertEqual(f["folds"], ["1 hidden"])
        # A full account keeps its place; it only gets a flag.
        for a in fixture["view"]["quotas"]["accounts"]:
            if a["id"] == "codex":
                a.update(worst_pct=100, level="wall", healthy=False, flag={"id": "full", "label": "Full"})
        f = facts(run(fixture, "/quotas", 390, 844, script=FACTS.replace("EXTRA", "")))
        self.assertEqual(f["order"], expected)
        self.assertIn("codex", f["flags"])

    def test_twelve_accounts_fit_one_phone_screen(self):
        fixture = with_accounts(extra=4, layout=lambda accounts: {"account_order": [a["id"] for a in accounts]})
        f = facts(run(fixture, "/quotas", 390, 844, script=FACTS.replace("EXTRA", "")))
        self.assertEqual(len(f["order"]), 12)
        self.assertLessEqual(f["lowest"], f["tabTop"])

    def test_tap_opens_details_and_live_refresh_keeps_them(self):
        fixture = with_accounts()
        view = fixture["view"]
        agent = view["agents"][0]
        cursor = next(a for a in view["quotas"]["accounts"] if a["id"] == "cursor")
        cursor["agents"] = [{"identity": agent["identity"], "name": "ui-builder", "stopped": False, "present": True}]
        cursor["history"] = [{"date": f"2026-11-{d}", "messages": d} for d in range(10, 17)]
        view["quota_stops"] = {"enabled": True, "label": "Out of quota", "groups": [
            {"account_id": "cursor", "label": "Cursor", "reset_at": None, "summary": "Stopped until 14 Dec.",
             "agents": [{"identity": agent["identity"], "name": "ui-builder"}]}]}
        extra = """
          document.querySelector('[data-quota-id="cursor"]').click();
          await new Promise(r=>setTimeout(r,80));
          const panel=document.getElementById('quota-open');
          document.querySelector('.quota-fold').open=true;
          render(true); await new Promise(r=>setTimeout(r,80));
          facts.detail={same:document.getElementById('quota-open')===panel,
            expanded:document.querySelector('[data-quota-id="cursor"]').getAttribute('aria-expanded'),
            agents:!!panel.querySelector('a.chip[href*="'+encodeURIComponent(FIXTURE.view.agents[0].identity)+'"]'),
            stopRow:!!panel.querySelector('#quota-stop-cursor'),
            history:panel.querySelectorAll('.quota-history > span').length,
            windows:panel.querySelectorAll('.quota-window').length,
            foldOpen:document.querySelector('.quota-fold').open};
          panel.querySelector('.quota-open-head button').click();
          await new Promise(r=>setTimeout(r,80));
          facts.closed=!document.getElementById('quota-open');
        """
        f = facts(run(fixture, "/quotas", 390, 844, script=FACTS.replace("EXTRA", extra)))
        d = f["detail"]
        self.assertTrue(d["same"] and d["agents"] and d["stopRow"] and d["foldOpen"], d)
        self.assertEqual((d["expanded"], d["history"], d["windows"]), ("true", 7, 3))
        self.assertTrue(f["closed"])

    def test_header_chip_opens_quotas_in_one_tap(self):
        extra = """
          const dials=[...document.querySelectorAll('.topbar [data-quota-dial]')];
          facts.dials=dials.map(d=>d.dataset.quotaDial); facts.tall=Math.min(...dials.map(d=>d.getBoundingClientRect().height));
          facts.now=document.body.textContent.includes('Now: ');
          dials[1].click(); await new Promise(r=>setTimeout(r,200));
          facts.route=route().name; facts.open=(document.getElementById('quota-open')||{dataset:{}}).dataset.readingKey||null;
        """
        fixture = with_accounts()
        f = facts(run(fixture, "/list", 390, 844, script=FACTS.replace("EXTRA", extra)))
        # v0.9.7.4: mini dials instead of the "Now: ..." chip; a tap opens that account's details.
        self.assertEqual(f["dials"], fixture["view"]["quotas"]["dials"])
        self.assertEqual(len(f["dials"]), 3)
        self.assertGreaterEqual(f["tall"], 30)
        self.assertEqual((f["route"], f["open"]), ("quotas", f"quota-open:{f['dials'][1]}"))


@unittest.skipUnless(BROWSER, "no Chromium")
class QuotaChipOnDesktop(unittest.TestCase):
    def test_header_chip_is_clear_of_the_desktop_tab_bar(self):
        extra = """
          const chip=document.querySelector('.topbar [data-quota-chip]').getBoundingClientRect();
          const bar=document.getElementById('tabbar').getBoundingClientRect();
          const hit=document.elementFromPoint(chip.left+chip.width/2, chip.top+chip.height/2);
          facts.chip={width:chip.width, right:chip.right, barLeft:bar.left,
            onTop:!!hit && !!hit.closest('[data-quota-chip]')};
        """
        for width in (1280, 900):
            page = run(with_accounts(), "/list", width, 800, script=FACTS.replace("EXTRA", extra))
            chip = facts(page)["chip"]
            self.assertEqual(facts(page)["width"], width)
            self.assertGreater(chip["width"], 60, (width, chip))
            self.assertLessEqual(chip["right"], chip["barLeft"], (width, chip))
            self.assertTrue(chip["onTop"], (width, chip))


@unittest.skipUnless(BROWSER, "no Chromium")
class OneAccountOrder(unittest.TestCase):
    """Merged Arrange + gauges: one order rule for the grid, the List row and the chip."""

    def test_pinned_reordered_and_hidden_account_follow_one_rule(self):
        from herdr_attention.quotas import summary, header_dials
        fixture = with_accounts()
        view = fixture["view"]
        fixed = view["quotas"]["account_ids"]
        layout = {"account_pinned": ["grokbot"], "account_order": ["codex", "claude:main"],
                  "account_hidden": ["cursor"]}
        view["layout"].update(layout)
        # The server summary is computed with the same layout (collector passes it).
        accounts = view["quotas"]["accounts"]
        view["quotas"]["summary"] = summary(accounts, [], layout)
        view["quotas"]["dials"] = header_dials(accounts, layout, 3)
        view["quotas"]["header"] = {"label": view["quotas"]["summary"]["label"], "urgent": False}
        expected = ["grokbot", "codex", "claude:main"] + [i for i in fixed if i not in ("grokbot", "codex", "claude:main", "cursor")]
        grid = facts(run(fixture, "/quotas", 390, 844, script=FACTS.replace("EXTRA", "")))
        self.assertEqual(grid["width"], 390)
        self.assertEqual(grid["order"], expected)
        self.assertEqual(grid["folds"], ["1 hidden"])
        self.assertLessEqual(grid["lowest"], grid["tabTop"])
        extra = """
          facts.strip=[...document.querySelectorAll('.quota-strip .quota-row')].map(r=>r.dataset.quotaId);
          facts.dials=[...document.querySelectorAll('.topbar [data-quota-dial]')].map(d=>d.dataset.quotaDial);
        """
        listed = facts(run(fixture, "/list", 390, 844, script=FACTS.replace("EXTRA", extra)))
        self.assertEqual(listed["strip"], expected)
        self.assertEqual(listed["dials"], view["quotas"]["dials"])
        self.assertTrue(set(listed["dials"]) <= set(expected))
        self.assertEqual(listed["dials"][0], expected[0])   # pins and Alex's order first
