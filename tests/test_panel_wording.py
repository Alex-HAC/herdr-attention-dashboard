"""Bar panel wording matches the web dashboard (one vocabulary)."""
import unittest
from pathlib import Path

PLUGIN = Path(__file__).parent.parent / 'plugin' / 'alexvdb.herdr-attention'


class PanelWording(unittest.TestCase):
    def test_attention_section_uses_the_web_heading(self):
        panel = (PLUGIN / 'Panel.qml').read_text()
        self.assertIn('"To look at ("', panel)
        self.assertNotIn('Needs a look', panel)

    def test_no_dead_explain_button(self):
        self.assertNotIn('"Explain"', (PLUGIN / 'AgentRow.qml').read_text())


if __name__ == '__main__':
    unittest.main()
