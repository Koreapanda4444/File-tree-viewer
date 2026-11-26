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
from PySide6.QtCore import QEvent
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
        self.qt_errors = []
        self.hook_patch = patch(
            "sys.excepthook",
            side_effect=lambda kind, value, tb: self.qt_errors.append(str(value)),
        )
        self.hook_patch.start()
        self.addCleanup(self.hook_patch.stop)
        self.addCleanup(lambda: self.assertEqual(self.qt_errors, []))
        self.addCleanup(self.close_window)
        from write_policy import policy

        self.addCleanup(lambda: policy.set_read_only(False))

    def close_window(self):
        self.window.close()
        wait_for(lambda: not self.window.isVisible())
        self.window.deleteLater()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
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

    def test_cross_pane_copy_move_conflicts_and_undo(self):
        from PySide6.QtCore import QItemSelectionModel

        workspace = self.window.real_workspace
        left, right = workspace.panes
        a, b = self.base / "source", self.base / "destination"
        a.mkdir()
        b.mkdir()
        source = a / "hello.txt"
        source.write_text("original", encoding="utf-8")
        left.set_root(a)
        workspace.set_dual(True)
        right.set_root(b)
        wait_for(lambda: left.model.index_for_path(source).isValid())
        index = left.browse_model.mapFromSource(left.model.index_for_path(source))
        left.tree.selectionModel().setCurrentIndex(
            index,
            QItemSelectionModel.SelectionFlag.ClearAndSelect
            | QItemSelectionModel.SelectionFlag.Rows,
        )
        wait_for(lambda: not left.preview_is_running)
        workspace.activate(left)
        self.assertTrue(workspace.transfer_to_other("copy"))
        self.assertFalse(workspace.set_dual(False))
        self.assertFalse(
            right._start_file_operation("copy", sources=[source], destination=b)
        )
        wait_for(lambda: not left.operation_is_running)
        self.assertEqual((b / source.name).read_text(), "original")
        self.assertTrue(source.exists())
        self.assertTrue(left.undo_stack)
        left.undo_last_operation()
        wait_for(lambda: not left.operation_is_running)
        self.assertFalse((b / source.name).exists())
        self.assertTrue(left.file_commands.transfer([source], "move", b))
        wait_for(lambda: not left.operation_is_running)
        self.assertFalse(source.exists())
        left.undo_last_operation()
        wait_for(lambda: not left.operation_is_running)
        self.assertEqual(source.read_text(), "original")
        (b / source.name).write_text("existing", encoding="utf-8")
        with patch("ui.real_explorer.show_error"):
            left.file_commands.transfer([source], "copy", b)
            wait_for(lambda: not left.operation_is_running)
        self.assertEqual((b / source.name).read_text(), "existing")
        self.assertEqual(source.read_text(), "original")
        self.assertFalse(workspace.validate_transfer([source], a))
        nested = a / "nested"
        nested.mkdir()
        self.assertFalse(workspace.validate_transfer([a], nested))
        self.assertTrue(workspace.swap_paths())
        self.assertEqual(left.model.root_path, b)
        self.assertTrue(workspace.open_same_path())
        self.assertEqual(right.model.root_path, b)

    def test_local_filter_finds_unloaded_rows_and_restores_inventory(self):
        from real.directory_index import DirectoryFilter
        from real.tree import FileTreeModel

        root = self.base / "filter"
        root.mkdir()
        for n in range(800):
            (root / f"{n:04d}.txt").touch()
        (root / "zz-한국어.bin").write_bytes(b"binary")
        (root / "sub").mkdir()
        (root / "sub" / "nested.bin").touch()
        model = FileTreeModel(page_size=7)
        counts = []
        model.listing_counts.connect(
            lambda visible, total: counts.append((visible, total))
        )
        model.set_root(root)
        model.fetchMore(model.index(0, 0))
        wait_for(lambda: not model.root.fetch_pending)
        self.assertEqual(len(model.root.children), 7)
        model.set_local_filter(
            DirectoryFilter(name="한국어", extensions=(".bin",), kind="files")
        )
        wait_for(lambda: not model.root.fetch_pending)
        self.assertEqual([n.name for n in model.root.children], ["zz-한국어.bin"])
        self.assertEqual(counts[-1], (1, 802))
        self.assertEqual(len(model.root.inventory), 802)
        model.set_local_filter(DirectoryFilter())
        wait_for(lambda: not model.root.fetch_pending)
        self.assertEqual(counts[-1], (802, 802))
        self.assertEqual(len(model.root.children), 7)
        model.clear()
        wait_for(lambda: model.index_pool.activeThreadCount() == 0)
        model.close()

    def test_filters_are_independent_and_navigation_policy_is_explicit(self):
        w = self.window.real_workspace
        root = self.base / "list"
        root.mkdir()
        (root / "keep.txt").touch()
        (root / "other.bin").touch()
        nested = root / "nested"
        nested.mkdir()
        (nested / "keep.txt").touch()
        w.left.set_root(root)
        w.set_dual(True)
        w.right.set_root(root)
        left, right = w.left.directory_filter, w.right.directory_filter
        left.name.setText("keep")
        left.extensions.setText(".txt")
        wait_for(
            lambda: not left.timer.isActive() and not w.left.model.root.fetch_pending
        )
        self.assertEqual([n.name for n in w.left.model.root.children], ["keep.txt"])
        self.assertFalse(w.right.model.local_filter.active)
        left.retain.setChecked(True)
        w.left.set_root(nested)
        self.assertTrue(w.left.model.local_filter.active)
        left.retain.setChecked(False)
        w.left.set_root(root)
        self.assertFalse(w.left.model.local_filter.active)
        self.assertEqual(left.name.text(), "")
        self.assertEqual(right.name.text(), "")

    def test_read_only_state_and_active_mutation_lease(self):
        from file_operations import create_item
        from write_policy import ReadOnlyError, policy

        root = self.base / "protected"
        root.mkdir()
        self.window.real_page.set_root(root)
        self.assertTrue(self.window.set_read_only(True))
        self.assertTrue(policy.read_only)
        self.assertFalse(self.window.real_page.new_file_button.isEnabled())
        with self.assertRaises(ReadOnlyError):
            create_item(root, "blocked.txt", directory=False)
        self.assertFalse((root / "blocked.txt").exists())
        self.assertTrue(self.window.set_read_only(False))
        with policy.mutation():
            self.assertFalse(self.window.set_read_only(True))
            self.assertFalse(policy.read_only)
        create_item(root, "allowed.txt", directory=False)
        self.assertTrue((root / "allowed.txt").exists())

    def test_read_only_rejects_engines_and_allows_reports_and_snapshots(self):
        from file_operations import FileOperationTask, rename_item
        from file_preview import save_text_atomic
        from plan_apply import apply_plan, undo_application
        from recovery import recover_root
        from snapshot import build_snapshot
        from snapshot_store import save_snapshot
        from sync_apply import apply_sync_plan, recover_sync_group
        from tree_export import build_tree, write_tree
        from virtual.storage import (
            VirtualNode,
            export_workspace_root,
            save_workspace_root,
        )
        from write_policy import ReadOnlyError

        root = self.base / "safe"
        root.mkdir()
        source = root / "source.txt"
        source.write_text("preserve")
        target = self.base / "target"
        target.mkdir()
        task = FileOperationTask("copy", sources=[source], destination=target)
        self.assertTrue(self.window.set_read_only(True))
        calls = (
            task.run,
            lambda: rename_item(source, "changed.txt"),
            lambda: save_text_atomic(source, "changed", encoding="utf-8", newline="\n"),
            lambda: apply_plan(None, None),
            lambda: undo_application(None),
            lambda: recover_root(root),
            lambda: apply_sync_plan(None),
            lambda: recover_sync_group(root / "sync.json"),
            lambda: export_workspace_root(target, VirtualNode("VM:", True)),
        )
        for callback in calls:
            with self.assertRaises(ReadOnlyError):
                callback()
        self.assertEqual(source.read_text(), "preserve")
        self.assertEqual(list(target.iterdir()), [])
        snapshot = build_snapshot(root)
        try:
            save_snapshot(
                snapshot, self.base / "report.ftvsnapshot", name="Read only snapshot"
            )
            self.assertTrue((self.base / "report.ftvsnapshot").exists())
            write_tree(build_tree(root), self.base / "tree.txt")
            self.assertTrue((self.base / "tree.txt").exists())
        finally:
            snapshot.close()
        save_workspace_root(self.base / "virtual.json", VirtualNode("VM:", True))
        self.assertTrue((self.base / "virtual.json").exists())

    def test_read_only_shortcuts_context_menu_and_deferred_worker(self):
        from PySide6.QtCore import QItemSelectionModel

        from real.operations import FileOperationWorker

        root = self.base / "source"
        root.mkdir()
        source = root / "file.txt"
        source.write_text("safe")
        target = self.base / "target"
        target.mkdir()
        page = self.window.real_page
        page.set_root(root)
        wait_for(lambda: page.model.index_for_path(source).isValid())
        index = page.browse_model.mapFromSource(page.model.index_for_path(source))
        page.tree.selectionModel().setCurrentIndex(
            index,
            QItemSelectionModel.SelectionFlag.ClearAndSelect
            | QItemSelectionModel.SelectionFlag.Rows,
        )
        wait_for(lambda: not page.preview_is_running)
        worker = FileOperationWorker("copy", sources=[source], destination=target)
        results = []
        worker.finished.connect(lambda *args: results.append(args))
        self.assertTrue(self.window.set_read_only(True))
        with (
            patch("ui.real_explorer.QFileDialog.getExistingDirectory") as chooser,
            patch("ui.real_explorer.QMessageBox.question") as question,
        ):
            page.transfer_selected("copy")
            page.delete_selected()
            page.file_commands.paste(target)
            self.assertFalse(page.file_commands.transfer([source], "move", target))
            chooser.assert_not_called()
            question.assert_not_called()
        menu = page.file_commands.build_menu()
        actions = {a.text(): a for a in menu.actions()}
        self.assertFalse(actions["Delete"].isEnabled())
        self.assertFalse(page.file_commands.actions["Paste"].isEnabled())
        self.assertFalse(page.preview.isReadOnly() is False)
        worker.run()
        self.assertTrue(results[0][2])
        self.assertEqual(list(target.iterdir()), [])
        self.assertEqual(source.read_text(), "safe")
        menu.deleteLater()
        self.window.plan_page.start_plan_apply()
        self.window.plan_page.start_plan_undo(record=object())
        self.assertIsNone(self.window.plan_page.apply_thread)
        self.assertIsNone(self.window.plan_page.undo_thread)

    def test_streaming_hash_cancellation_and_external_change(self):
        import hashlib
        import threading

        from file_hashing import hash_file
        from snapshot import SnapshotCancelled

        source = self.base / "large.bin"
        block = b"abc123" * 200000
        source.write_bytes(block * 4)
        progress = []
        result = hash_file(
            source, progress=lambda done, total: progress.append((done, total))
        )
        self.assertEqual(result.digest, hashlib.sha256(block * 4).hexdigest())
        self.assertGreater(len(progress), 2)
        self.assertEqual(progress[-1], (source.stat().st_size, source.stat().st_size))
        event = threading.Event()
        with self.assertRaises(SnapshotCancelled):
            hash_file(source, cancelled=event, progress=lambda done, total: event.set())

        def modify(done, total):
            if done:
                source.write_bytes(b"externally changed")

        with self.assertRaises(OSError):
            hash_file(source, progress=modify)
        with self.assertRaises(OSError):
            result.verify()
        empty = self.base / "empty"
        empty.touch()
        self.assertEqual(hash_file(empty).digest, hashlib.sha256(b"").hexdigest())

    def test_hash_dialog_comparison_copy_cancel_and_invalidation(self):
        from file_hashing import hash_file
        from ui.file_hash import FileHashDialog
        from write_policy import policy

        left, right = self.base / "hash-a.txt", self.base / "hash-b.txt"
        left.write_text("same")
        right.write_text("same")
        dialog = FileHashDialog((left, right), self.window)
        dialog.show()
        self.window.set_read_only(True)
        dialog.start()
        wait_for(lambda: dialog.thread is None)
        self.assertEqual(len(dialog.results), 2)
        self.assertIn("match", dialog.status.text())
        dialog.expected.setText("invalid")
        self.assertIn("64", dialog.status.text())
        dialog.expected.setText(dialog.results[0].digest.upper())
        self.assertIn("Expected hash matches", dialog.status.text())
        dialog.copy_hash()
        self.assertEqual(APP.clipboard().text(), dialog.results[0].digest)
        right.write_text("different")
        self.assertFalse(dialog.validate_results())
        self.assertFalse(dialog.copy.isEnabled())
        self.assertEqual(dialog.output.toPlainText(), "")
        import threading

        entered, release = threading.Event(), threading.Event()

        def paused_hash(path, **kwargs):
            entered.set()
            release.wait(5)
            return hash_file(path, **kwargs)

        with patch("ui.file_hash.hash_file", side_effect=paused_hash):
            dialog.start()
            wait_for(entered.is_set)
            dialog.cancel_calculation()
            release.set()
            wait_for(lambda: dialog.thread is None)
        self.assertFalse(dialog.results)
        self.assertFalse(dialog.copy.isEnabled())
        self.assertTrue(policy.read_only)
        self.assertEqual(hash_file(left).size, 4)
        dialog.reject()
        dialog.deleteLater()

    def test_cross_pane_drag_drop_and_task_focus(self):
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtGui import QDropEvent

        from real.file_clipboard import file_mime

        workspace = self.window.real_workspace
        left, right = workspace.panes
        a, b = self.base / "drag-from", self.base / "drag-to"
        a.mkdir()
        b.mkdir()
        source = a / "drag.txt"
        source.write_text("dragged")
        left.set_root(a)
        workspace.set_dual(True)
        right.set_root(b)
        mime = file_mime([source])
        event = QDropEvent(
            QPointF(-1, -1),
            Qt.DropAction.CopyAction | Qt.DropAction.MoveAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.ControlModifier,
        )
        right.file_drop.drop_files(right.folder_view, event)
        self.assertTrue(event.isAccepted())
        self.assertEqual(event.dropAction(), Qt.DropAction.CopyAction)
        self.window.task_monitor.focus_task()
        self.assertIs(self.window.real_page, right)
        wait_for(lambda: not right.operation_is_running)
        self.assertEqual((b / "drag.txt").read_text(), "dragged")
        self.assertTrue(source.exists())
        right.undo_last_operation()
        wait_for(lambda: not right.operation_is_running)
        self.assertFalse((b / "drag.txt").exists())
        self.window.set_read_only(True)
        blocked_mime = file_mime([source])
        blocked = QDropEvent(
            QPointF(-1, -1),
            Qt.DropAction.CopyAction,
            blocked_mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.ControlModifier,
        )
        right.file_drop.drop_files(right.folder_view, blocked)
        self.assertFalse(blocked.isAccepted())
        self.assertIsNone(right.operation_thread)

    def test_plan_apply_undo_rollback_and_snapshot_comparison_regression(self):
        import threading

        from plan_apply import ApplicationStatus, apply_plan, undo_application
        from planning import FilePlan
        from snapshot import build_snapshot
        from snapshot_compare import compare_snapshots

        root = self.base / "plan"
        root.mkdir()
        (root / "keep.txt").write_text("keep")
        before = build_snapshot(root)
        self.addCleanup(before.close)
        plan = FilePlan(root)
        plan.add_rename("keep.txt", "renamed.txt")
        plan.add_create_folder("new-folder")
        record = apply_plan(before, plan)
        self.assertIs(record.status, ApplicationStatus.COMPLETED)
        self.assertTrue((root / "renamed.txt").exists())
        after = build_snapshot(root)
        comparison = compare_snapshots(before, after)
        self.assertGreater(comparison.count(), 0)
        comparison.close()
        after.close()
        undo_application(record)
        self.assertEqual((root / "keep.txt").read_text(), "keep")
        self.assertFalse((root / "new-folder").exists())
        fresh = build_snapshot(root)
        self.addCleanup(fresh.close)
        staged = FilePlan(root)
        staged.add_create_file("first.txt")
        staged.add_create_file("second.txt")
        cancel = threading.Event()

        def progress(phase, *args):
            if phase == "Creating files":
                cancel.set()

        cancelled = apply_plan(fresh, staged, cancelled=cancel, progress=progress)
        self.assertIs(cancelled.status, ApplicationStatus.ROLLED_BACK)
        self.assertFalse((root / "first.txt").exists())
        self.assertEqual((root / "keep.txt").read_text(), "keep")

    def test_synchronization_apply_undo_and_read_only_review(self):
        from folder_compare import compare_folders
        from sync_apply import apply_sync_plan, recover_sync_group
        from sync_plan import plan_one_way_sync
        from ui.sync import SyncReviewDialog

        a, b = self.base / "sync-left", self.base / "sync-right"
        a.mkdir()
        b.mkdir()
        (a / "source.txt").write_text("source")
        comparison = compare_folders(a, b, verify_content=True)
        plan = plan_one_way_sync(comparison)
        self.addCleanup(plan.close)
        self.addCleanup(comparison.close)
        result = apply_sync_plan(plan)
        self.assertEqual(result.status, "completed", result.error)
        self.assertEqual((b / "source.txt").read_text(), "source")
        restored = recover_sync_group(
            result.journals[0].parent / "sync.json", undo=True
        )
        self.assertFalse(restored.recovery_errors)
        self.assertFalse((b / "source.txt").exists())
        self.assertEqual((a / "source.txt").read_text(), "source")
        dialog = SyncReviewDialog(plan, self.window)
        dialog.reviewed.setChecked(True)
        self.assertTrue(dialog.apply_button.isEnabled())
        self.window.set_read_only(True)
        self.assertFalse(dialog.apply_button.isEnabled())
        dialog.start_execution("apply")
        self.assertIsNone(dialog.thread)
        dialog.reject()
        dialog.deleteLater()

    def test_large_directory_filter_sort_and_stale_worker_cancellation(self):
        from real.directory_index import DirectoryFilter
        from real.tree import FileTreeModel

        root = self.base / "many"
        root.mkdir()
        for n in range(20000):
            (root / f"{19999 - n:05d}.txt").touch()
        model = FileTreeModel(page_size=23)
        model.set_root(root)
        model.fetchMore(model.index(0, 0))
        model.set_local_filter(DirectoryFilter(name="19999"))
        wait_for(lambda: not model.root.fetch_pending, timeout=30)
        self.assertEqual([node.name for node in model.root.children], ["19999.txt"])
        model.set_local_filter(DirectoryFilter())
        wait_for(lambda: not model.root.fetch_pending)
        self.assertEqual(
            [node.name for node in model.root.children],
            [f"{n:05d}.txt" for n in range(23)],
        )
        model.set_local_filter(DirectoryFilter(name="none"))
        model.clear()
        wait_for(lambda: model.index_pool.activeThreadCount() == 0)
        self.assertIsNone(model.root)
        model.close()

    def test_read_only_output_saves_never_overwrite_existing_files(self):
        from persistence import atomic_text_write, save_plan
        from planning import FilePlan
        from snapshot import build_snapshot
        from snapshot_store import save_snapshot
        from virtual.storage import (
            VirtualNode,
            node_to_data,
            save_workspace,
            save_workspace_root,
        )
        from write_policy import ReadOnlyError

        root = self.base / "output-source"
        root.mkdir()
        (root / "original.txt").write_text("preserve")
        snapshot = build_snapshot(root)
        self.addCleanup(snapshot.close)
        output = self.base / "existing.json"
        output.write_bytes(b"preserve exactly")
        self.window.set_read_only(True)
        virtual = VirtualNode("VM:", True)
        operations = (
            lambda path: save_workspace_root(path, virtual),
            lambda path: save_workspace(path, node_to_data(virtual)),
            lambda path: save_plan(path, FilePlan(root)),
            lambda path: save_snapshot(snapshot, path, name="Saved"),
            lambda path: atomic_text_write(
                path, lambda stream: stream.write("new report")
            ),
        )
        for index, operation in enumerate(operations):
            with self.assertRaises(ReadOnlyError):
                operation(output)
            self.assertEqual(output.read_bytes(), b"preserve exactly")
            fresh = self.base / f"new-output-{index}"
            operation(fresh)
            self.assertTrue(fresh.is_file())
        self.assertEqual((root / "original.txt").read_text(), "preserve")

    def test_deferred_command_does_not_run_after_window_is_hidden(self):
        from ui.command_palette import CommandPalette

        palette = CommandPalette(self.window)
        command = next(c for c in palette.commands if c.key == "real.new_folder")
        root = self.base / "commands"
        root.mkdir()
        self.window.real_page.set_root(root)
        self.assertTrue(command.available())
        calls = []
        command.callback = lambda: calls.append(True)
        palette.visible_commands = [command]
        palette.list.setCurrentRow(0)
        palette.run_selected()
        self.window.hide()
        APP.processEvents()
        self.assertFalse(calls)
        self.window.show()
        palette.deleteLater()


if __name__ == "__main__":
    unittest.main()
