"""Fail when shared definitions are copied or their generated consumers drift."""
from pathlib import Path
import unittest
from unittest.mock import patch

from herdr_attention.config import DEFAULTS
from herdr_attention.notify_tiers import ACTION_LABELS
from herdr_attention.ui_contract import CONTRACT, browser_source, definition_errors

ROOT = Path(__file__).resolve().parents[1]


class SharedDefinitions(unittest.TestCase):
    def test_shipping_definitions_do_not_drift(self):
        self.assertEqual(definition_errors(), [])

    def test_check_detects_copies_of_each_shared_definition(self):
        path = 'herdr_attention/desktop.py'
        old = (ROOT/path).read_text()
        for definition in (CONTRACT['api']['focus'], CONTRACT['hashes']['conversation'],
                           CONTRACT['hashes']['notification'], ACTION_LABELS['open_in_herdr'], 'pane.focus'):
            with self.subTest(definition=definition):
                self.assertTrue(definition_errors(sources={path: old + '\nCOPY = ' + repr(definition)}))
        self.assertTrue(definition_errors(sources={path: old + '\nFEATURES = {"desktop_focus": True}'}))
        port = CONTRACT['listeners']['desktop']['port']
        self.assertTrue(definition_errors(sources={path: old + f'\nPORT = {port}'}))

    def test_check_detects_listener_and_focus_allowlist_drift(self):
        from herdr_attention.focus import HerdrFocus
        with patch.dict(DEFAULTS['desktop'], {'port': 9000}):
            self.assertTrue(any('defaults differ' in error for error in definition_errors()))
        with patch.object(HerdrFocus, 'ALLOWED_METHODS', frozenset({'pane.focus', 'agent.prompt'})):
            self.assertTrue(any('allowlist' in error for error in definition_errors()))

    def test_feature_flag_and_shared_vocabulary_are_generated(self):
        # Changing the feature flag or v0.8.1 vocabulary invalidates the
        # checked-in browser contract, rather than leaving a silent old copy.
        for mapping, values in ((CONTRACT['features'], {'desktop_focus': 'changed'}),
                                (ACTION_LABELS, {'open_in_herdr': 'changed'})):
            with patch.dict(mapping, values):
                self.assertTrue(any('browser contract is stale' in error for error in definition_errors()))
