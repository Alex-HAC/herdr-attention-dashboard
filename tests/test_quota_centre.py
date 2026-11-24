"""v0.9.8.8: which limit the number in the middle of a quota ring shows, and model-only limits.

Alex 19 Nov 21:17: "can we somehow choose whats the % inside the radial?" And Claude Code Personal
read 100 % with "Agents stopped" while new Opus agents ran fine: the 100 % was "Fable Weekly", a
limit for one model only, which had taken the outer ring and the number (the real Weekly was 60 %).
"""
import unittest

from herdr_attention import quotas
from herdr_attention.config import EDITABLE
from tests.test_quota_gauges_ui import with_accounts
from tests.test_quota_settings_ui import CAPTURE, page, with_view
from tests.web_harness import BROWSER

RINGS = [{"kind": "weekly", "title": "Weekly", "pct": 62, "unit": "wk"},
         {"kind": "session", "title": "Session", "pct": 21, "unit": "5h"}]


class Centre(unittest.TestCase):
    def test_each_choice_names_its_limit(self):
        c = quotas.centre
        self.assertEqual((c(RINGS, "higher")["text"], c(RINGS, "higher")["unit"]), ("62%", "wk"))
        self.assertEqual((c(RINGS, "weekly")["text"], c(RINGS, "weekly")["unit"]), ("62%", "wk"))
        self.assertEqual((c(RINGS, "session")["text"], c(RINGS, "session")["unit"]), ("21%", "5h"))
        both = c(RINGS, "both")
        self.assertEqual((both["text"], both["unit"], both["both"]), ("62 \u00b7 21", "wk \u00b7 5h", True))
        hot = [dict(RINGS[0], pct=40), dict(RINGS[1], pct=95)]
        self.assertEqual(c(hot, "higher")["unit"], "5h", "the fuller one, labelled")

    def test_an_account_without_a_5_hour_limit_says_so(self):
        weekly = RINGS[:1]
        self.assertEqual((quotas.centre(weekly, "session")["text"], quotas.centre(weekly, "session")["unit"]), ("62%", "wk"))
        self.assertFalse(quotas.centre(weekly, "both")["both"])
        self.assertIsNone(quotas.centre([], "both"))

    def test_the_setting_is_saved_like_the_others_and_checked(self):
        self.assertTrue(EDITABLE["quotas.centre"][1])
        self.assertEqual(quotas.validate_centre("both"), "both")
        with self.assertRaises(ValueError):
            quotas.validate_centre("median")


class ModelOnlyLimits(unittest.TestCase):
    def snapshot(self):
        windows = [{"label": "Weekly (7-day)", "title": "Weekly", "used_pct": 60, "resets_label": "Mon 20:00",
                    "resets_at": "2026-11-23T20:00:00+01:00", "resets_in_seconds": 340000},
                   {"label": "Fable Weekly", "title": "Fable Weekly", "used_pct": 100, "resets_label": "Mon 20:00",
                    "resets_at": "2026-11-23T20:00:00+01:00", "resets_in_seconds": 340000}]
        return {"timezone": "Europe/Amsterdam", "accounts": [
            {"id": "claude:personal", "provider": "claude", "provider_name": "Claude Code", "label": "Personal",
             "windows": windows, "age_seconds": 5, "fetched_age_label": "just now"}]}

    def test_a_full_model_limit_is_a_line_not_the_account(self):
        q = quotas.enrich(self.snapshot(), [])
        a = q["accounts"][0]
        self.assertEqual([(r["title"], r["pct"]) for r in a["rings"]], [("Weekly", 60)])
        self.assertEqual((a["worst_pct"], a["out_of_quota"], a["centre"]["text"]), (60, False, "60%"))
        self.assertEqual(a["scoped"][0]["label"], "Fable: 100% \u00b7 resets Mon 20:00")
        self.assertEqual(a["flag"], {"id": "scoped_full", "label": "Fable limit reached"})
        self.assertEqual(a["header_label"], "Personal 60% wk")

    def test_the_flag_counts_the_stopped_agents(self):
        stopped = [{"identity": f"claude:{i}", "name": f"a{i}", "present": True, "quota_wall": {"x": 1},
                    "launch": {"account": "claude-personal"}} for i in range(2)]
        a = quotas.enrich(self.snapshot(), stopped[:1])["accounts"][0]
        self.assertEqual(a["flag"]["label"], "1 agent stopped")
        a = quotas.enrich(self.snapshot(), stopped)["accounts"][0]
        self.assertEqual(a["flag"]["label"], "2 agents stopped")


@unittest.skipUnless(BROWSER, "no Chromium")
class CentreOnScreen(unittest.TestCase):
    FACTS = """
      const g=id=>document.querySelector(`.quota-main [data-quota-id="${id}"]`);
      const num=id=>g(id).querySelector('.quota-center .qc-num')?.textContent;
      const unit=id=>g(id).querySelector('.quota-center .qc-unit')?.textContent;
      facts.main={num:num('claude:main'), unit:unit('claude:main')};
      facts.dials=[...document.querySelectorAll('.quota-dial-mini')].map(d=>d.querySelector('.qd-pct').textContent);
      document.querySelector('.quota-settings-open').click(); await wait(80);
      const choice=[...panel().querySelectorAll('[data-quota-focus^="centre:"]')];
      facts.choices=choice.map(b=>b.textContent); facts.pressed=choice.filter(b=>b.getAttribute('aria-pressed')==='true').map(b=>b.textContent);
      choice.find(b=>b.dataset.quotaFocus==='centre:both').click(); await wait(400);
      facts.posted=posted();
    """

    def test_both_shows_two_numbers_and_the_choice_saves(self):
        fixture = with_view(with_accounts(), centre="both")
        f = page(fixture, self.FACTS)
        self.assertEqual(f["main"], {"num": "94 \u00b7 0", "unit": "wk \u00b7 5h"})
        self.assertTrue(any("\u00b7" in d for d in f["dials"]), f["dials"])
        self.assertEqual(f["choices"], ["Fuller", "Weekly", "5-hour", "Both"])
        self.assertEqual(f["pressed"], ["Both"])
        self.assertEqual(f["posted"].get("quotas.centre"), "both")

    def test_the_default_names_the_fuller_limit(self):
        f = page(with_view(with_accounts()), self.FACTS)
        self.assertEqual(f["main"], {"num": "94%", "unit": "wk"})
        self.assertEqual(f["pressed"], ["Fuller"])


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(BROWSER, "no Chromium")
class ModelOnlyOnScreen(unittest.TestCase):
    def test_the_details_name_the_model_limit(self):
        fixture = with_accounts()
        fixture["view"]["quotas"]["accounts"] = [a for a in fixture["view"]["quotas"]["accounts"] if a["id"] != "claude:personal"]
        fixture["view"]["quotas"]["accounts"].append(ModelOnlyLimits().snapshot()["accounts"][0])
        f = page(with_view(fixture), """
          const g=document.querySelector('.quota-main [data-quota-id="claude:personal"]');
          facts.num=g.querySelector('.qc-num').textContent; facts.line=g.querySelector('.quota-scoped')?.textContent;
          g.click(); await wait(200);
          facts.note=[...document.querySelectorAll('#quota-open p, #quota-open div')].map(e=>e.textContent).find(t=>t.includes('for one model only'));
        """)
        self.assertEqual(f["num"], "60%")
        self.assertTrue(f["line"].startswith("Fable"), f["line"])
        self.assertTrue((f["note"] or "").startswith("Fable Weekly: for one model only"), f["note"])
