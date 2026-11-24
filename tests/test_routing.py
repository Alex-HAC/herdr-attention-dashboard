"""Alex's routing policy: ordered eligibility and account/quota boundaries."""
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from herdr_attention.routing import load, validate, pick, RoutingError

ROOT = Path(__file__).resolve().parent.parent


class Routing(unittest.TestCase):
    def setUp(self):
        self.data = load(ROOT / 'config/routing.toml')
        # Exercise the configured project boundary even from an isolated worktree.
        self.cwd = str(Path(self.data["project"][0]["match"]).expanduser())

    def test_all_roles_resolve_defined_agents_and_validate(self):
        validate(self.data)
        for names in self.data['roles'].values():
            self.assertTrue(all(n in self.data['agents'] for n in names))
        bad = copy.deepcopy(self.data)
        bad['roles']['backend'] = ['not-defined']
        with self.assertRaisesRegex(RoutingError, 'defined agents'):
            validate(bad)

    def test_project_restricts_even_explicit_account(self):
        self.assertEqual(pick(self.data, role='backend', cwd=self.cwd)['account'], 'codex-work')
        self.assertEqual(pick(self.data, role='gruntwork', cwd=self.cwd)['agent'], 'codex-sol')
        # The mechanism, not today's allowance: with this project limited to codex-work
        # (routing.toml may also allow claude-work for a while), Claude picks are refused.
        strict = copy.deepcopy(self.data)
        for project in strict['project']:
            if 'herdr-attention-dashboard' in project['match']:
                project['accounts'] = ['codex-work']
        for kw in ({'role': 'coordinator'}, {'role': 'backend', 'agent': 'opus'}):
            with self.assertRaisesRegex(RoutingError, 'account not allowed'):
                pick(strict, cwd=self.cwd, **kw)
        with self.assertRaises(RoutingError):
            pick(self.data, cwd=self.cwd + '-other', role='backend')
        self.assertEqual(pick(self.data, cwd=self.cwd + '/tests', role='backend')['account'], 'codex-work')

    def unrestricted(self):
        self.data['project'][0]['accounts'] = ['codex-work', 'cursor-work', 'claude-work']

    def test_wall_fallback_premium_only_and_avoid(self):
        self.unrestricted()
        wall = [{'provider': 'codex'}]
        self.assertEqual(pick(self.data, role='backend', cwd=self.cwd, walls=wall)['agent'], 'cursor-sol')
        self.assertEqual(pick(self.data, role='backend', cwd=self.cwd, walls=[*wall, {'provider': 'cursor', 'premium_only': True}])['agent'], 'cursor-grok')
        self.data['fallback']['codex'] = ['claude']
        with self.assertRaises(RoutingError):
            pick(self.data, role='backend', cwd=self.cwd, walls=wall)
        self.data['roles']['backend'] = ['opus', 'codex-sol']
        self.data['agents']['opus']['avoid_for'].append('backend')
        self.assertEqual(pick(self.data, role='backend', cwd=self.cwd)['agent'], 'codex-sol')

    def test_ask_first_and_explicit_model_effort_win(self):
        self.data['roles']['backend'].insert(0, 'codex-astra')
        self.assertEqual(pick(self.data, role='backend', cwd=self.cwd)['agent'], 'codex-sol')
        self.assertEqual(pick(self.data, role='backend', cwd=self.cwd, kind='codex')['agent'], 'codex-sol')
        chosen = pick(self.data, role='backend', cwd=self.cwd, agent='codex-astra', model='custom', effort='low')
        self.assertEqual((chosen['agent'], chosen['model'], chosen['effort']), ('codex-astra', 'custom', 'low'))

    def test_user_override_wins_and_bad_toml_is_clear(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'XDG_CONFIG_HOME': directory}):
            folder = Path(directory) / 'herdr-attention'
            folder.mkdir()
            (folder / 'routing.toml').write_text('bad = [')
            with self.assertRaisesRegex(RoutingError, 'routing.toml'):
                load()

