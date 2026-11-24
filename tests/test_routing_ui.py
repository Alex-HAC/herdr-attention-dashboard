"""Quota/sandbox facts and in-process children in the real browser (fixtures only)."""
import unittest
import urllib.parse
import uuid
from tests.web_fixture import build
from tests.web_harness import BROWSER, run


@unittest.skipUnless(BROWSER, 'no Chromium found')
class RoutingCards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = build()
        c = cls.data.pop('collector')
        identity = cls.data['ids']['ui']
        run_id = str(uuid.uuid4())
        c.observations.launch({'run_id': run_id, 'kind': 'codex', 'sandbox': 'fixture test',
                               'parent_session': None, 'parent_pane': 'w1:p1'})
        cls.child = 'codex:fixture-hook-child'
        c.hook_event({'run_id': run_id, 'session': identity, 'parent_session': None, 'parent_pane': 'w1:p1',
                      'event': 'SubagentStart', 'child_session': cls.child})
        c.observations.wall('codex', identity)
        cls.data['view'] = c.view(local=False)
        cls.data['details'][identity] = c.agent_detail(identity)
        cls.data['details'][cls.child] = c.agent_detail(cls.child)
        c.store.close()

    def test_quota_sandbox_and_observed_child_render_without_errors(self):
        identity = self.data['ids']['ui']
        for width in (390, 1280):
            page = run(self.data, '/g/' + urllib.parse.quote(identity, safe=''), width, 844)
            self.assertIn('Out of quota (codex)', page['dom'])
            self.assertIn('Sandbox: fixture test', page['dom'])
            self.assertIn('data-id="' + self.child + '"', page['dom'])
            self.assertEqual(page['probe']['overflowing'], [])
        page = run(self.data, '/g/' + urllib.parse.quote(self.child, safe=''), 390, 844)
        self.assertIn('class="sc-task">In-process subagent', page['dom'])
        self.assertEqual(page['probe']['overflowing'], [])
