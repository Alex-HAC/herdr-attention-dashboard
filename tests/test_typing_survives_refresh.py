"""Polls never rebuild what Alex is typing in (v0.8.4: phone keyboard kept closing)."""

import json
import unittest
import urllib.parse

from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER, run

POLL = """
const poll = async () => { FIXTURE.view = {...FIXTURE.view, version: (FIXTURE.view.version || 0) + 1}; await refresh(); };
const wait = (ms) => new Promise((ok) => setTimeout(ok, ms));
"""

REPLY = POLL + """
await wait(300);
openReply(FIXTURE.ids.ui);
let box = document.querySelector('.easy-composer textarea');
box.focus(); box.value = 'half a sentence'; box.dispatchEvent(new Event('input'));
box.setSelectionRange(4, 9, "backward");
const before = box;
await poll(); await poll();
box = document.querySelector('.easy-composer textarea');
document.body.dataset.reading = JSON.stringify({
  same: box === before, focused: document.activeElement === box, value: box.value, caret: box.selectionStart, end: box.selectionEnd, direction: box.selectionDirection,
});
"""

SETTINGS = POLL + """
await wait(300);
const field = [...document.querySelectorAll('#app input, #app select, #app textarea')]
  .find((el) => !['checkbox', 'radio', 'button', 'submit'].includes(el.type));
field.focus();
await poll(); await poll();
const kept = document.contains(field) && document.activeElement === field;
const deferred = app.renderDeferred;
field.blur(); await wait(50);
document.body.dataset.reading = JSON.stringify({
  found: !!field, kept, deferred, flushed: app.renderDeferred === false,
});
"""


def reading(page):
    raw = page["dom"].split('data-reading="', 1)[1].split('"', 1)[0]
    return json.loads(raw.replace("&quot;", '"').replace("&amp;", "&"))


