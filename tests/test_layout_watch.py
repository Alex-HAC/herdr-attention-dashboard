"""Today's crowded layout, quiet findings, privacy, and positive completion proof."""
import contextlib
import io
import json
from pathlib import Path
import shlex
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from herdr_attention.collector import Collector
from herdr_attention.cli import build_parser, cmd_layout, cmd_doctor
from herdr_attention.layout import findings, unique_name
from herdr_attention.layout_evidence import CompletionEvidence, candidate
from herdr_attention.launcher import plan, preview
from tests.helpers import FakeHerdr, Clock, agent, settings, workspaces


def today():
    agents = [agent(f'w4:p{i}', session=f's{i}', name=('codex-b1' if i == 1 else 'codex-k1' if i == 2 else None),
                    kind='codex', title='Continue the task' if i < 4 else f'Implement slice {i}') for i in range(1, 8)]
    extra = agent('w4:p8', session='extra', name='api-worker', title='API checks', kind='codex')
    extra['tab_id'] = 'w4:t2'
    hidden = agent('w9:p1', session='secret', name=None, title='Continue SECRET')
    client = FakeHerdr([*agents, extra, hidden], workspaces('w4', 'w9'))
    client.tabs = lambda: [{'tab_id': 'w4:t1', 'workspace_id': 'w4', 'label': 'codex'},
                           {'tab_id': 'w4:t2', 'workspace_id': 'w4', 'label': '1'},
                           {'tab_id': 'w9:t1', 'workspace_id': 'w9', 'label': 'SECRET'}]
    c = Collector(db_path=':memory:', settings=settings(watch={'enabled_workspaces': ['w4']}), client=client)
    c.handle_snapshot()
    return c


class Watcher(unittest.TestCase):
    def setUp(self):
        self.c = today()
        self.addCleanup(self.c.store.close)

    def test_todays_seven_panes_unnamed_generic_and_numeric_tab(self):
        before = self.c.engine.view()['attention_count']
        events = self.c.store.open_events()
        view = self.c.view(local=False)
        hints = view['layout']['findings']
        self.assertEqual(sum(h['kind'] == 'over_limit' for h in hints), 1)
        self.assertEqual(sum(h['kind'] == 'unnamed_agent' for h in hints), 5)
        self.assertEqual(sum(h['kind'] == 'generic_title' for h in hints), 3)
        self.assertEqual(sum(h['kind'] == 'default_tab_label' for h in hints), 1)
        self.assertIn('7 panes (limit 4)', next(h['message'] for h in hints if h['kind'] == 'over_limit'))
        self.assertEqual(view['attention_count'], before)
        self.assertEqual(self.c.store.open_events(), events)
        self.assertNotIn('SECRET', json.dumps(hints))
        ws = view['workspaces'][0]
        self.assertEqual(len(ws['layout_hints']), len(hints))
        self.assertTrue(all(t['layout_hints'] for t in ws['tabs']))
        commands = [shlex.split(cmd) for h in hints for cmd in h['commands']]
        self.assertTrue(any(cmd[:3] == ['herdr', 'agent', 'rename'] for cmd in commands))
        self.assertIn(['herdr', 'tab', 'rename', 'w4:t2', 'codex-2'], commands)
        names = [cmd[-1] for cmd in commands if cmd[:3] == ['herdr', 'agent', 'rename']]
        self.assertEqual(len(set(names)), 5)
        self.assertFalse(any(c[0] in ('prompt', 'rename', 'focus', 'close') for c in self.c.client.calls))

    def test_shells_count_and_unwatch_hides_previously_collected_findings(self):
        self.c.client.shell_panes = [{'pane_id': 'w4:p9', 'workspace_id': 'w4', 'tab_id': 'w4:t1'}]
        self.c.handle_snapshot()
        hints = self.c.view(local=False)['layout']['findings']
        self.assertIn('8 panes', next(h['message'] for h in hints if h['kind'] == 'over_limit'))
        self.c.settings.set_override('watch.enabled_workspaces', [])
        self.assertEqual(self.c.view(local=False)['layout']['findings'], [])

    def test_cli_prints_same_findings_without_mutation(self):
        hints = self.c.view(local=False)['layout']['findings']
        client = SimpleNamespace(get=lambda path: {'findings': hints})
        args = build_parser().parse_args(['layout', 'check', '--workspace', 'w4'])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(cmd_layout(args, client), 0)
        self.assertIn('herdr agent rename w4:p3', output.getvalue())
        self.assertIn('herdr tab rename w4:t2 codex-2', output.getvalue())
        self.assertIn('no changes made', output.getvalue())

    def test_doctor_reports_hints_without_failing_health(self):
        import time
        view = self.c.view(local=True)
        view['collector']['herdr'].update(last_reconcile=time.time(), reconcile_error=None)
        view['presence']['state'] = 'present'
        with tempfile.NamedTemporaryFile() as config, patch('herdr_attention.cli.load_settings', return_value=settings()), \
             patch('herdr_attention.herdr.HerdrClient') as herdr, patch('herdr_attention.cli.Client') as client, \
             patch('herdr_attention.secrets.Keyring') as keyring:
            herdr.return_value.ping.return_value = {'version': '0.9.1', 'protocol': 22}
            client.return_value.get.return_value = view
            keyring.return_value.available.return_value = True
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = cmd_doctor(SimpleNamespace(config=config.name))
            self.assertEqual(result, 0)
            self.assertIn('[--] layout - 10 quiet suggestion(s)', output.getvalue())
            self.assertIn('7 panes (limit 4)', output.getvalue())


