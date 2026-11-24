"""v0.9.7.1: the agent page's "Runs on" Account line says where a proven account comes from."""
import unittest

from herdr_attention import account_icons
from tests.icon_fixture import icon_fixture
from tests.test_account_icons_places import PAGE
from tests.test_typing_survives_refresh import reading
from tests.web_harness import BROWSER, run

# Runs on lives on the Usage screen of the details view.
USAGE = "await new Promise(r => setTimeout(r, 400)); document.querySelector('[data-door=usage]').click();"


@unittest.skipUnless(BROWSER, "no Chromium found")
class RunsOnSource(unittest.TestCase):
    def test_a_proven_account_names_its_source_next_to_recorded_at_launch(self):
        data = icon_fixture()
        view = data["view"]
        docs = next(a for a in view["agents"] if a.get("name") == "docs-writer")       # Claude, not started with herdr-spawn
        self.assertFalse((docs.get("launch") or {}).get("account"))
        docs["account_proof"] = {"account": "claude-personal", "source": "config_folder"}
        account_icons.decorate(view, view["settings"]["accounts.icons"])
        r = reading(run(data, f"/g/{docs['identity']}", 390, 844, script=USAGE + PAGE))
        self.assertEqual(r["runs"]["account"]["account"], "claude:personal")
        self.assertTrue(r["runs"]["source"].endswith("Account: from the session's config folder."), r["runs"]["source"])
        lead = next(a for a in view["agents"] if a.get("name") == "web-lead")
        r = reading(run(data, f"/g/{lead['identity']}", 390, 844, script=USAGE + PAGE))
        self.assertTrue(r["runs"]["source"].endswith("Account: recorded at launch."), r["runs"]["source"])


if __name__ == "__main__":
    unittest.main()
