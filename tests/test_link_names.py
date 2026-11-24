"""v0.9.3: link ends are named with content.agent_name, never a state label or a raw session ID."""
import unittest

from herdr_attention.attention import coordinator_of
from herdr_attention.engine import Engine
from herdr_attention.store import Store
from tests.helpers import Clock, settings


class LinkNames(unittest.TestCase):
    def name(self, dest):
        engine = Engine(settings(), Store(':memory:'), Clock())
        links = [{'kind': 'reports_to', 'from': 'w', 'to': 'c'}]
        engine._name_links(links, [dict(dest, identity='c'), {'identity': 'w', 'name': 'worker'}])
        return links

    def test_state_label_is_not_a_name(self):
        links = self.name({'name': None, 'title': '', 'label': 'Idle', 'pane_id': 'w1:p2'})
        self.assertEqual(links[0]['to_name'], 'Agent w1:p2')
        self.assertEqual(links[0]['from_name'], 'worker')
        self.assertEqual(coordinator_of('w', links)['name'], 'its coordinator')

    def test_task_title_names_a_nameless_coordinator(self):
        links = self.name({'name': None, 'title': 'Coordinate Fable recovery', 'label': 'Idle'})
        self.assertEqual(links[0]['to_name'], 'Coordinate Fable recovery')

    def test_a_real_name_is_kept_in_full(self):
        links = self.name({'name': 'lead | checkout', 'title': 'x', 'label': 'Working'})
        self.assertEqual(links[0]['to_name'], 'lead | checkout')


if __name__ == '__main__':
    unittest.main()
