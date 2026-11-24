"""uiux2 F8: reply sheet on a phone keeps the draft on a backdrop tap; send bar stays in view."""
import json
import unittest
import urllib.parse

from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER, run

SCRIPT = """
const id = FIXTURE.ids.ui;
saveDraft(`steer:${id}`, '');
openReply(id, '',null,null,{steer:true});
const root = document.getElementById('composer');
const backdrop = () => root.querySelector('.modal-backdrop');
backdrop().click();
const emptyClosed = !app.composer;
openReply(id, '',null,null,{steer:true});
const box = root.querySelector('textarea');
box.value = 'Yes, ship it'; box.dispatchEvent(new Event('input'));
backdrop().click();
const keptOpen = !!app.composer && app.composer.text === 'Yes, ship it';
const bar = root.querySelector('.modal > .actions:last-child');
const sticky = bar ? getComputedStyle(bar).position : '';
const modal = root.querySelector('.modal');
const fits = modal.getBoundingClientRect().height <= window.innerHeight;
[...root.querySelectorAll('button')].find(b => b.textContent === 'Cancel').click();
document.body.dataset.reading = JSON.stringify({emptyClosed, keptOpen, sticky, fits, cancelled: !app.composer});
"""


@unittest.skipUnless(BROWSER, "no Chromium found")
class PhoneReplySheet(unittest.TestCase):
    def test_backdrop_keeps_draft_and_send_bar_is_sticky(self):
        data = page_fixture()
        page = run(data, "/a/" + urllib.parse.quote(data["ids"]["ui"], safe=""), 390, 844, script=SCRIPT)
        raw = page["dom"].split('data-reading="', 1)[1].split('"', 1)[0]
        r = json.loads(raw.replace("&quot;", '"').replace("&amp;", "&"))
        self.assertTrue(r["emptyClosed"], "an empty sheet still closes on a backdrop tap")
        self.assertTrue(r["keptOpen"], "a typed reply survives a backdrop tap")
        self.assertEqual(r["sticky"], "sticky")
        self.assertTrue(r["fits"])
        self.assertTrue(r["cancelled"], "Cancel still closes")


if __name__ == "__main__":
    unittest.main()
