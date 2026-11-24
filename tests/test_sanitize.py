import unittest

from herdr_attention.config import DEFAULTS
from herdr_attention.sanitize import clean_output, fingerprint, redact, strip_terminal


class StripTerminal(unittest.TestCase):
    def test_removes_colour_and_cursor_codes(self):
        self.assertEqual(strip_terminal("\x1b[31mred\x1b[0m \x1b[2K\x1b[1;5Hdone"), "red done")

    def test_removes_osc_title_and_hyperlinks(self):
        text = "\x1b]0;window title\x07hello \x1b]8;;https://x.test\x1b\\link\x1b]8;;\x1b\\"
        self.assertEqual(strip_terminal(text), "hello link")

    def test_removes_control_and_bidi_characters(self):
        self.assertEqual(strip_terminal("a\x00b\x07c‮d\x9b"), "abcd")

    def test_keeps_tabs_and_newlines(self):
        self.assertEqual(strip_terminal("a\tb\r\nc\rd"), "a\tb\nc\nd")

    def test_html_is_left_as_text(self):
        # Escaping is the renderer's job (PlainText / textContent); nothing is interpreted here.
        self.assertEqual(strip_terminal("<b>x</b>"), "<b>x</b>")


class Redaction(unittest.TestCase):
    patterns = DEFAULTS["output"]["redact"]

    def test_default_patterns(self):
        text = ("token=abc123 password: hunter2 sk-abcdefghijklmnopqrstuv ghp_abcdefghijklmnopqrstuvwxyz "
                "AKIAABCDEFGHIJKLMNOP plain words stay")
        out, count = redact(text, self.patterns)
        for secret in ("abc123", "hunter2", "sk-abc", "ghp_abc", "AKIAABCD"):
            self.assertNotIn(secret, out)
        self.assertIn("plain words stay", out)
        self.assertEqual(count, 5)

    def test_bad_pattern_is_ignored(self):
        self.assertEqual(redact("x", ["(unclosed"]), ("x", 0))


class CleanOutput(unittest.TestCase):
    def test_keeps_last_lines_only(self):
        text = "\n".join(f"line {i}" for i in range(100))
        out = clean_output(text, [], 5)
        self.assertEqual(out["text"].split("\n"), [f"line {i}" for i in range(95, 100)])


class Fingerprint(unittest.TestCase):
    def test_timers_and_spinners_do_not_count_as_change(self):
        a = "⠋ Running tests · 1m 03s · 1200 tokens\nesc to interrupt"
        b = "⠙ Running tests · 1m 04s · 1250 tokens\nesc to interrupt"
        self.assertEqual(fingerprint(a), fingerprint(b))

    def test_new_text_counts_as_change(self):
        self.assertNotEqual(fingerprint("Running tests"), fingerprint("Running tests\nAll passed"))


if __name__ == "__main__":
    unittest.main()
