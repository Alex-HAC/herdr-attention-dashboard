"""ntfy QR helper: writes a PNG, never prints the topic."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from herdr_attention.cli import main


class NtfyQr(unittest.TestCase):
    def test_png_is_written_without_printing_the_topic(self):
        topic = "unit-secret-topic-qr"
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.toml"
            cfg.write_text(
                "[notify]\n"
                'backend = "ntfy"\n'
                'ntfy_url = "https://omarchy.example.ts.net:8443"\n'
                f'ntfy_topic = "{topic}"\n'
            )
            png = Path(tmp) / "qr.png"
            from io import StringIO
            import contextlib
            stdout, stderr = StringIO(), StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = main(["--config", str(cfg), "ntfy", "qr", "--png", str(png)])
            out = stdout.getvalue() + stderr.getvalue()
            self.assertEqual(code, 0)
            self.assertTrue(png.is_file())
            self.assertTrue(png.read_bytes().startswith(b"\x89PNG"))
            self.assertNotIn(topic, out)
            self.assertIn(str(png), out)
            png.unlink()
            self.assertFalse(png.exists())


if __name__ == "__main__":
    unittest.main()
