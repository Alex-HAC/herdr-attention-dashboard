"""Desktop notification click opens the per-notification link (v0.8.2 slice C).

Uses a fake notify-send on PATH; no real notification is shown.
"""

import os
import stat
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from herdr_attention.notify import DesktopSender

FAKE = """#!/bin/sh
if [ "$1" = "--help" ]; then
  [ "$FAKE_ACTIONS" = "1" ] && echo "  -A, --action=[NAME=]Text...  Specifies the actions"
  exit 0
fi
printf '%s\\n' "$@" > "$FAKE_DIR/args"
[ -n "$FAKE_SLEEP" ] && sleep "$FAKE_SLEEP"
[ -n "$FAKE_PICK" ] && echo "$FAKE_PICK"
exit 0
"""


class DesktopClick(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        exe = self.dir / "notify-send"
        exe.write_text(FAKE)
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
        self.env = mock.patch.dict(os.environ, {"PATH": f"{self.dir}:{os.environ['PATH']}", "FAKE_DIR": str(self.dir),
                                                "FAKE_ACTIONS": "1", "FAKE_PICK": "", "FAKE_SLEEP": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.opened = []

    def send(self, click="https://laptop.example.ts.net/#/n/42", **kw):
        sender = DesktopSender(opener=self.opened.append, **kw)
        sender.send(title="Waiting on you: ui-builder", message="Approve npm test?", priority="high",
                    tags=[], click=click)
        return sender

    def args(self):
        for _ in range(100):
            path = self.dir / "args"
            if path.exists() and path.read_text():
                return path.read_text().splitlines()
            time.sleep(0.02)
        return []

    def wait_threads(self):
        import threading
        for t in threading.enumerate():
            if t.name == "desktop-notify-click":
                t.join(5)

    def test_click_opens_the_link(self):
        os.environ["FAKE_PICK"] = "default"
        self.send()
        self.wait_threads()
        self.assertIn("--action=default=Open", self.args())
        self.assertEqual(self.opened, ["https://laptop.example.ts.net/#/n/42"])

    def test_dismissed_without_click_opens_nothing(self):
        self.send()
        self.wait_threads()
        self.assertIn("--action=default=Open", self.args())
        self.assertEqual(self.opened, [])

    def test_send_does_not_block_and_wait_is_bounded(self):
        os.environ["FAKE_PICK"] = "default"
        os.environ["FAKE_SLEEP"] = "3"
        started = time.monotonic()
        self.send(wait_seconds=0.3)
        self.assertLess(time.monotonic() - started, 1.0, "the collector is not blocked")
        self.wait_threads()
        self.assertEqual(self.opened, [], "killed after the bounded wait, nothing opened")

    def test_without_action_support_falls_back(self):
        os.environ["FAKE_ACTIONS"] = "0"
        os.environ["FAKE_PICK"] = "default"
        self.send()
        args = self.args()
        self.assertNotIn("--action=default=Open", args)
        self.assertIn("Waiting on you: ui-builder", args)
        self.assertEqual(self.opened, [])

    def test_without_link_falls_back(self):
        self.send(click="")
        self.assertFalse(any(a.startswith("--action") for a in self.args()))


if __name__ == "__main__":
    unittest.main()
