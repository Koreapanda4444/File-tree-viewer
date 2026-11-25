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
        APP.processEvents()
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

    def test_localized_commands_and_deferred_availability(self):
        from ui.command_palette import Command, CommandPalette
        from ui.i18n import set_language

        set_language("ko")
        page = self.window.real_page
        page.file_commands.actions["Delete"].setText("삭제")
        commands = collect_commands(self.window)
        item = next(c for c in commands if c.key == "real.delete")
        self.assertEqual(
            migrate_keys(["실제 파일 / 삭제", "Real File System / Delete"], commands),
            [item.key],
        )
        palette = CommandPalette(self.window)
        enabled, calls = [True], []
        command = Command(
            "test.native",
            "삭제",
            lambda: calls.append(True),
            lambda: enabled[0],
            True,
            "handler",
        )
        palette.visible_commands = [command]
        palette.list.setCurrentRow(0)
        with patch("ui.command_palette.QMessageBox.question") as question:
            palette.run_selected()
            enabled[0] = False
            APP.processEvents()
            question.assert_not_called()
        self.assertFalse(calls)
        palette.deleteLater()
        set_language("en")

    def test_image_zoom_pan_and_reset(self):
        from PySide6.QtGui import QImage

        from ui.image_preview import ImagePreview

        viewer = ImagePreview()
        viewer.resize(420, 300)
        viewer.show()
        image = QImage(1000, 500, QImage.Format.Format_RGB32)
        image.fill(0xFF448866)
        viewer.set_image(image)
        APP.processEvents()
        self.assertLess(viewer.zoom, 1)
        viewer.actual_size()
        self.assertEqual(viewer.zoom, 1)
        self.assertGreater(viewer.view.horizontalScrollBar().maximum(), 0)
        viewer.zoom_by(2)
        self.assertEqual(viewer.source.size(), image.size())
        self.assertEqual(viewer.zoom, 2)
        viewer.set_image(image)
        self.assertTrue(viewer.fit_mode)
        viewer.clear()
        self.assertIsNone(viewer.source)
        self.assertFalse(viewer.buttons["in"].isEnabled())
        viewer.close()
        viewer.deleteLater()

    def test_standalone_image_detects_external_changes(self):
        from PySide6.QtGui import QImage

        from real.preview import FilePreviewWorker
        from ui.image_preview import ImagePreviewDialog

        path = self.base / "image.png"
        image = QImage(2048, 128, QImage.Format.Format_RGB32)
        image.fill(0xFF336699)
        self.assertTrue(image.save(str(path)))
        worker = FilePreviewWorker(path)
        results = []
        worker.finished.connect(results.append)
        worker.run()
        result = results[0]
        self.assertEqual(result.image.width(), 2048)
        dialog = ImagePreviewDialog(result, self.window)
        dialog.show()
        self.assertFalse(dialog.stale)
        path.write_bytes(b"changed")
        dialog.check_source()
        self.assertTrue(dialog.stale)
        self.assertIsNotNone(dialog.preview.source)
        dialog.close()
        self.assertIsNone(dialog.preview.source)

    def test_independent_explorer_state_roundtrip(self):
        from ui.explorer_panes import restore_pane, save_pane
        from ui.i18n import choice_text, set_choice_text
        from ui.preferences import application_settings
        from ui.real_explorer import RealExplorerPage

        other = RealExplorerPage("right")
        left = self.window.real_page
        a, b = self.base / "a", self.base / "b"
        a.mkdir()
        b.mkdir()
        self.assertTrue(left.set_root(a))
        self.assertTrue(other.set_root(b))
        left.sort_field.setCurrentIndex(2)
        set_choice_text(left.folder_panel.mode, "List")
        self.assertEqual(other.sort_field.currentIndex(), 0)
        self.assertEqual(choice_text(other.folder_panel.mode), "Details")
        settings = application_settings()
        save_pane(left, settings, "left")
        self.assertTrue(restore_pane(other, settings, "left"))
        self.assertEqual(other.model.root_path, a)
        self.assertEqual(other.sort_field.currentIndex(), 2)
        self.assertIsNot(other.history, left.history)
        wait_for(other.prepare_close)
        other.finalize_close()
        other.deleteLater()

    def test_dual_pane_toggle_focus_and_navigation(self):
        workspace = self.window.real_workspace
        self.assertFalse(workspace.dual)
        self.assertIs(self.window.real_page, workspace.left)
        a, b = self.base / "left", self.base / "right"
        a.mkdir()
        b.mkdir()
        self.assertTrue(workspace.left.set_root(a))
        self.assertTrue(workspace.set_dual(True))
        self.assertTrue(workspace.right.set_root(b))
        workspace.activate(workspace.right)
        self.assertIs(self.window.real_page, workspace.right)
        self.assertEqual(workspace.left.model.root_path, a)
        self.assertEqual(workspace.right.model.root_path, b)
        self.assertTrue(workspace.set_dual(False))
        self.assertIs(self.window.real_page, workspace.left)
        self.assertTrue(workspace.set_dual(True))
        self.assertEqual(workspace.right.model.root_path, b)
        self.assertIn("real.delete", [c.key for c in collect_commands(self.window)])


if __name__ == "__main__":
    unittest.main()
