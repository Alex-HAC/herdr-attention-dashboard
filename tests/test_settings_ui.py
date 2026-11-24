"""Settings page: masked ntfy password and the locked Waiting-on-you switch. FIXTURE-BASED."""

import unittest

from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER, run

FAKE_PASSWORD = "fake-pass-7Q2x"
SETUP = {"configured": True, "ready": True, "deep_link": "ntfy://example.invalid/topic",
         "server": "https://ntfy.example.invalid", "username": "phone", "topic": "t", "password": FAKE_PASSWORD}


def main(dom):
    return dom.split('<main id="app"', 1)[1].split("</main>", 1)[0]


@unittest.skipUnless(BROWSER, "no Chromium found")
class SettingsPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = page_fixture()
        cls.data["notifySetup"] = SETUP
        cls.page = main(run(cls.data, "/settings")["dom"])
        cls.revealed = main(run(cls.data, "/settings", script="""
          await new Promise(r => setTimeout(r, 600));
          const b = [...document.querySelectorAll('button')].find(x => x.textContent === 'Show');
          if (b) b.click();
        """)["dom"])

    def test_password_is_masked_by_default(self):
        self.assertNotIn(FAKE_PASSWORD, self.page)
        self.assertIn('data-secret="ntfy-password"', self.page)
        self.assertIn(">Copy<", self.page)
        self.assertIn("Set up phone notifications", self.page, "the deep link stays the main action")

    def test_show_reveals_the_password(self):
        self.assertIn(FAKE_PASSWORD, self.revealed)

    def test_waiting_on_you_switch_is_locked_on(self):
        self.assertIn('class="switch locked"', self.page)
        self.assertIn('aria-label="Waiting on you (always on)"', self.page)
        self.assertIn("Approvals and direct questions. Always sent; the AI may only reword it.", self.page)


if __name__ == "__main__":
    unittest.main()
