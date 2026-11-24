"""Passive reporting fallback: evidence, vetoes, promotion, privacy and rendering."""
import html
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from herdr_attention.collector import Collector
from herdr_attention.inference import EvidenceReader, Inferences, select
from herdr_attention.launcher import plan
from herdr_attention.relations import Registry, RelationError, State
from herdr_attention.store import Store
from tests.helpers import FakeHerdr, agent, settings, workspaces
from tests.trace_fixtures import claude_trace, codex_trace, sid
from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER, run

A, B, C = 'claude:' + sid('infer-lead'), 'codex:' + sid('infer-worker'), 'codex:' + sid('infer-peer')


class Evidence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.briefs = self.root / 'recovery/briefs'
        self.briefs.mkdir(parents=True)
        self.common = self.briefs / 'night-common.md'
        self.common.write_text('# Rules\n\nCoordinator: Fable in Herdr pane w1:p1.\n')
        self.brief = self.briefs / 'night-worker.md'
        self.brief.write_text('Read `night-common.md`.\n')
        self.parent_trace = claude_trace(self.root, sid('infer-lead'), 1000, [{'user': 'Coordinate this run', 'reply': []}])
        self.worker_trace = codex_trace(self.root, sid('infer-worker'), 2000, [], messages=[
            ('user', '<environment_context>injected metadata</environment_context>'),
            ('user', f'You are night worker m1. Read {self.brief} and the files it names.')])
        herdr = FakeHerdr([agent('w1:p1', session=sid('infer-lead'), name='Fable'),
                           agent('w1:p2', session=sid('infer-worker'), kind='codex', name='m1')], workspaces('w1'))
        self.collector = Collector(db_path=self.root / 'state.db', client=herdr, settings=settings(
            traces={'claude_dirs': [str(self.root / 'claude')], 'codex_dirs': [str(self.root / 'codex')]}))
        self.addCleanup(self.collector.store.close)
        self.collector.handle_snapshot()
        self.collector.relations.set_label(A, 'role', 'coordinator', at=1000, provenance='manual', actor='cli')

    def links(self):
        self.collector.poll_inference()
        return [l for l in self.collector.view(local=True)['links'] if l['kind'] == 'reports_to']

    def declare(self, provenance='manual', parent=A):
        self.collector.relations.link(B, 'reports_to', parent, at=2100, provenance=provenance, actor='cli')

    def test_nested_first_prompt_brief_infers_with_stored_evidence_and_render_flags(self):
        link, = self.links()
        self.assertEqual((link['from'], link['to'], link['provenance']), (B, A, ['inferred']))
        self.assertTrue(link['dashed'])
        self.assertTrue(link['inferred'])
        self.assertEqual(link['confidence'], .97)
        self.assertIn('night-common.md:3', link['source'])
        self.assertIn('inferred: ', link['source'])
        self.assertNotIn('You are night worker', link['source'])
        self.assertEqual(self.collector.relations_overview()['links'][0]['provenance'], ['inferred'])
        persisted = Inferences(self.collector.store).effective(self.collector.relations.state(), {A, B})
        self.assertEqual(persisted[0]['evidence'], link['evidence'])

    def test_declarations_immediately_override_fallback_even_to_another_parent(self):
        for provenance in ('manual', 'registered'):
            with self.subTest(provenance=provenance):
                self.links()
                self.declare(provenance, C)
                links = self.collector.view(local=True)['links']
                self.assertFalse(any(l.get('inferred') for l in links))
                self.assertEqual([l['provenance'] for l in links], [[provenance]])
                self.collector.relations.unlink(B, 'reports_to', C, at=2200, provenance='manual', actor='test')

    def test_team_link_promotes_and_stays_solid(self):
        self.links()
        self.collector.relation_action({'action': 'link', 'subject': 'w1:p2', 'kind': 'reports_to', 'object': 'w1:p1'}, local=True)
        link, = self.links()
        self.assertEqual(link['provenance'], ['manual'])
        self.assertFalse(link['dashed'])
        self.assertFalse(link['inferred'])

    def test_unlink_is_permanent_for_worker_after_new_evidence_and_reopen(self):
        self.links()
        self.collector.relation_action({'action': 'unlink', 'subject': B, 'kind': 'reports_to', 'object': A}, local=True)
        self.common.write_text(self.common.read_text() + '\nUpdated evidence.\n')
        self.assertEqual(self.links(), [])
        reopened = Store(self.root / 'state.db')
        self.addCleanup(reopened.close)
        self.assertIn(B, Inferences(reopened).suppressed())
        # Explicit declarations can always override a permanent inference veto.
        self.declare()
        self.assertEqual(self.links()[0]['provenance'], ['manual'])

    def test_unlink_declared_also_vetoes_fallback(self):
        self.declare()
        self.collector.relation_action({'action': 'unlink', 'subject': B, 'kind': 'reports_to', 'object': A}, local=True)
        self.assertEqual(self.links(), [])

    def test_unlink_wrong_endpoint_does_not_suppress(self):
        self.links()
        with self.assertRaises(RelationError):
            self.collector.relation_action({'action': 'unlink', 'subject': A, 'kind': 'reports_to', 'object': B}, local=True)
        self.assertEqual(self.collector.inferences.suppressed(), set())

    def test_ignore_declarations_is_read_only_and_does_not_use_ignored_link_as_evidence(self):
        self.declare()
        before = self.collector.store._db.total_changes
        check = self.collector.inference_check(['w1:p2'])
        self.assertEqual([(l['from'], l['to']) for l in check['links']], [(B, A)])
        self.assertEqual(check['ignored_declarations'], [B])
        self.assertEqual(self.collector.store._db.total_changes, before)
        self.assertIn((B, 'reports_to', A), self.collector.relations.state().links)
        self.assertEqual(self.collector.inference_check()['links'], [])

    def test_no_parent_when_only_names_tabs_or_directory_match(self):
        self.common.write_text('Ordinary common worker rules; no coordinator.\n')
        self.assertEqual(self.links(), [])

    def test_explicit_brief_coordinator_does_not_require_manual_role_or_any_link(self):
        self.collector.relations.clear_label(A, 'role', 'coordinator', at=1100, provenance='manual', actor='test')
        link, = self.links()
        self.assertEqual((link['to'], link['confidence']), (A, .97))
        self.assertEqual(self.collector.relations.labels_for(A)['role'], [], 'roles are never inferred')

    def test_exact_recent_coordinator_reference_is_specific_evidence(self):
        self.common.write_text('Ordinary rules.\n')
        # Parent started first and mentions this exact brief near worker start.
        claude_trace(self.root, sid('infer-lead'), 1000, [{'user': 'Run', 'reply': [f'Worker brief: {self.brief}']}])
        link, = self.links()
        self.assertEqual(link['confidence'], .90)
        self.assertIn('exact brief', link['source'])
        self.parent_trace.write_text(self.parent_trace.read_text().replace(str(self.brief), str(self.briefs / 'other.md')))
        self.assertEqual(self.links(), [])

    def test_recent_declared_sibling_requires_shared_common_file(self):
        self.common.write_text('Ordinary rules.\n')
        peer = self.briefs / 'peer.md'
        peer.write_text('Read `night-common.md`.\n')
        codex_trace(self.root, sid('infer-peer'), 1800, [], messages=[('user', f'Read {peer}')])
        self.collector.engine.apply_agent(agent('w1:p3', session=sid('infer-peer'), kind='codex'))
        self.collector.relations.link(C, 'reports_to', A, at=1900, provenance='registered', actor='helper')
        inferred, = [l for l in self.links() if l.get('inferred')]
        self.assertEqual(inferred['confidence'], .85)
        peer.write_text('Independent task in same directory.\n')
        self.assertFalse(any(l.get('inferred') for l in self.links()))

    def test_conflicting_coordinators_are_not_guessed(self):
        codex_trace(self.root, sid('infer-peer'), 1500, [], messages=[('user', 'Coordinate')])
        self.collector.engine.apply_agent(agent('w1:p3', session=sid('infer-peer'), kind='codex'))
        self.collector.relations.set_label(C, 'role', 'coordinator', at=1500, provenance='manual', actor='cli')
        self.common.write_text(self.common.read_text() + '\nCoordinator: another lead in w1:p3.\n')
        self.assertEqual(self.links(), [])

    def test_old_worker_cannot_attach_to_a_new_coordinator_in_reused_pane(self):
        claude_trace(self.root, sid('infer-lead'), 3000, [{'user': 'New run', 'reply': []}])
        self.assertEqual(self.links(), [])

    def test_collision_or_unwatched_endpoint_cannot_infer(self):
        self.collector.engine.agents[B]['session_collision'] = True
        self.assertEqual(self.links(), [])
        self.collector.engine.agents[B]['session_collision'] = False
        self.collector.engine.agents[A]['workspace_id'] = 'w2'
        self.assertEqual(self.links(), [])
        self.assertNotIn(A, self.collector.inference_reader._cache)

    def test_disabled_trace_or_inference_never_reads(self):
        for setting in ('traces', 'inference'):
            self.collector.settings.values[setting]['enabled'] = False
            with patch.object(self.collector.inference_reader, 'candidates', side_effect=AssertionError('must not read')):
                self.collector.poll_inference()
                self.assertFalse(self.collector.inference_check()['enabled'])
            self.collector.settings.values[setting]['enabled'] = True

    def test_cached_link_disappears_immediately_including_historical_details(self):
        self.links()
        self.collector.engine.agents[B]['present'] = False
        self.assertFalse(any(l.get('inferred') for l in self.collector.engine.agent_detail(B)['connections']))
        self.assertFalse(any(l.get('inferred') for l in self.collector.engine.view()['links']))

    def test_spawn_unlink_does_not_veto_reporting_fallback(self):
        self.collector.relations.link(B, 'spawned_by', A, at=1900, provenance='registered', actor='helper')
        self.collector.relation_action({'action': 'unlink', 'subject': B, 'kind': 'spawned_by', 'object': A}, local=True)
        self.assertTrue(self.links()[0]['inferred'])
        self.assertEqual(self.collector.inferences.suppressed(), set())

    def test_brief_symlink_and_parent_escape_are_not_read(self):
        outside = self.root / 'private.md'
        outside.write_text('Coordinator: Fable in w1:p1.\n')
        link = self.briefs / 'escape.md'
        link.symlink_to(outside)
        self.brief.write_text('Read `escape.md` and `../../private.md`.\n')
        self.assertEqual(self.links(), [])

    def test_bounded_reader_skips_malformed_or_oversized_briefs(self):
        self.common.write_text('Coordinator: Fable in w1:p1.\n' + 'x' * 66000)
        with self.worker_trace.open('a') as f:
            f.write('[]\n{"type":"response_item","payload":"malformed"}\n')
        self.assertEqual(self.links(), [])

    def test_fallback_cannot_close_declared_cycle(self):
        self.collector.relations.link(A, 'reports_to', B, at=2000, provenance='manual', actor='cli')
        self.assertFalse(any(l.get('inferred') for l in self.links()))


