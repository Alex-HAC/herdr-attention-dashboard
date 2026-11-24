"""v0.9.8.4: Super+Shift+H finds the dashboard's own window, wherever it is.

Seen on 19 Nov: the only window with class "herdr-overview" was a Google Drive tab on
another workspace (Chromium had been started by the overview with --class, so every later
browser window got that class), and the dashboard window itself, opened through Omarchy's
browser handoff, had Chromium's app id "chrome-127.0.0.1__-Default". The shortcut focused
the Drive window."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from herdr_attention import desktop
from herdr_attention.ui_contract import CONTRACT

URL = "http://127.0.0.1:8766/"
TITLE = CONTRACT["desktop"]["title"]
HERE = {"id": 1, "name": "1"}


def win(address, klass, title, ws="1", history=3):
    return {"address": address, "class": klass, "title": title, "workspace": {"id": 0, "name": ws}, "focusHistoryID": history}


DRIVE = win("drive", "herdr-overview", "Validatieplan.pdf - Google Drive - Chromium", ws="4", history=0)
GRAPH = win("graph", "chrome-127.0.0.1__team-graph_-Default", "Team graph", ws="special:teamgraph")
DASH = win("dash", "chrome-127.0.0.1__-Default", TITLE, ws="5")


class FindTheWindow(unittest.TestCase):
    def test_a_browser_window_with_the_class_is_not_the_dashboard(self):
        self.assertIsNone(desktop.find_overview([DRIVE, GRAPH], URL))
        self.assertEqual(desktop.plan([DRIVE, GRAPH], HERE, URL, None, 100.0), ("launch", None))

    def test_the_dashboard_by_title_or_app_id(self):
        self.assertEqual(desktop.find_overview([DRIVE, GRAPH, DASH], URL)["address"], "dash")
        loading = dict(DASH, title="Herdr")       # before the page sets its title
        self.assertEqual(desktop.find_overview([DRIVE, loading], URL)["address"], "dash")
        other_app = dict(DASH, title="Some other local app")
        self.assertIsNone(desktop.find_overview([other_app], URL))
        self.assertIsNone(desktop.find_overview([GRAPH], URL), "another app on 127.0.0.1")

    def test_a_tab_titled_like_the_dashboard_in_a_normal_window_is_not_it(self):
        self.assertIsNone(desktop.find_overview([win("tab", "chromium", TITLE + " - Chromium")], URL))


class WhatThePressDoes(unittest.TestCase):
    def test_on_another_workspace_it_is_focused_there(self):
        self.assertEqual(desktop.plan([DRIVE, DASH], HERE, URL, None, 100.0)[0], "focus")

    def test_on_this_workspace_it_is_focused(self):
        self.assertEqual(desktop.plan([dict(DASH, workspace={"id": 1, "name": "1"})], HERE, URL, None, 100.0)[0], "focus")

    def test_hidden_on_a_special_workspace_it_comes_here(self):
        hidden = dict(DASH, workspace={"id": -95, "name": "special:scratchpad"})
        self.assertEqual(desktop.plan([hidden], HERE, URL, None, 100.0), ("bring", hidden))

    def test_a_second_press_while_it_opens_does_not_open_another(self):
        self.assertEqual(desktop.plan([], HERE, URL, 98.0, 100.0), ("wait", None))
        self.assertEqual(desktop.plan([], HERE, URL, 80.0, 100.0), ("launch", None))


class Dispatches(unittest.TestCase):
    def run_show(self, windows):
        calls = []
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(desktop, "LAUNCH_STAMP", Path(tmp) / "stamp"), \
             patch.object(desktop, "clients", return_value=windows), \
             patch.object(desktop, "active_workspace", return_value=HERE), \
             patch.object(desktop, "run", side_effect=lambda args: calls.append(args)), \
             patch.object(desktop.subprocess, "Popen", side_effect=lambda args, **kw: calls.append(args)):
            action = desktop.show_overview(URL)
            stamped = (Path(tmp) / "stamp").exists()
        return action, calls, stamped

    def test_hidden_window_is_moved_to_the_current_workspace_then_focused(self):
        hidden = dict(DASH, workspace={"id": -95, "name": "special:scratchpad"})
        action, calls, _ = self.run_show([DRIVE, hidden])
        self.assertEqual(action, "bring")
        self.assertEqual(calls, [["hyprctl", "dispatch", 'hl.dsp.window.move({ workspace = "1", window = "address:dash" })'],
                                 ["hyprctl", "dispatch", 'hl.dsp.focus({ window = "address:dash" })']])

    def test_only_the_dashboard_window_is_touched(self):
        action, calls, _ = self.run_show([DRIVE, GRAPH, DASH])
        self.assertEqual(action, "focus")
        self.assertEqual(calls, [["hyprctl", "dispatch", 'hl.dsp.focus({ window = "address:dash" })']])

    def test_not_running_launches_without_class_and_stamps(self):
        action, calls, stamped = self.run_show([DRIVE])
        self.assertEqual(action, "launch")
        self.assertEqual(calls, [["omarchy-launch-webapp", URL]])
        self.assertTrue(stamped)


if __name__ == "__main__":
    unittest.main()
