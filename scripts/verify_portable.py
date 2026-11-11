from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from application_paths import (
    application_directory,
    configure_data_directory,
    data_directory,
    history_path,
    prepare_data_directory,
    resource_path,
    settings_path,
)
from operation_history import HistoryStore
from ui.data_migration import copy_history
from ui.preferences import application_settings


class PortableChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ftv-data-check-")
        self.addCleanup(self.temporary.cleanup)
        configure_data_directory(Path(self.temporary.name) / "portable-data")
        self.addCleanup(configure_data_directory)

    def test_executable_directory_is_distinct_from_resources_and_cwd(self):
        root = Path(self.temporary.name)
        executable = root / "app" / "FileTreeViewer.exe"
        resources = root / "app" / "_internal"
        with (
            patch.object(sys, "frozen", True, create=True),
            patch.object(sys, "executable", str(executable)),
            patch.object(sys, "_MEIPASS", str(resources), create=True),
        ):
            self.assertEqual(application_directory(), executable.parent.resolve())
            self.assertEqual(resource_path("assets"), resources / "assets")
            configure_data_directory()
            self.assertEqual(data_directory(), executable.parent.resolve() / "data")

    def test_atomic_settings_and_history_use_portable_data(self):
        prepare_data_directory()
        settings = application_settings()
        settings.setValue("preferences/language", "ko")
        settings.sync()
        self.assertEqual(settings.status(), settings.Status.NoError)
        self.assertEqual(Path(settings.fileName()), settings_path())
        self.assertEqual(application_settings().value("preferences/language"), "ko")
        HistoryStore().append(
            [{"category": "verification", "action": "portable", "status": "success"}]
        )
        self.assertTrue(history_path().is_file())
        self.assertFalse((data_directory() / "cache").exists())
        self.assertFalse((data_directory() / "logs").exists())

    def test_history_import_preserves_source_and_never_overwrites(self):
        source = Path(self.temporary.name) / "previous.sqlite3"
        store = HistoryStore(source)
        store.append([{"category": "real", "action": "copy", "status": "success"}])
        original = source.read_bytes()
        self.assertTrue(copy_history(source, history_path()))
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(len(HistoryStore().rows()), 1)
        self.assertFalse(copy_history(source, history_path()))
        self.assertEqual(len(HistoryStore().rows()), 1)
        with sqlite3.connect(history_path()) as database:
            self.assertEqual(database.execute("PRAGMA quick_check").fetchone(), ("ok",))

    def test_existing_legacy_preferences_are_not_used_as_a_fallback(self):
        settings = application_settings()
        self.assertFalse(settings.fallbacksEnabled())
        self.assertTrue(settings.isAtomicSyncRequired())

    @unittest.skipIf(os.name == "nt", "Windows write permissions are governed by ACLs")
    def test_read_only_folder_reports_error(self):
        directory = data_directory()
        directory.mkdir()
        directory.chmod(0o555)
        try:
            with self.assertRaises(PermissionError):
                prepare_data_directory()
        finally:
            directory.chmod(0o755)


if __name__ == "__main__":
    unittest.main()
