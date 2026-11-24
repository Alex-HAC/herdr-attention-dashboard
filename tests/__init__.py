"""Tests. Run: python3 -m unittest discover -s tests -t ."""

import warnings

# Many tests use short-lived in-memory stores and let them be garbage collected.
warnings.filterwarnings("ignore", category=ResourceWarning, message="unclosed database")