class NamesAndFocus(unittest.TestCase):
    def test_slug_and_numeric_dedupe_stay_bounded(self):
        self.assertEqual(unique_name('Réview / API', {'review-api', 'review-api-2'}), 'review-api-3')
        self.assertRegex(unique_name('123 / ??'), r'^[a-z][a-z0-9_-]*$')
        original = 'a' * 32
        self.assertEqual(unique_name(original, {original}), 'a' * 30 + '-2')
        self.assertLessEqual(len(unique_name(original, {original})), 32)
        args = build_parser().parse_args(['team', 'start', '--role', 'backend', '--name', 'My API', '--focus'])
        self.assertEqual(args.name_override, 'My API')
        self.assertTrue(args.focus)

    def test_every_placement_preserves_focus_unless_explicitly_opted_in(self):
        env = {'HERDR_ENV': '1', 'HERDR_PANE_ID': 'w1:p1', 'HERDR_WORKSPACE_ID': 'w1', 'HERDR_TAB_ID': 'w1:t1'}
        for role, tab in (('backend', False), ('backend', True), ('coordinator', False)):
            kwargs = dict(name='Bad Name', kind='codex', env=env, config_dir=None, direction='right', cwd='/tmp',
                          role=role, task='API work', agent_args=[], tab=tab, layout_policy={'max_panes_per_tab': 4})
            steps = plan(**kwargs)
            self.assertIn('--no-focus', steps['split'])
            self.assertNotIn('focus', steps['start'])
            shown = preview(steps)
            self.assertEqual(shown['name'], 'bad-name')
            self.assertEqual(shown['title'], 'bad-name · API work')
            self.assertTrue(shown['placement']['why'])
            explicit = plan(**kwargs, focus=True)
            self.assertIn('--focus', explicit['split'])
            self.assertNotIn('--no-focus', explicit['split'])


class FinishedProof(unittest.TestCase):
    def test_only_existing_report_plus_confirmed_merge_suggests_close(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / 'report.md'
            report.write_text('PR https://github.com/example/repo/pull/12\n')
            identity = 'codex:s1'
            clock = Clock()
            calls = []
            def runner(command, **kwargs):
                calls.append(command)
                return SimpleNamespace(returncode=0, stdout=json.dumps({'state': 'OPEN', 'mergedAt': None}))
            evidence = CompletionEvidence(clock, runner)
            evidence.consider(identity, f'DONE {report}', directory)
            evidence.resolve({identity})
            self.assertEqual(evidence.facts(), {})
            evidence.resolve({identity})
            self.assertEqual(len(calls), 1, 'negative lookups are cached')
            clock.advance(301)
            evidence.runner = lambda *a, **k: SimpleNamespace(returncode=0, stdout=json.dumps({'state':'MERGED','mergedAt':'2026-11-14T00:00:00Z'}))
            evidence.resolve({identity})
            raw = agent('w1:p1', status='done', session='s1', kind='codex', name='worker', title='Real task')
            hints = findings([raw], [], [], {}, completions=evidence.facts())
            self.assertEqual([h['kind'] for h in hints], ['finished_pane'])
            self.assertEqual(hints[0]['commands'], ['herdr pane close w1:p1'])
            raw['agent_status'] = 'working'
            self.assertEqual(findings([raw], [], [], {}, completions=evidence.facts()), [])
            report.unlink()
            self.assertEqual(evidence.facts(), {})
            self.assertIsNone(candidate(f'DONE {report}', directory))
            self.assertEqual(calls[0], ['gh','pr','view','https://github.com/example/repo/pull/12','--json','state,mergedAt'])

    def test_merge_claims_ambiguity_and_unavailable_gh_are_not_proof(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / 'report.md'
            report.write_text('The PR is merged (unverified claim).')
            self.assertIsNone(candidate(f'DONE {report}', directory))
            text = f'DONE {report}\nhttps://github.com/a/b/pull/1 https://github.com/a/b/pull/2'
            self.assertIsNone(candidate(text, directory))
            evidence = CompletionEvidence(runner=lambda *a, **k: SimpleNamespace(returncode=1, stdout=''))
            evidence.consider('codex:s1', f'DONE {report}\nhttps://github.com/a/b/pull/1', directory)
            evidence.resolve({'codex:s1'})
            self.assertEqual(evidence.facts(), {})
            evidence.resolve(set())
            self.assertEqual(evidence.pending, {})