@unittest.skipUnless(BROWSER, "no Chromium found")
class TypingSurvivesRefresh(unittest.TestCase):
    def test_reply_text_and_focus_survive_polls(self):
        data = page_fixture()
        agent = urllib.parse.quote(data["ids"]["ui"], safe="")
        for route in ("/g/" + agent, "/a/" + agent):
            with self.subTest(route=route):
                r = reading(run(data, route, 390, 844, script=REPLY))
                self.assertTrue(r["same"], "the textarea was not rebuilt by a poll")
                self.assertTrue(r["focused"])
                self.assertEqual(r["value"], "half a sentence")
                self.assertEqual(r["caret"], 4)
                self.assertEqual(r["end"], 9)
                self.assertEqual(r["direction"], "backward")

    def test_focused_settings_field_survives_a_view_change(self):
        r = reading(run(page_fixture(), "/settings", 390, 844, script=SETTINGS))
        self.assertTrue(r["found"])
        self.assertTrue(r["kept"], "focused field stays in the page and focused")
        self.assertTrue(r["deferred"])
        self.assertTrue(r["flushed"], "the deferred render runs on focusout")

    def test_passphrase_focus_range_and_login_keep_document(self):
        data = page_fixture()
        data["loginFirst"] = True
        r = reading(run(data, "/n", 412, 915, script=POLL + """
          const doc = document.documentElement;
          const box = document.querySelector('#passphrase');
          box.focus(); box.value = 'fixture passphrase'; box.setSelectionRange(2, 8);
          const recognized = typingInPage();
          await refresh(); await refresh();
          const kept = document.activeElement === box && box.selectionStart === 2 && box.selectionEnd === 8;
          await login({preventDefault() {}}); await wait(100);
          await poll();
          document.body.dataset.reading = JSON.stringify({recognized, kept,
            same: document.documentElement === doc, route: route().name, hidden: document.querySelector('#login').hidden});
        """))
        for key in ("recognized", "kept", "same", "hidden"):
            self.assertTrue(r[key], key)
        self.assertEqual(r["route"], "notifications")

    def test_unfocused_open_modal_and_settings_form_keep_nodes(self):
        for kind in ("reply", "ask"):
            with self.subTest(kind=kind):
                r = reading(run(page_fixture(), "/", 412, 915, script=POLL + f"""
                  {'openReply' if kind == 'reply' else 'openAsk'}(FIXTURE.ids.ui, 'quoted text'{',null,null,{steer:true}' if kind == 'reply' else ''});
                  const modal = document.querySelector('#composer .modal');
                  const box = modal.querySelector('textarea'); box.value = 'draft';
                  box.dispatchEvent(new Event('input')); box.focus(); box.setSelectionRange(1, 3);
                  renderComposer();
                  const editing = document.activeElement === box && box.selectionStart === 1 && box.selectionEnd === 3;
                  box.blur(); await poll(); await poll(); renderComposer();
                  document.body.dataset.reading = JSON.stringify({{editing,
                    same: document.querySelector('#composer .modal') === modal && modal.querySelector('textarea') === box,
                    value: box.value, quote: modal.querySelector('blockquote').textContent}});
                """))
                self.assertTrue(r["editing"])
                self.assertTrue(r["same"])
                self.assertEqual(r["value"], "draft")
                self.assertEqual(r["quote"], "quoted text")
        r = reading(run(page_fixture(), "/settings", 412, 915, script=POLL + """
          const form = document.querySelector('#app > div');
          const details = form.querySelector('details'); if (details) details.open = true;
          const field = form.querySelector('select'); field.value = field.options[1].value;
          field.blur(); await poll(); await poll();
          document.body.dataset.reading = JSON.stringify({same: form === document.querySelector('#app > div'),
            field: form.contains(field), open: !details || details.open});
        """))
        self.assertTrue(all(r.values()), r)

    def test_inspector_filter_and_transcript_selection_survive_poll(self):
        data = page_fixture()
        identity = urllib.parse.quote(data["ids"]["ui"], safe="")
        r = reading(run(data, "/g/" + identity, 412, 915, script=POLL + """
          await loadDetail(FIXTURE.ids.ui);
          document.querySelector('[data-door=links]').click();   // the relations editor is on Links
          const sheet = graph.shell.sheetBody.firstChild;
          const editor = sheet.querySelector('details.editor'); editor.open = true;
          const input = editor.querySelector('input[aria-label="Team"]');
          input.value = 'filter-like draft'; input.focus(); input.setSelectionRange(2, 7);
          await poll(); render(true);
          const editing = document.activeElement === input && input.selectionStart === 2 && input.selectionEnd === 7;
          input.blur(); await wait(20);
          const host = sheet.querySelector('[data-selectable]') || sheet;
          // Use the real selectable transcript surface when available.
          if (!host.hasAttribute('data-selectable')) { host.dataset.selectable = 'conversation'; host.dataset.agentIdentity = FIXTURE.ids.ui; }
          const text = document.createTextNode('Selected transcript text for the summary agent'); host.append(text);
          const range = document.createRange(); range.setStart(text, 9); range.setEnd(text, 24);
          const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
          const quote = String(selection);
          await poll(); await poll(); render(true); await wait(100);
          const kept = String(selection) === quote && selection.anchorNode === text;
          document.querySelector('#select-bar button').click();
          document.body.dataset.reading = JSON.stringify({editing, kept,
            same: graph.shell.sheetBody.firstChild === sheet && sheet.contains(editor),
            quote: app.composer.quote, expected: quote, modal: !document.querySelector('#composer').hidden});
        """))
        for key in ("editing", "kept", "same", "modal"):
            self.assertTrue(r[key], key)
        self.assertEqual(r["quote"], r["expected"])

    def test_open_inspector_and_its_draft_survive_the_agent_exiting(self):
        data=page_fixture()
        identity=urllib.parse.quote(data["ids"]["ui"],safe="")
        r=reading(run(data,"/g/"+identity,412,915,script=POLL+"""
          await loadDetail(FIXTURE.ids.ui);
          document.querySelector('[data-door=links]').click();
          const sheet=graph.shell.sheet, root=graph.shell.sheetBody.firstChild;
          const editor=root.querySelector('details.editor');editor.open=true;
          const field=editor.querySelector('input[aria-label="Team"]');
          field.value='draft';field.focus();field.setSelectionRange(1,4);
          FIXTURE.view.agents.find(a=>a.identity===FIXTURE.ids.ui).present=false;
          await poll();render(true);
          const focused=document.activeElement===field && field.selectionStart===1 && field.selectionEnd===4;
          field.blur();await wait(100);await poll();
          const same=root===graph.shell.sheetBody.firstChild && root.contains(field), open=!sheet.hidden && editor.open, draft=field.value;
          document.querySelector('.screen-back').click();   // the gone banner is on the status card
          document.body.dataset.reading=JSON.stringify({focused, same, open, draft,
            gone:graph.shell.sheetBody.textContent.includes('No longer running')});
        """))
        for key in ("focused","same","open","gone"):
            self.assertTrue(r[key],key)
        self.assertEqual(r["draft"],"draft")

    def test_no_full_page_navigation_in_shipped_scripts(self):
        from tests.web_harness import WEB
        import re
        for name in ("app.js", "graph.js"):
            source = (WEB / name).read_text()
            # v0.9.7.5: the one allowed reload is reloadIfUpgraded's default (after an upgrade,
            # never while typing; tests/test_reload_on_upgrade.py).
            source = source.replace("reload = () => location.reload()", "", 1)
            self.assertIsNone(re.search(r"location\s*\.\s*(reload|replace|assign)\s*\(|location(?:\.href)?\s*=", source))


if __name__ == "__main__":
    unittest.main()
