"""Layout policy uses observed topology; it never controls an existing agent."""
import unittest
from herdr_attention.layout import choose, generated_name
from herdr_attention.routing import load, validate, RoutingError
from pathlib import Path


class Layout(unittest.TestCase):
    def setUp(self):
        self.policy = {'max_panes_per_tab': 4, 'group_workers_by': 'provider', 'coordinator_own_tab': True,
                       'role_abbrev': {'gruntwork': 'wrk'}}
        self.tabs = [{'tab_id': 'w1:t1', 'workspace_id': 'w1', 'label': 'codex'}]
        self.panes = [{'pane_id': f'w1:p{i}', 'tab_id': 'w1:t1', 'workspace_id': 'w1'} for i in range(1, 5)]

    def choose(self, **changes):
        args = dict(name='explicit-name', role='backend', provider='codex', workspace='w1', caller_pane='w1:p1',
                    caller_tab='w1:t1', tabs=self.tabs, panes=self.panes)
        args.update(changes)
        return choose(self.policy, **args)

    def test_full_tab_overflows_even_for_explicit_split(self):
        for explicit in (False, True):
            selected = self.choose(explicit_split=explicit)
            self.assertTrue(selected['create_tab'])
            self.assertEqual(selected['label'], 'codex-2')

    def test_reuses_group_below_cap_and_keeps_caller_parent(self):
        selected = self.choose(panes=self.panes[:3])
        self.assertFalse(selected['create_tab'])
        self.assertEqual(selected['target'], 'w1:p3')
        self.assertEqual(selected['label'], 'codex')
        # The layout's split target is separate from the launch creation parent.
        self.assertNotEqual(selected['target'], 'w1:p1')

    def test_coordinator_has_own_tab_and_explicit_tab_uses_unique_group_label(self):
        selected = self.choose(role='coordinator', explicit_split=True)
        self.assertTrue(selected['create_tab'])
        self.assertEqual(selected['label'], 'explicit-name coordinator')
        self.assertEqual(self.choose(new_tab=True, panes=self.panes[:1])['label'], 'codex-2')

    def test_other_workspace_groups_are_never_reused(self):
        self.tabs[0]['workspace_id'] = 'w2'
        self.panes[0]['workspace_id'] = 'w2'
        self.assertEqual(self.choose()['label'], 'codex')
        self.assertTrue(self.choose()['create_tab'])

    def test_role_group_and_generated_herdr_names(self):
        self.policy['group_workers_by'] = 'role'
        self.assertEqual(self.choose(role='pr-review')['label'], 'pr-review')
        name = generated_name(self.policy, role='gruntwork', provider='codex', task='Long task / many words ' * 5, suffix='abc')
        self.assertLessEqual(len(name), 32)
        self.assertRegex(name, r'^[a-z][a-z0-9_-]*$')
        self.assertTrue(name.startswith('wrk-codex-'))

    def test_bad_layout_is_rejected_before_launch(self):
        data = load(Path(__file__).resolve().parent.parent / 'config/routing.toml')
        for bad in ({'max_panes_per_tab': 0}, {'group_workers_by': 'anything'},
                    {'coordinator_own_tab': 'yes'}, {'pane_name_template': '{secret}'}):
            data['layout'] = bad
            with self.assertRaises(RoutingError):
                validate(data)
