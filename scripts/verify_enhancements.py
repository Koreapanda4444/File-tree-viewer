"""Repeatable integration checks using disposable data and file trees."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication

from ui.command_palette import collect_commands
from ui.commands import migrate_keys
from ui.window import MainWindow

APP = QApplication.instance() or QApplication([])


def wait_for(condition, timeout=15):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        APP.processEvents()
        if condition():
            return
        time.sleep(0.002)
    raise AssertionError("Background work timed out")


class WindowChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ftv-check-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.data_patch = patch(
            "application_paths.data_directory", return_value=self.base / "data"
        )
        self.data_patch.start()
        self.addCleanup(self.data_patch.stop)
        self.window = MainWindow()
        self.window.show()
        self.addCleanup(self.close_window)

    def close_window(self):
        self.window.close()
        wait_for(lambda: not self.window.isVisible())
        self.window.deleteLater()
        APP.processEvents()

    def test_stable_commands_and_legacy_history(self):
        commands = collect_commands(self.window)
        keys = [c.key for c in commands]
        self.assertEqual(len(keys), len(set(keys)))
        command = next(c for c in commands if c.key == "real.delete")
        self.assertTrue(command.dangerous)
        self.assertEqual(command.confirmation, "handler")
        self.assertEqual(migrate_keys([command.aliases[0]], commands), [command.key])
        self.window.real_page.delete_button.setText("Changed display")
        self.assertIn("real.delete", [c.key for c in collect_commands(self.window)])


if __name__ == "__main__":
    unittest.main()