class Selection(unittest.TestCase):
    def test_inferred_cycle_and_self_link_are_rejected(self):
        cs = [{'from': s, 'to': o, 'confidence': .9, 'evidence': ['brief evidence']} for s, o in [(A, B), (B, C), (C, A), (C, C)]]
        chosen = select(cs, State(), {A, B, C})
        self.assertEqual(len(chosen), 2)

    def test_suppression_follows_identity_upgrade(self):
        store = Store(':memory:')
        self.addCleanup(store.close)
        inference = Inferences(store)
        inference.suppress('term:x', 1, 'cli')
        store.rename_identity('term:x', B)
        self.assertIn(B, inference.suppressed())

    def test_global_rules_pointer_all_providers_with_and_without_brief(self):
        with tempfile.TemporaryDirectory() as tmp:
            brief = Path(tmp) / 'brief.md'
            brief.write_text('A task')
            for provider in ('codex', 'claude', 'cursor', 'agy'):
                for path in (None, str(brief)):
                    with self.subTest(provider=provider, brief=path):
                        steps = plan(name='worker', kind=provider, env={'HERDR_ENV': '1', 'HERDR_PANE_ID': 'w1:p1'},
                                     config_dir='/tmp/claude' if provider == 'claude' else None,
                                     direction='right', cwd=None, agent_args=['Do the work'], brief=path)
                        self.assertTrue(steps['args'][-1].startswith('Global rules: read ~/.config/agents/AGENTS.md\n'))
                        self.assertIn('Do the work', steps['args'][-1])
                        if path:
                            self.assertIn(path, steps['args'][-1])


