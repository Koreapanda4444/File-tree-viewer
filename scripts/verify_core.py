"""Temporary-directory regression checks; never operate on user-selected files."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from file_content import read_file_bytes
from file_operations import FileOperationTask, create_item, rename_item
from folder_compare import compare_folders
from fs_ops import OperationCancelled, copy_exclusive, transaction_lock
from plan_apply import ApplicationStatus, apply_plan, undo_application
from planning import FilePlan
from recovery import inspect_recovery
from snapshot import SnapshotCancelled, build_snapshot
from sync_apply import apply_sync_plan, recover_sync_group
from sync_plan import plan_bidirectional_sync, plan_one_way_sync
from tree_export import (
    TreeExportOptions,
    build_tree,
    tree_lines,
    write_tree,
)


class FilesystemChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ftv-verification-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def file(self, path, content="content"):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def snapshot(self, root=None):
        result = build_snapshot(root or self.root)
        self.addCleanup(result.close)
        return result

    def test_create_rename_move_undo_and_conflicts(self):
        record = create_item(self.root, "original.txt", directory=False)
        source = record.changes[0].target
        rename = rename_item(source, "renamed.txt")
        changes, errors = FileOperationTask("undo", record=rename).run()
        self.assertFalse(errors)
        self.assertEqual(len(changes), 1)
        self.assertTrue(source.exists())
        with self.assertRaises(FileExistsError):
            create_item(self.root, source.name, directory=False)
        destination = self.root / "destination"
        destination.mkdir()
        moved, errors = FileOperationTask(
            "move", sources=[source], destination=destination
        ).run()
        self.assertFalse(errors)
        self.assertFalse(source.exists())
        self.assertTrue(moved[0].target.exists())

    def test_copy_conflict_preserves_destination(self):
        source = self.file("source/item.txt", "original")
        target = self.file("target/item.txt", "keep")
        changes, errors = FileOperationTask(
            "copy", sources=[source], destination=target.parent, conflict_policy="skip"
        ).run()
        self.assertFalse(changes)
        self.assertFalse(errors)
        self.assertEqual(target.read_text(), "keep")

    def test_changed_source_is_rejected(self):
        source = self.file("source.txt")
        destination = self.root / "destination"
        destination.mkdir()
        task = FileOperationTask("copy", sources=[source], destination=destination)
        source.write_text("changed externally")
        changes, errors = task.run()
        self.assertFalse(changes)
        self.assertTrue(errors)
        self.assertFalse((destination / source.name).exists())

    def test_cancelled_copy_preserves_source(self):
        source = self.file("source.txt")
        event = threading.Event()
        event.set()
        with self.assertRaises(OperationCancelled):
            copy_exclusive(source, self.root / "copy.txt", cancelled=event)
        self.assertTrue(source.exists())
        self.assertFalse((self.root / "copy.txt").exists())

    def test_handle_permission_and_creation_metadata_do_not_change_identity(self):
        source = self.file("metadata.txt", "content")
        original = os.fstat

        def metadata(descriptor):
            details = original(descriptor)
            return SimpleNamespace(
                st_dev=details.st_dev,
                st_ino=details.st_ino,
                st_mode=details.st_mode ^ 0o100,
                st_size=details.st_size,
                st_mtime_ns=details.st_mtime_ns,
                st_ctime_ns=details.st_ctime_ns + 100,
            )

        with patch("file_content.os.fstat", side_effect=metadata):
            self.assertEqual(read_file_bytes(source, limit=1024).data, b"content")

    def test_replaced_file_handle_is_rejected(self):
        source = self.file("identity.txt")
        original = os.fstat

        def metadata(descriptor):
            details = original(descriptor)
            return SimpleNamespace(
                st_dev=details.st_dev,
                st_ino=details.st_ino + 1,
                st_mode=details.st_mode,
                st_size=details.st_size,
                st_mtime_ns=details.st_mtime_ns,
                st_ctime_ns=details.st_ctime_ns,
            )

        with (
            patch("file_content.os.fstat", side_effect=metadata),
            self.assertRaises(FileExistsError),
        ):
            read_file_bytes(source, limit=1024)

    def test_modified_copy_cannot_be_undone(self):
        source = self.file("source/nested/item.txt", "original")
        target = self.root / "destination"
        target.mkdir()
        changes, errors = FileOperationTask(
            "copy", sources=[source.parent], destination=target
        ).run()
        self.assertFalse(errors)
        changed = changes[0].target / "item.txt"
        changed.write_text("external")
        from file_operations import OperationRecord

        undone, errors = FileOperationTask(
            "undo", record=OperationRecord("copy", changes)
        ).run()
        self.assertFalse(undone)
        self.assertTrue(errors)
        self.assertEqual(changed.read_text(), "external")

    def test_plan_apply_and_undo_preserve_journal(self):
        self.file("rename.txt", "rename")
        self.file("remove.txt", "preserve in backup")
        snapshot = self.snapshot()
        plan = FilePlan(self.root)
        plan.add_rename("rename.txt", "renamed.txt")
        plan.add_delete("remove.txt")
        plan.add_create_folder("created")
        record = apply_plan(snapshot, plan)
        self.assertIs(record.status, ApplicationStatus.COMPLETED)
        self.assertFalse((self.root / "remove.txt").exists())
        self.assertTrue(
            record.journal_path.is_relative_to(self.root / ".file-tree-viewer")
        )
        undo_application(record)
        self.assertEqual((self.root / "remove.txt").read_text(), "preserve in backup")
        self.assertTrue((self.root / "rename.txt").exists())
        self.assertFalse((self.root / "created").exists())
        self.assertFalse(inspect_recovery(self.root).needs_attention)

    def test_plan_cancellation_rolls_back(self):
        snapshot = self.snapshot()
        plan = FilePlan(self.root)
        plan.add_create_file("first.txt")
        plan.add_create_file("second.txt")
        event = threading.Event()

        def progress(phase, *args):
            if phase == "Creating files":
                event.set()

        record = apply_plan(snapshot, plan, cancelled=event, progress=progress)
        self.assertIs(record.status, ApplicationStatus.ROLLED_BACK)
        self.assertFalse((self.root / "first.txt").exists())
        self.assertFalse((self.root / "second.txt").exists())

    def test_plan_changed_source_is_preserved(self):
        source = self.file("original.txt", "before")
        snapshot = self.snapshot()
        plan = FilePlan(self.root)
        plan.add_rename("original.txt", "after.txt")
        source.write_text("external modification")
        try:
            record = apply_plan(snapshot, plan)
        except (ValueError, FileExistsError):
            pass
        else:
            self.assertIsNot(record.status, ApplicationStatus.COMPLETED)
        self.assertEqual(source.read_text(), "external modification")

    def test_transaction_lock_blocks_concurrent_plan(self):
        snapshot = self.snapshot()
        plan = FilePlan(self.root)
        plan.add_create_file("blocked.txt")
        with transaction_lock(self.root), self.assertRaises(RuntimeError):
            apply_plan(snapshot, plan)
        self.assertFalse((self.root / "blocked.txt").exists())

    def comparison(self):
        left, right = self.root / "left", self.root / "right"
        left.mkdir(exist_ok=True)
        right.mkdir(exist_ok=True)
        result = compare_folders(left, right, verify_content=True)
        self.addCleanup(result.close)
        return result

    def test_sync_apply_undo_and_conflict(self):
        self.file("left/item.txt", "left")
        self.file("right/keep.txt", "keep")
        comparison = self.comparison()
        plan = plan_one_way_sync(comparison)
        self.addCleanup(plan.close)
        self.assertTrue(plan.can_apply, plan.issues)
        result = apply_sync_plan(plan)
        self.assertEqual(
            result.status, "completed", (result.error, result.recovery_errors)
        )
        self.assertEqual((self.root / "right/item.txt").read_text(), "left")
        self.assertEqual((self.root / "right/keep.txt").read_text(), "keep")
        recover_sync_group(result.journals[0].parent / "sync.json", undo=True)
        self.assertFalse((self.root / "right/item.txt").exists())

    def test_bidirectional_conflict_blocks_application(self):
        self.file("left/item.txt", "left")
        self.file("right/item.txt", "right")
        comparison = self.comparison()
        plan = plan_bidirectional_sync(comparison, initialize=True)
        self.addCleanup(plan.close)
        self.assertFalse(plan.can_apply)
        with self.assertRaises(ValueError):
            apply_sync_plan(plan)

    def test_tree_formats_exclusions_unicode_and_cancel(self):
        self.file("폴더/파일.txt", "abc")
        self.file("omit.log")
        self.file(".hidden")
        options = TreeExportOptions(excluded_extensions=("log",), show_sizes=True)
        document = build_tree(self.root, options)
        snapshot_document = build_tree(self.snapshot(), options, kind="snapshot")
        self.assertEqual(document, snapshot_document)
        self.assertIn("파일.txt (3 B)", "\n".join(tree_lines(document)))
        for format in ("txt", "md", "json"):
            destination = self.root / f"export.{format}"
            write_tree(document, destination, format)
            with self.assertRaises(FileExistsError):
                write_tree(document, destination, format)
        self.assertEqual(
            json.loads((self.root / "export.json").read_text(encoding="utf-8"))[
                "format"
            ],
            "file-tree-viewer-tree",
        )
        event = threading.Event()
        event.set()
        with self.assertRaises(SnapshotCancelled):
            build_tree(self.root, cancelled=event)


if __name__ == "__main__":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    unittest.main()
