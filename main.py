from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QMessageBox

from application_paths import (
    application_directory,
    configure_data_directory,
    data_directory,
    prepare_data_directory,
    settings_path,
)
from branding import set_windows_application_id
from operation_history import HistoryStore
from ui.data_migration import import_previous_data
from ui.preferences import application_settings
from ui.window import MainWindow
from version import __version__


def main() -> int:
    parser = argparse.ArgumentParser(description="File Tree Viewer desktop application")
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="check workspace startup and shutdown with temporary preferences",
    )
    parser.add_argument(
        "--verify-portable",
        action="store_true",
        help="verify real executable-relative storage during an isolated packaging check",
    )
    options, qt_arguments = parser.parse_known_args()
    if options.verify_portable and not options.smoke_test:
        parser.error("--verify-portable requires --smoke-test")
    if options.smoke_test:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    set_windows_application_id()
    app = QApplication([sys.argv[0], *qt_arguments])
    app.setApplicationVersion(__version__)
    app.setApplicationName("FileTreeViewer")
    app.setOrganizationName("FileTreeViewer")
    if options.smoke_test:
        with tempfile.TemporaryDirectory(
            prefix="file-tree-viewer-startup-"
        ) as directory:
            configure_data_directory(
                None if options.verify_portable else Path(directory) / "data"
            )
            prepare_data_directory()
            settings = application_settings()
            previous_language = settings.value("preferences/language", "en")
            for language in ("en", "ko"):
                settings.setValue("preferences/language", language)
                settings.sync()
                window = MainWindow()
                if window.windowIcon().isNull():
                    raise RuntimeError("Application icon is missing")
                window.show()
                for index in range(window.tabs.count()):
                    window.tabs.setCurrentIndex(index)
                    app.processEvents()
                window.close()
                deadline = time.monotonic() + 10
                while window.isVisible() and time.monotonic() < deadline:
                    app.processEvents()
                    time.sleep(0.005)
                if window.isVisible():
                    raise RuntimeError("Workspace shutdown did not complete")
                app.processEvents()
            settings.setValue("preferences/language", previous_language)
            settings.sync()
            if options.verify_portable:
                if data_directory() != application_directory() / "data":
                    raise RuntimeError("Data path is not executable-relative")
                if not settings_path().is_file():
                    raise RuntimeError("Portable settings were not saved")
                if not settings.contains("verification/portable_token"):
                    settings.setValue("verification/portable_token", "portable-1.0.0")
                    settings.sync()
                HistoryStore().append(
                    [
                        {
                            "category": "verification",
                            "action": "portable-startup",
                            "status": "success",
                        }
                    ]
                )
            configure_data_directory()
        return 0
    try:
        prepare_data_directory()
    except OSError as error:
        QMessageBox.critical(
            None,
            "Portable data folder is not writable",
            f"File Tree Viewer could not write to:\n{data_directory()}\n\nMove the entire program folder to a writable location, then try again.\n\n{error}",
        )
        return 1
    import_previous_data(startup=True)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
