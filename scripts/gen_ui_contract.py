#!/usr/bin/env python3
"""Generate the web and bar panel contracts. --check fails if shared definitions drift."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from herdr_attention.ui_contract import PLUGIN_CONTRACT, browser_source, definition_errors, plugin_source

root = Path(__file__).resolve().parents[1]
outputs = {root / 'herdr_attention/web/ui-contract.js': browser_source(), root / PLUGIN_CONTRACT: plugin_source()}
if '--check' in sys.argv:
    if any(not path.exists() or path.read_text() != expected for path, expected in outputs.items()):
        sys.exit('UI contract drift: run python3 scripts/gen_ui_contract.py')
    errors = definition_errors()
    if errors:
        sys.exit('Shared definition drift:\n' + '\n'.join(errors))
else:
    for path, expected in outputs.items():
        path.write_text(expected)