@unittest.skipUnless(BROWSER, 'Chromium needed')
class Rendering(unittest.TestCase):
    def test_phone_inferred_edges_stay_dashed_when_selected_and_declared_stay_solid(self):
        fixture = page_fixture()
        first = fixture['view']['links'][0]
        first.update(kind='reports_to', provenance=['inferred'], inferred=True, dashed=True,
                     confidence=.97, source='inferred: fixture brief names coordinator')
        second = fixture['view']['links'][1]
        second.update(kind='reports_to', provenance=['manual'], inferred=False, dashed=False)
        from urllib.parse import quote
        script = '''
          const links = [...document.querySelectorAll('svg.graph path.edge.reports_to')].map(p => ({
            inferred: p.classList.contains('inferred'), active: p.classList.contains('active'),
            dash: getComputedStyle(p).strokeDasharray, opacity: getComputedStyle(p).opacity, title: p.textContent
          }));
          document.body.dataset.inferredProbe = JSON.stringify(links);
        '''
        for width in (390, 1280):
            result = run(fixture, '/graph/' + quote(first['from'], safe=''), width, 900, script=script)
            probe = re.search(r'data-inferred-probe="([^"]+)"', result['dom'])
            self.assertIsNotNone(probe)
            links = json.loads(html.unescape(probe[1]))
            inferred, = [l for l in links if l['inferred']]
            self.assertTrue(inferred['active'])
            self.assertNotEqual(inferred['dash'], 'none')
            self.assertLess(float(inferred['opacity']), 1)
            self.assertIn('inferred: fixture brief', inferred['title'])
            self.assertTrue(any(l['dash'] == 'none' for l in links if not l['inferred']))
            self.assertEqual(result['probe']['overflowing'], [])
