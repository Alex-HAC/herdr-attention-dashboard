"""v0.9.8.9 Settings > Phone alerts on a 390 px phone: every kind, its loudness, away-only,
snooze, quiet hours, rate limit, workspaces and the test button; no secrets on the page."""
import unittest

from tests.test_typing_survives_refresh import reading
from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER, run


@unittest.skipUnless(BROWSER, "no Chromium found")
class PhoneAlertsSection(unittest.TestCase):
    def test_section_at_390(self):
        r = reading(run(page_fixture(), "/settings", 390, 2400, script="""
          const sec=document.querySelector('#settings-phone_alerts');
          const rows=[...sec.querySelectorAll('.pa-type')];
          document.body.dataset.reading=JSON.stringify({
            first:document.querySelector('section[id^="settings-"]').id,
            types:rows.map(r=>r.dataset.type),
            levels:rows.filter(r=>r.querySelector('.pa-options')).map(r=>[r.dataset.type,
              r.querySelector('[aria-pressed="true"]')?.textContent]),
            away:rows.map(r=>!!r.querySelector('.pa-away input')),
            snooze:[...sec.querySelectorAll('[aria-label^="Snooze"]')].map(b=>b.textContent),
            test:!![...sec.querySelectorAll('button')].find(b=>b.textContent==='Send test notification'),
            quiet:sec.querySelector('[data-setting="notify_tiers.quiet_hours"]')?.checked,
            rate:sec.querySelector('[data-setting="notify.rate_count"] output')?.textContent,
            wide:Math.max(...[...sec.querySelectorAll('*')].map(e=>e.getBoundingClientRect().right)),vw:window.innerWidth,
            text:sec.innerText});
        """))
        self.assertEqual(r["first"], "settings-phone_alerts")
        self.assertEqual(r["types"], ["waiting_on_you", "might_look", "fyi_finished", "coordinator_milestone",
                                      "waiting_on_coordinator", "quota_stop", "visuals"])
        self.assertEqual(dict(r["levels"]), {"waiting_on_you": "Urgent", "might_look": "Normal", "fyi_finished": "Quiet"})
        self.assertEqual(r["away"][:3], [False, True, True], "asks always come; the others can wait")
        self.assertEqual(r["snooze"], ["1 hour", "Until 08:00"])
        self.assertTrue(r["test"])
        self.assertIs(r["quiet"], False, "the test settings switch quiet hours off")
        self.assertEqual(r["rate"], "0 alerts", "the test settings switch the limit off")
        self.assertLessEqual(r["wide"], r["vw"], "nothing wider than the phone")
        self.assertNotIn("Topic:", r["text"])


if __name__ == "__main__":
    unittest.main()
