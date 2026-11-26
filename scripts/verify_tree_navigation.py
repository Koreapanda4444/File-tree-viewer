"""Tree UX regression checks use disposable roots, settings and application data."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import verify_enhancements as support

from ui.command_palette import collect_commands
from ui.commands import migrate_keys
from ui.explorer_panes import RealWorkspace
from ui.i18n import set_choice_text, set_language, tr

APP, wait_for = support.APP, support.wait_for


class TreeChecks(unittest.TestCase):
    close_window = support.WindowChecks.close_window

    def setUp(self):
        support.WindowChecks.setUp(self)
        self.addCleanup(APP.clipboard().clear)
        self.unexpected_dialogs = []
        self.addCleanup(lambda: self.assertEqual(self.unexpected_dialogs, []))
        from PySide6.QtWidgets import QMessageBox

        for kind in ("warning", "critical"):

            def reject(*args, name=kind, **kwargs):
                self.unexpected_dialogs.append((name, str(args[1:3])))
                return QMessageBox.StandardButton.Cancel

            guard = patch("ui.real_explorer.QMessageBox." + kind, side_effect=reject)
            guard.start()
            self.addCleanup(guard.stop)

    def root(self, name="tree"):
        root = self.base / name
        (root / "src/ui").mkdir(parents=True)
        (root / "docs").mkdir()
        (root / "notes.txt").write_text("notes", encoding="utf-8")
        (root / "src/main.py").write_text("print('hello')", encoding="utf-8")
        (root / "src/ui/icon.bin").write_bytes(b"\0\1")
        (root / "docs/README.md").write_text("# Guide", encoding="utf-8")
        return root

    def load(self, root, page=None):
        page = page or self.window.real_page
        self.assertTrue(page.set_root(root))
        wait_for(lambda: page.model.root.inventory is not None)
        return page

    def select(self, path, page=None):
        page = page or self.window.real_page
        self.assertTrue(page.file_revealer.start(path))
        wait_for(lambda: page.current_preview_path == path)
        wait_for(lambda: not page.preview_is_running)

    def test_layouts_keep_selection_model_and_unsaved_text(self):
        root = self.root()
        page = self.load(root)
        self.select(root / "notes.txt")
        model, selection = page.tree.model(), page.tree.selectionModel()
        page.preview.setPlainText("unsaved text")
        page.preview.document().setModified(True)
        try:
            for mode in ("Tree Focus", "Compact", "Explorer", "Tree Focus"):
                self.assertTrue(page.explorer_layout.set_mode(mode))
                APP.processEvents()
                self.assertIs(page.tree.model(), model)
                self.assertIs(page.tree.selectionModel(), selection)
                self.assertEqual(page.selected_paths(), [root / "notes.txt"])
                self.assertEqual(page.preview.toPlainText(), "unsaved text")
                self.assertTrue(page.preview.document().isModified())
            self.assertTrue(page.folder_panel.isHidden())
            self.assertFalse(page.tree.isHidden())
        finally:
            page.preview.document().setModified(False)

    def test_dual_layouts_and_tree_choices_are_independent(self):
        workspace = self.window.real_workspace
        a, b = self.root("left"), self.root("right")
        self.load(a, workspace.left)
        workspace.left.explorer_layout.set_mode("Tree Focus")
        self.assertTrue(workspace.set_dual(True))
        self.load(b, workspace.right)
        self.assertFalse(workspace.left.navigation_splitter.widget(0).isHidden())
        self.assertFalse(workspace.right.navigation_splitter.widget(0).isHidden())
        workspace.left.explorer_layout.set_mode("Compact")
        workspace.right.explorer_layout.set_panel(0, False)
        self.assertTrue(workspace.left.navigation_splitter.widget(0).isHidden())
        self.assertTrue(workspace.right.navigation_splitter.widget(0).isHidden())
        self.assertTrue(workspace.set_dual(False))
        self.assertEqual(workspace.left.explorer_layout.mode, "Tree Focus")
        self.assertFalse(workspace.left.navigation_splitter.widget(0).isHidden())
        self.assertTrue(workspace.set_dual(True))
        self.assertEqual(workspace.left.explorer_layout.mode, "Compact")
        self.assertTrue(workspace.right.navigation_splitter.widget(0).isHidden())
        workspace.activate(workspace.right)
        self.assertIs(self.window.real_page, workspace.right)
        self.assertEqual(workspace.left.model.root_path, a)
        self.assertEqual(workspace.right.model.root_path, b)

    def test_layout_persistence_and_invalid_settings(self):
        workspace = self.window.real_workspace
        root = self.root()
        self.load(root)
        workspace.left.explorer_layout.set_mode("Tree Focus")
        workspace.left.tree.setColumnWidth(0, 455)
        self.assertTrue(workspace.set_dual(True))
        workspace.left.explorer_layout.set_mode("Compact")
        workspace.right.explorer_layout.set_mode("Tree Focus")
        workspace.save(self.window.settings)
        other = RealWorkspace()

        def cleanup():
            wait_for(other.prepare_close)
            other.finalize_close()
            other.deleteLater()

        self.addCleanup(cleanup)
        other.restore(self.window.settings)
        self.assertTrue(other.dual)
        self.assertEqual(other.left.explorer_layout.mode, "Compact")
        self.assertEqual(other.right.explorer_layout.mode, "Tree Focus")
        self.assertTrue(other.set_dual(False))
        self.assertEqual(other.left.explorer_layout.mode, "Tree Focus")
        layout = other.left.explorer_layout
        document = layout.export_state()
        invalid = json.loads(json.dumps(document))
        invalid["contexts"]["single"]["configurations"]["Explorer"]["visible"] = [
            False,
            False,
            True,
        ]
        self.assertFalse(layout.load_state(invalid))
        self.assertFalse(layout.load_state({"version": 99}))
        self.assertEqual(layout.mode, "Tree Focus")

    def test_nested_reveal_expands_ancestors_without_changing_root(self):
        root = self.root()
        page = self.load(root)
        target = root / "src/ui/icon.bin"
        page.explorer_layout.set_mode("Tree Focus")
        page.jump_to_item(str(target))
        wait_for(lambda: page.current_preview_path == target)
        self.assertEqual(page.model.root_path, root)
        self.assertEqual(page.current_directory, target.parent)
        self.assertIn(str(target), page.explorer_layout.path_label.toolTip())
        for path in (root, root / "src", root / "src/ui"):
            self.assertTrue(
                page.tree.isExpanded(
                    page.browse_model.mapFromSource(page.model.index_for_path(path))
                )
            )
        failures = []
        page.file_revealer.failed.connect(failures.append)
        page.file_revealer.start(root / "missing/unknown.txt")
        wait_for(lambda: not page.file_revealer.timer.isActive())
        self.assertEqual(len(failures), 1)
        self.assertEqual(page.model.root_path, root)

    def test_tree_selection_scopes_listing_and_paste_target(self):
        from real.file_clipboard import file_mime

        root = self.root()
        page = self.load(root)
        folder = root / "src"
        self.select(folder)
        self.assertEqual(page.current_directory, folder)
        self.assertEqual(page._path_from_index(page.folder_view.rootIndex()), folder)
        mime = file_mime([root / "notes.txt"])
        APP.clipboard().setMimeData(mime)
        with patch.object(
            page.file_commands, "transfer", return_value=True
        ) as transfer:
            page.file_commands.paste()
        self.assertEqual(transfer.call_args.args[2], folder)
        self.select(folder / "main.py")
        index = page.browse_model.mapFromSource(
            page.model.index_for_path(folder / "main.py")
        )
        page.folder_view.scrollTo(index)
        APP.processEvents()
        with patch.object(page.file_commands, "build_menu"):
            page.file_commands.show_context(
                page.folder_view, page.folder_view.visualRect(index).center()
            )
        self.assertEqual(page.selected_paths(), [folder / "main.py"])
        self.assertIs(page.tree.selectionModel(), page.folder_view.selectionModel())

    def test_existing_file_view_modes_remain_available(self):
        root = self.root()
        page = self.load(root)
        self.select(root / "notes.txt")
        for mode in ("Details", "List", "Large Icons"):
            set_choice_text(page.folder_panel.mode, mode)
            page.explorer_layout.set_mode("Tree Focus")
            page.explorer_layout.set_mode("Explorer")
            self.assertEqual(page.selected_paths(), [root / "notes.txt"])
            self.assertIs(
                page.folder_icons.selectionModel(), page.tree.selectionModel()
            )
        page.explorer_layout.set_mode("Compact")
        commands = {command.key: command for command in collect_commands(self.window)}
        self.assertTrue(commands["real.delete"].available())
        self.assertTrue(commands["view.real_layout.tree_focus"].available())
        self.assertTrue(
            next(
                a
                for mode, a in self.window.panel_manager.mode_actions
                if mode == "Compact"
            ).isChecked()
        )

    def test_read_only_allows_tree_actions_and_quick_look(self):
        root = self.root()
        page = self.load(root)
        self.select(root / "notes.txt")
        page.explorer_layout.set_mode("Tree Focus")
        self.assertTrue(self.window.set_read_only(True))
        self.assertTrue(page.tree_navigation.start(2, path=root))
        wait_for(lambda: not page.tree_navigation.running)
        commands = {command.key: command for command in collect_commands(self.window)}
        self.assertFalse(commands["real.delete"].available())
        self.assertFalse(commands["real.copy"].available())
        self.window.open_quick_look_from_pane(page)
        dialog = self.window.quick_look_dialog
        self.assertIsNotNone(dialog)
        wait_for(lambda: dialog.session.thread is None)
        self.assertEqual(dialog.text.toPlainText(), "notes")
        dialog.reject()
        wait_for(lambda: self.window.quick_look_dialog is None)
        self.assertTrue((root / "notes.txt").exists())

    def test_preview_reads_do_not_block_path_swap(self):
        workspace = self.window.real_workspace
        a, b = self.root("a"), self.root("b")
        self.load(a)
        self.assertTrue(workspace.set_dual(True))
        self.load(b, workspace.right)
        workspace.left._show_path(a / "notes.txt")
        self.assertIsNotNone(workspace.left.preview_thread)
        self.assertTrue(workspace.swap_paths())
        self.assertEqual(workspace.left.model.root_path, b)
        self.assertTrue(workspace.open_same_path())
        self.assertEqual(workspace.right.model.root_path, b)

    def test_navigation_preserves_dirty_editor_on_cancel_and_layout_switch(self):
        from PySide6.QtWidgets import QMessageBox

        root = self.root()
        page = self.load(root)
        self.select(root / "notes.txt")
        self.window.real_workspace.set_dual(True)
        page.preview.setPlainText("keep this edit")
        page.preview.document().setModified(True)
        try:
            with patch(
                "ui.real_explorer.QMessageBox.warning",
                return_value=QMessageBox.StandardButton.Cancel,
            ) as warning:
                self.assertFalse(self.window.real_workspace.swap_paths())
                warning.assert_called_once()
            self.assertTrue(self.window.real_workspace.set_dual(False))
            self.assertEqual(page.preview.toPlainText(), "keep this edit")
            self.assertTrue(page.preview.document().isModified())
            self.assertEqual(page.model.root_path, root)
        finally:
            page.preview.document().setModified(False)

    def test_menu_ids_and_legacy_aliases_survive_localization(self):
        commands = collect_commands(self.window)
        keys = [command.key for command in commands]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertTrue(
            {
                "workspace.tree_depth",
                "workspace.structure_summary",
                "view.real_layout.tree_focus",
                "real.copy",
                "real.delete",
                "workspace.disk_usage",
                "workspace.recovery_history",
            }
            <= set(keys)
        )
        for key, old_title in (("real.copy", "Copy"), ("real.delete", "Delete")):
            self.assertEqual(
                migrate_keys(["Real File System / " + old_title], commands), [key]
            )
        try:
            set_language("ko")
            self.assertEqual(tr("Tree Focus"), "트리 중심")
            self.assertEqual(
                tr("Reveal selection in tree"), "선택 항목을 트리에서 찾기"
            )
            self.assertEqual(
                migrate_keys(["실제 파일 / 삭제"], collect_commands(self.window)),
                ["real.delete"],
            )
        finally:
            set_language("en")

    def test_scroll_selection_and_columns_survive_layout_switch(self):
        root = self.root()
        for number in range(120):
            (root / f"long_file_name_{number:03}.txt").touch()
        page = self.load(root)
        page.explorer_layout.set_mode("Tree Focus")
        self.select(root / "long_file_name_100.txt")
        page.tree.setColumnWidth(0, 800)
        APP.processEvents()
        page.tree.verticalScrollBar().setValue(
            min(100, page.tree.verticalScrollBar().maximum())
        )
        vertical = page.tree.verticalScrollBar().value()
        page.explorer_layout.set_mode("Compact")
        APP.processEvents()
        page.explorer_layout.set_mode("Tree Focus")
        APP.processEvents()
        self.assertEqual(page.tree.columnWidth(0), 800)
        self.assertEqual(page.tree.verticalScrollBar().value(), vertical)
        self.assertEqual(page.selected_paths(), [root / "long_file_name_100.txt"])

    def test_bounded_depth_expansion_and_subtree_collapse(self):
        root = self.root()
        page = self.load(root)
        for depth in (1, 2, 3):
            self.assertTrue(page.tree_navigation.start(depth, path=root))
            wait_for(lambda: not page.tree_navigation.running)
            for path, level in ((root, 0), (root / "src", 1), (root / "src/ui", 2)):
                index = page.model.index_for_path(path)
                if level < depth:
                    self.assertTrue(index.isValid())
                    self.assertTrue(
                        page.tree.isExpanded(page.browse_model.mapFromSource(index))
                    )
                elif index.isValid():
                    self.assertFalse(
                        page.tree.isExpanded(page.browse_model.mapFromSource(index))
                    )
            self.assertTrue(
                page.tree_navigation.start(8, collapse=True, recursive=True, path=root)
            )
            wait_for(lambda: not page.tree_navigation.running)
            self.assertFalse(
                page.tree.isExpanded(
                    page.browse_model.mapFromSource(page.model.index_for_path(root))
                )
            )
        self.assertFalse(page.tree_navigation.start(9, path=root))

    def test_large_tree_stays_responsive_and_obeys_item_budget(self):
        from PySide6.QtCore import QTimer

        root = self.root()
        for number in range(1600):
            (root / f"item-{number:04}.dat").touch()
        page = self.load(root)
        page.tree_navigation.max_items = 350
        beats = []
        heartbeat = QTimer(self.window)
        heartbeat.setInterval(1)
        heartbeat.timeout.connect(lambda: beats.append(True))
        heartbeat.start()
        before = len(page.model.nodes_by_path)
        self.assertTrue(page.tree_navigation.start(3, path=root))
        wait_for(lambda: not page.tree_navigation.running)
        heartbeat.stop()
        self.assertGreater(len(beats), 1)
        self.assertLessEqual(page.tree_navigation.processed, 350)
        self.assertLessEqual(len(page.model.nodes_by_path) - before, 350)
        self.assertIsNone(page.model.load_budget)
        self.assertIsNone(page.model.load_budget_owner)

    def test_cancel_closes_index_jobs_and_keeps_selection(self):
        import threading

        from real.directory_index import DirectoryIndexJob

        root = self.root()
        slow = root / "slow"
        slow.mkdir()
        page = self.load(root)
        self.select(root / "notes.txt")
        entered = threading.Event()
        original = DirectoryIndexJob.run

        def delay(job):
            if job.node.path == slow:
                entered.set()
                job.cancelled.wait(3)
            original(job)

        with patch.object(DirectoryIndexJob, "run", delay):
            self.assertTrue(page.tree_navigation.start(3, path=root))
            wait_for(entered.is_set)
            page.tree_navigation.cancel()
            self.assertFalse(page.tree_navigation.running)
            self.assertIsNone(page.model.load_budget_owner)
            self.assertEqual(page.selected_paths(), [root / "notes.txt"])
        self.window.close()
        wait_for(lambda: not self.window.isVisible())

    def test_deferred_cleanup_does_not_remove_new_generation_nodes(self):
        root = self.root()
        page = self.load(root)
        target = root / "src/ui/icon.bin"
        self.select(target)
        for _ in range(3):
            wait_for(lambda: not page.preview_is_running)
            page.explorer_state.capture()
            page.refresh_root()
            wait_for(lambda: page.explorer_state.pending is None)
            self.assertEqual(page.selected_paths(), [target])
            wait_for(lambda: not page.model.retired)
            self.assertTrue(page.model.index_for_path(target).isValid())

    def test_summary_matches_snapshot_and_reuses_valid_cache(self):
        from analysis import analyze_snapshot
        from disk_usage import inspect_folder_size
        from snapshot import build_snapshot
        from ui.folder_size import FolderSizeDialog

        root = self.root()
        result = inspect_folder_size(root)
        snapshot = build_snapshot(root)
        try:
            original = analyze_snapshot(snapshot)
            self.assertEqual(
                (result.files, result.folders, result.logical, result.maximum_depth),
                (
                    original.file_count,
                    original.folder_count,
                    original.total_size,
                    original.maximum_depth,
                ),
            )
            self.assertEqual(result.extensions, original.extensions)
        finally:
            snapshot.close()
        self.assertEqual(result.maximum_folder_depth, 2)
        cache = self.window.folder_size_cache
        self.assertTrue(cache.put(result))
        with patch("ui.folder_size.DiskUsageWorker") as worker:
            dialog = FolderSizeDialog(root, cache, self.window, summary=True)
            dialog.show()
            worker.assert_not_called()
            self.assertIs(dialog.result, result)
            self.assertIn("Maximum folder depth: 2", dialog.structure.text())
            dialog.reject()
            dialog.deleteLater()
        revision = cache.revision
        cache.invalidate([self.base / "unrelated"])
        self.assertFalse(cache.changed_since(root, revision))
        cache.invalidate([root / "src/main.py"])
        self.assertTrue(cache.changed_since(root, revision))
        self.assertIsNone(cache.get(root))

    def test_summary_limits_cancellation_partial_results_and_expiry(self):
        import threading

        from disk_usage import FolderSizeCache, inspect_folder_size
        from snapshot import SnapshotCancelled
        from ui.folder_size import FolderSizeDialog

        root = self.root()
        for result in (
            inspect_folder_size(root, max_items=2),
            inspect_folder_size(root, max_depth=0),
        ):
            self.assertTrue(result.limited)
            self.assertFalse(result.complete)
            self.assertFalse(self.window.folder_size_cache.put(result))
        entered = threading.Event()

        def blocked(root, *, cancelled=None, **kwargs):
            entered.set()
            cancelled.wait(3)
            if cancelled.is_set():
                raise SnapshotCancelled
            return inspect_folder_size(root)

        with patch("ui.disk_usage.inspect_folder_size", side_effect=blocked):
            dialog = FolderSizeDialog(
                root, self.window.folder_size_cache, self.window, summary=True
            )
            dialog.show()
            wait_for(entered.is_set)
            dialog.reject()
            wait_for(lambda: dialog.thread is None and not dialog.isVisible())
            self.assertIsNone(self.window.folder_size_cache.get(root))
            dialog.deleteLater()
        now = [0.0]
        cache = FolderSizeCache(ttl=1, clock=lambda: now[0])
        self.assertTrue(cache.put(inspect_folder_size(root)))
        now[0] = 2
        self.assertIsNone(cache.get(root))

    def test_tree_skips_links_and_reports_inaccessible_directories(self):
        import os

        root = self.root()
        denied = root / "denied"
        denied.mkdir()
        try:
            (root / "loop").symlink_to(root, target_is_directory=True)
        except OSError:
            pass
        page = self.load(root)
        original = os.scandir

        def limited(path):
            if str(path) == str(denied):
                raise PermissionError("Synthetic inaccessible directory")
            return original(path)

        with patch("real.directory_index.os.scandir", side_effect=limited):
            self.assertTrue(page.tree_navigation.start(3, path=root))
            wait_for(lambda: not page.tree_navigation.running)
        self.assertGreaterEqual(page.tree_navigation.errors, 1)
        self.assertLess(len(page.model.nodes_by_path), 30)
        loop = page.model.node_from_index(page.model.index_for_path(root / "loop"))
        if loop is not None:
            self.assertTrue(loop.is_link)
            self.assertFalse(loop.is_directory)

    def test_tree_renders_with_native_dpi_and_all_palettes(self):
        root = self.root()
        page = self.load(root)
        page.explorer_layout.set_mode("Tree Focus")
        self.assertGreaterEqual(page.tree.indentation(), 20)
        self.assertEqual(
            page.tree.columnCount()
            if hasattr(page.tree, "columnCount")
            else page.tree.model().columnCount(),
            4,
        )
        for mode in ("Light", "Dark", "System"):
            self.window.theme_manager.apply(mode)
            APP.processEvents()
            pixmap = page.tree.grab()
            self.assertFalse(pixmap.isNull())
            self.assertGreaterEqual(pixmap.devicePixelRatio(), 1)
            self.assertFalse(self.window.windowIcon().isNull())


if __name__ == "__main__":
    unittest.main()
