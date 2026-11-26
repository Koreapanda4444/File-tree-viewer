"""Archive, Quick Look and folder inspection checks on disposable fixtures."""

from __future__ import annotations

import os
import stat
import struct
import sys
import tempfile
import threading
import unittest
import warnings
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from archive import ArchiveLimits, extract_members, index_archive, read_member
from snapshot import SnapshotCancelled
from write_policy import ReadOnlyError, policy


class ArchiveChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ftv-archive-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.addCleanup(lambda: policy.set_read_only(False))

    def archive(self, members, *, compression=zipfile.ZIP_STORED):
        path = self.root / "한글 archive.zip"
        with (
            warnings.catch_warnings(),
            zipfile.ZipFile(path, "w", compression=compression) as archive,
        ):
            warnings.simplefilter("ignore", UserWarning)
            for name, data in members:
                archive.writestr(name, data)
        return path

    def test_tree_preview_and_selected_extraction(self):
        path = self.archive(
            (
                ("자료/hello world.txt", "안녕하세요"),
                ("empty/", ""),
                ("other.bin", b"\0\1"),
            )
        )
        original = path.read_bytes()
        index = index_archive(path)
        self.assertTrue(index.entries["자료"].directory)
        content = read_member(index, "자료/hello world.txt")
        self.assertEqual(content.data.decode(), "안녕하세요")
        self.assertTrue(content.crc_checked)
        limited = read_member(index, "자료/hello world.txt", limit=3)
        self.assertTrue(limited.truncated)
        self.assertFalse(limited.crc_checked)
        output = extract_members(index, ("자료", "empty"), self.root / "new output")
        self.assertEqual(
            (output / "자료/hello world.txt").read_text(encoding="utf-8"), "안녕하세요"
        )
        self.assertTrue((output / "empty").is_dir())
        self.assertFalse((output / "other.bin").exists())
        self.assertEqual(path.read_bytes(), original)
        with self.assertRaises(FileExistsError):
            extract_members(index, ("",), output)

    def test_invalid_and_colliding_paths_are_rejected(self):
        invalid = (
            "../escape",
            "/absolute",
            "C:relative",
            "C:/drive",
            "\\\\server\\share\\file",
            "a//b",
            "a/./b",
            "a/../b",
            "CON.txt",
            "NUL",
            "name.",
            "name ",
            "a:stream",
            ".file-tree-viewer/journal",
            "a/.file-tree-viewer/state",
        )
        for name in invalid:
            with self.subTest(name=name), self.assertRaises(ValueError):
                index_archive(self.archive(((name, "unsafe"),)))
        for members in (
            (("A/file", "1"), ("a/other", "2")),
            (("same", "1"), ("same", "2")),
            (("file", "1"), ("file/child", "2")),
            (("é.txt", "1"), ("e\u0301.txt", "2")),
        ):
            with self.subTest(members=members), self.assertRaises(ValueError):
                index_archive(self.archive(members))
        path = self.archive((("badXname", "1"),))
        path.write_bytes(path.read_bytes().replace(b"badXname", b"bad\0name"))
        with self.assertRaises(ValueError):
            index_archive(path)

    def test_metadata_limits_ratio_and_special_entries(self):
        path = self.archive((("a/b/c/d", "x"),))
        with self.assertRaises(ValueError):
            index_archive(path, limits=ArchiveLimits(max_entries=2))
        with self.assertRaises(ValueError):
            index_archive(path, limits=ArchiveLimits(max_directory_bytes=10))
        path = self.archive(
            (("bomb", b"A" * (2 * 1024**2)),), compression=zipfile.ZIP_DEFLATED
        )
        index = index_archive(path)
        self.assertIn("ratio", index.entries["bomb"].blocked)
        with self.assertRaises(ValueError):
            read_member(index, "bomb")
        info = zipfile.ZipInfo("link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        index = index_archive(self.archive(((info, "../target"),)))
        with self.assertRaises(ValueError):
            extract_members(index, ("",), self.root / "blocked")
        path = self.archive((("a", "123456"), ("b", "123456")))
        index = index_archive(path, limits=ArchiveLimits(max_extract_bytes=10))
        with self.assertRaises(ValueError):
            extract_members(index, ("",), self.root / "too big")
        self.assertFalse((self.root / "too big").exists())

    def test_crc_damage_and_cancellation_leave_no_output(self):
        path = self.archive((("payload", b"CRC-CONTENT-UNIQUE"),))
        path.write_bytes(
            path.read_bytes().replace(b"CRC-CONTENT-UNIQUE", b"BAD-CONTENT-UNIQUE", 1)
        )
        index = index_archive(path)
        with self.assertRaises(zipfile.BadZipFile):
            read_member(index, "payload")
        with self.assertRaises(zipfile.BadZipFile):
            extract_members(index, ("",), self.root / "broken")
        self.assertFalse((self.root / "broken").exists())
        path = self.archive((("payload", os.urandom(1024**2)),))
        index = index_archive(path)
        event = threading.Event()
        with self.assertRaises(SnapshotCancelled):
            extract_members(
                index,
                ("",),
                self.root / "cancelled",
                cancelled=event,
                progress=lambda *args: event.set(),
            )
        self.assertFalse((self.root / "cancelled").exists())
        self.assertEqual(list(self.root.glob(".ftv-extract-*")), [])

    def test_changed_archive_existing_output_and_read_only(self):
        index = index_archive(self.archive((("file.txt", "safe"),)))
        policy.set_read_only(True)
        self.assertEqual(read_member(index, "file.txt").data, b"safe")
        with self.assertRaises(ReadOnlyError):
            extract_members(index, ("",), self.root / "forbidden")
        policy.set_read_only(False)
        target = self.root / "existing"
        target.mkdir()
        (target / "keep").write_text("keep")
        with self.assertRaises(FileExistsError):
            extract_members(index, ("",), target)
        self.assertEqual((target / "keep").read_text(), "keep")
        index.path.write_bytes(b"changed")
        with self.assertRaises(OSError):
            read_member(index, "file.txt")

    def test_malformed_encrypted_unsupported_and_zip64(self):
        path = self.root / "bad.zip"
        path.write_bytes(b"not a zip")
        with self.assertRaises(zipfile.BadZipFile):
            index_archive(path)
        for flag, method, reason in ((1, 0, "Encrypted"), (0, 99, "Unsupported")):
            path = self.archive((("file", "content"),))
            data = bytearray(path.read_bytes())
            central = data.index(b"PK\x01\x02")
            struct.pack_into("<H", data, 6, flag)
            struct.pack_into("<H", data, central + 8, flag)
            struct.pack_into("<H", data, 8, method)
            struct.pack_into("<H", data, central + 10, method)
            path.write_bytes(data)
            self.assertIn(reason, index_archive(path).entries["file"].blocked)
        path = self.archive((("file", "zip64"),))
        data = path.read_bytes()
        eocd_offset = data.rfind(b"PK\x05\x06")
        record = struct.unpack("<4s4H2LH", data[eocd_offset:])
        size, offset = record[5:7]
        z64 = struct.pack(
            "<4sQHHLLQQQQ", b"PK\x06\x06", 44, 45, 45, 0, 0, 1, 1, size, offset
        )
        locator = struct.pack("<4sLQL", b"PK\x06\x07", 0, eocd_offset, 1)
        eocd = struct.pack(
            "<4s4H2LH", b"PK\x05\x06", 0, 0, 0xFFFF, 0xFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0
        )
        path.write_bytes(data[:eocd_offset] + z64 + locator + eocd)
        self.assertEqual(read_member(index_archive(path), "file").data, b"zip64")

    def test_output_race_and_linked_destination_preserve_existing_data(self):
        index = index_archive(self.archive((("file", "content"),)))
        destination = self.root / "appears"

        def race(done, total, name):
            if not destination.exists():
                destination.mkdir()
                (destination / "keep").write_text("external")

        with self.assertRaises(FileExistsError):
            extract_members(index, ("",), destination, progress=race)
        self.assertEqual((destination / "keep").read_text(), "external")
        self.assertEqual(list(self.root.glob(".ftv-extract-*")), [])
        linked = self.root / "linked"
        try:
            linked.symlink_to(destination, target_is_directory=True)
        except OSError:
            return
        with self.assertRaises((OSError, ValueError)):
            extract_members(index, ("",), linked / "output")
        self.assertFalse((destination / "output").exists())


class FolderSizeChecks(unittest.TestCase):
    setUp = ArchiveChecks.setUp

    def test_empty_nested_and_hard_link_sizes(self):
        from disk_usage import inspect_folder_size

        empty = inspect_folder_size(self.root)
        self.assertEqual((empty.logical, empty.files, empty.folders), (0, 0, 0))
        folder = self.root / "nested"
        folder.mkdir()
        path = self.root / "file"
        path.write_bytes(b"1234567")
        try:
            os.link(path, folder / "hard")
        except OSError:
            self.skipTest("Hard links unavailable")
        result = inspect_folder_size(self.root)
        self.assertEqual(
            (result.logical, result.unique, result.files, result.folders), (14, 7, 2, 1)
        )
        self.assertTrue(result.complete)
        self.assertTrue(result.completed >= result.started)

    def test_links_errors_and_changed_files_are_partial(self):
        from unittest.mock import patch

        from disk_usage import inspect_folder_size

        path = self.root / "file"
        path.write_bytes(b"123")
        for number in range(260):
            (self.root / str(number)).write_bytes(b"x")
        modified = False

        def change(phase, count):
            nonlocal modified
            if not modified and phase == "Scanning disk usage":
                for item in self.root.iterdir():
                    item.write_bytes(b"changed externally")
                modified = True

        result = inspect_folder_size(self.root, progress=change)
        self.assertTrue(result.changed)
        self.assertFalse(result.complete)
        original = os.scandir

        def denied(folder):
            if not isinstance(folder, int) and Path(folder) == self.root / "denied":
                raise PermissionError("test permission denial")
            return original(folder)

        (self.root / "denied").mkdir()
        with patch("disk_usage.os.scandir", side_effect=denied):
            result = inspect_folder_size(self.root)
        self.assertGreater(result.errors, 0)
        self.assertFalse(result.complete)
        link = self.root / "loop"
        try:
            link.symlink_to(self.root, target_is_directory=True)
        except OSError:
            return
        result = inspect_folder_size(self.root)
        self.assertEqual(result.skipped, 1)
        self.assertFalse(result.complete)

    def test_cancelled_folder_inspection_and_read_only(self):
        from disk_usage import inspect_folder_size

        event = threading.Event()
        for number in range(300):
            (self.root / str(number)).touch()
        with self.assertRaises(SnapshotCancelled):
            inspect_folder_size(
                self.root, cancelled=event, progress=lambda *_: event.set()
            )
        policy.set_read_only(True)
        self.assertEqual(inspect_folder_size(self.root).files, 300)


import verify_enhancements as support


class ExplorationChecks(unittest.TestCase):
    setUp = support.WindowChecks.setUp
    close_window = support.WindowChecks.close_window

    def archive_dialog(self):
        from ui.archive_browser import ArchiveDialog

        path = self.base / "자료 test.zip"
        with zipfile.ZipFile(path, "w") as output:
            output.writestr("folder/hello.txt", "Hello ZIP")
            output.writestr("binary.bin", b"\0\1\2")
        dialog = ArchiveDialog(path, self.window)
        dialog.show()

        def cleanup():
            dialog.reject()
            support.wait_for(
                lambda: dialog.thread is None and dialog.preview_thread is None
            )
            dialog.deleteLater()
            support.APP.processEvents()

        self.addCleanup(cleanup)
        support.wait_for(lambda: dialog.thread is None)
        self.assertIsNotNone(dialog.archive, dialog.status.text())
        return dialog

    def test_archive_preview_extraction_and_read_only(self):
        dialog = self.archive_dialog()
        dialog.navigate("folder")
        dialog.files.setCurrentIndex(dialog.file_model.index_for("folder/hello.txt"))
        support.wait_for(lambda: dialog.preview_thread is None)
        self.assertEqual(dialog.preview.toPlainText(), "Hello ZIP")
        self.assertTrue(dialog.preview.isReadOnly())
        self.assertTrue(dialog.extract_selection.isEnabled())
        self.assertTrue(self.window.set_read_only(True))
        self.assertFalse(dialog.extract_all.isEnabled())
        self.assertFalse(dialog.start_extraction([""], self.base / "forbidden"))
        self.assertTrue(self.window.set_read_only(False))
        self.assertTrue(dialog.start_extraction(["folder"], self.base / "output"))
        self.assertFalse(self.window.set_read_only(True))
        support.wait_for(lambda: dialog.thread is None)
        self.assertEqual(
            (self.base / "output/folder/hello.txt").read_text(), "Hello ZIP"
        )
        self.assertFalse((self.base / "output/binary.bin").exists())
        dialog.path.unlink()
        dialog.check_source()
        self.assertTrue(dialog.stale)
        self.assertEqual(dialog.preview.toPlainText(), "")
        self.assertFalse(dialog.extract_all.isEnabled())

    def test_quick_look_shortcuts_navigation_and_input(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        root = self.base / "quick files"
        root.mkdir()
        for number in range(270):
            (root / f"{number:03}.txt").write_text(str(number))
        page = self.window.real_page
        page.set_root(root)
        support.wait_for(lambda: page.model.root.inventory is not None)
        page.file_revealer.start(root / "255.txt")
        support.wait_for(lambda: page.current_preview_path == root / "255.txt")
        page.folder_panel.active_view.setFocus()
        support.APP.processEvents()
        QTest.keyClick(page.folder_panel.active_view, Qt.Key.Key_Space)
        support.wait_for(lambda: self.window.quick_look_dialog is not None)
        dialog = self.window.quick_look_dialog
        support.wait_for(lambda: dialog.session.thread is None)
        self.assertEqual(dialog.text.toPlainText(), "255")
        QTest.keyClick(dialog.text, Qt.Key.Key_Right)
        support.wait_for(
            lambda: (
                dialog.session.thread is None
                and page.current_preview_path == root / "256.txt"
            )
        )
        self.assertEqual(dialog.text.toPlainText(), "256")
        self.assertTrue(dialog.text.isReadOnly())
        QTest.keyClick(dialog.text, Qt.Key.Key_Space)
        support.wait_for(lambda: self.window.quick_look_dialog is None)
        for field in (page.location, page.search_input, page.name_input, page.preview):
            field.setFocus()
            QTest.keyClick(field, Qt.Key.Key_Space)
            self.assertIsNone(self.window.quick_look_dialog)
        page.preview.document().setModified(False)

    def test_quick_look_dual_pane_and_stale_source(self):
        root = self.base / "quick"
        root.mkdir()
        path = root / "sample.txt"
        path.write_text("left")
        page = self.window.real_page
        page.set_root(root)
        support.wait_for(lambda: not page.preview_is_running)
        self.assertTrue(self.window.real_workspace.set_dual(True))
        right = self.window.real_workspace.right
        other = self.base / "right"
        other.mkdir()
        target = other / "other.bin"
        target.write_bytes(b"\0\1\2")
        right.set_root(other)
        right.file_revealer.start(target)
        support.wait_for(lambda: right.current_preview_path == target)
        self.window.open_quick_look_from_pane(right)
        dialog = self.window.quick_look_dialog
        support.wait_for(lambda: dialog.session.thread is None)
        self.assertEqual(dialog.path, target)
        self.assertIn("00000000", dialog.text.toPlainText())
        target.unlink()
        dialog.session.check_source()
        self.assertEqual(dialog.text.toPlainText(), "")
        self.assertIsNone(dialog.session.result)
        dialog.reject()
        support.wait_for(lambda: self.window.quick_look_dialog is None)

    def test_archive_close_during_preview(self):
        dialog = self.archive_dialog()
        for key in ["binary.bin", "folder/hello.txt"] * 5:
            dialog.request_preview(key)
        dialog.reject()
        support.wait_for(lambda: not dialog.isVisible())
        self.assertIsNone(dialog.preview_thread)
        self.assertIsNone(dialog.thread)


if __name__ == "__main__":
    unittest.main()
