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


if __name__ == "__main__":
    unittest.main()
