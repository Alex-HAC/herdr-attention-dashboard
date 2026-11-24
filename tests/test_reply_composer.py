"""Reply safety (v0.8.0 slice 1.1, GR-3 hint): what is shown is what is sent."""

import json
import unittest
import urllib.parse

from herdr_attention import reply as reply_mod
from tests.test_replies import agent, service
from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER, run


class PayloadIsWhatIsSent(unittest.TestCase):
    def check(self, text, quote):
        svc, _, sender = service()
        prev = svc.preview(agent(), text, quote)
        svc.submit(agent(), text=text, quote=quote, when="now", interrupt=False, confirm=True)
        self.assertEqual(sender.calls[-1][1], prev["payload"])
        self.assertEqual(prev["length"], len(prev["payload"]))
        self.assertEqual(prev["max_text"], reply_mod.MAX_TEXT)
        return prev["payload"]

    def test_without_quote(self):
        self.assertEqual(self.check("  ship it  ", None), "ship it")
        self.assertEqual(self.check("ship it", "   "), "ship it")

    def test_with_quote(self):
        self.assertEqual(self.check("yes", "line one\n\nline two"), "> line one\n>\n> line two\n\nyes")

    def test_over_limit_is_previewed_then_refused_never_truncated(self):
        svc, _, sender = service()
        quote = "q" * 50
        text = "x" * (reply_mod.MAX_TEXT - 10)
        prev = svc.preview(agent(), text, quote)
        self.assertTrue(prev["too_long"])
        with self.assertRaises(ValueError):
            svc.submit(agent(), text=text, quote=quote, when="now", interrupt=False, confirm=True)
        self.assertEqual(sender.calls, [])
        self.assertFalse(svc.preview(agent(), text, None)["too_long"])

    def test_blocked_warning_says_answer_in_terminal(self):
        svc, _, _ = service()
        prev = svc.preview(agent(status="blocked"), "yes")
        self.assertIn("Answer this in the terminal", prev["warning"])


COMPOSER = """
const realFetch = window.fetch;
window.fetch = async (path, init) => {
  if (String(path).startsWith('/api/replies') && init && init.method === 'POST') {
    const b = JSON.parse(init.body);
    const quote = (b.quote || '').trim();
    const payload = quote ? quote.split('\\n').map(l => l.trim() ? '> ' + l : '>').join('\\n') + '\\n\\n' + b.text.trim() : b.text.trim();
    const max = FIXTURE.view.features.reply_max_text;
    return {ok: true, status: 200, json: async () => ({payload, length: payload.length, max_text: max, too_long: payload.length > max})};
  }
  return realFetch(path, init);
};
const wait = (ms) => new Promise((ok) => setTimeout(ok, ms));
const id = FIXTURE.ids.ui;
const hint = !!document.querySelector('.terminal-hint');
openReply(id, 'Should I bump the schema?',null,null,{steer:true});
const root = document.getElementById('composer');
const radios = [...root.querySelectorAll('input[type=radio]')];
const box = root.querySelector('textarea');
box.value = 'Yes, bump it'; box.dispatchEvent(new Event('input'));
const before = root.querySelector('.reply-confirm').disabled;
await wait(400);
const shown = root.querySelector('.reply-payload').textContent;
const okDisabled = root.querySelector('.reply-confirm').disabled;
root.querySelector('.reply-include-box').click();
await wait(400);
const noQuote = root.querySelector('.reply-payload').textContent;
const box2 = root.querySelector('textarea');
box2.value = 'x'.repeat(FIXTURE.view.features.reply_max_text + 1); box2.dispatchEvent(new Event('input'));
await wait(400);
document.body.dataset.reading = JSON.stringify({
  hint, names: radios.map(r => r.name), nowDisabled: radios.find(r => r.value === 'now').disabled,
  reason: root.querySelector('.reply-when').textContent, warning: (root.querySelector('.warn-send') || {}).textContent || '',
  before, shown, okDisabled, noQuote,
  over: root.querySelector('.reply-count').classList.contains('over'),
  overDisabled: root.querySelector('.reply-confirm').disabled,
  label: root.textContent.includes('Exact text that will be sent'),
});
"""


@unittest.skipUnless(BROWSER, "no Chromium found")
class ComposerInBrowser(unittest.TestCase):
    def test_reply_composer_is_honest(self):
        data = page_fixture()
        data['view']['links'] = []
        for a in data['view']['agents']: a['reports_to'] = None; a.pop('reply_target',None)
        page = run(data, "/a/" + urllib.parse.quote(data["ids"]["ui"], safe=""), 390, 900, script=COMPOSER)
        raw = page["dom"].split('data-reading="', 1)[1].split('"', 1)[0]
        r = json.loads(raw.replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">"))
        self.assertFalse(r["hint"], "plain Reply is now available at the top of the page")
        self.assertEqual(r["names"], ["when", "when"], "one radio group")
        self.assertTrue(r["nowDisabled"])
        self.assertIn("not possible", r["reason"])
        self.assertIn("Answer this in the terminal", r["warning"])
        self.assertTrue(r["before"], "no confirm before the preview arrives")
        self.assertEqual(r["shown"], "> Should I bump the schema?\n\nYes, bump it")
        self.assertFalse(r["okDisabled"])
        self.assertEqual(r["noQuote"], "Yes, bump it")
        self.assertTrue(r["over"])
        self.assertTrue(r["overDisabled"])
        self.assertFalse(r["label"], "old misleading label is gone")


if __name__ == "__main__":
    unittest.main()
