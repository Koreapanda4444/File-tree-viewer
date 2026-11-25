from __future__ import annotations

import argparse
import os
import sys
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QMessageBox

from application_paths import data_directory, prepare_data_directory
from branding import set_windows_application_id
from ui.data_migration import import_previous_data
from ui.window import MainWindow
from version import __version__


def main() -> int:
    parser = argparse.ArgumentParser(description="File Tree Viewer desktop application")
    parser.add_argument(
        "--verify-startup",
        action="store_true",
        help="run the isolated packaging startup check",
    )
    options, qt_arguments = parser.parse_known_args()
    if options.verify_startup:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    set_windows_application_id()
    app = QApplication([sys.argv[0], *qt_arguments])
    app.setApplicationVersion(__version__)
    app.setApplicationName("FileTreeViewer")
    app.setOrganizationName("FileTreeViewer")
    try:
        prepare_data_directory()
    except OSError as error:
        QMessageBox.critical(
            None,
            "Portable data folder is not writable",
            f"File Tree Viewer could not write to:\n{data_directory()}\n\nMove the entire program folder to a writable location, then try again.\n\n{error}",
        )
        return 1
    if not options.verify_startup:
        import_previous_data(startup=True)
    window = MainWindow()
    window.show()
    if options.verify_startup:
        try:
            if window.windowIcon().isNull():
                raise RuntimeError("Application icon is missing")
            app.processEvents()
            for index in range(window.tabs.count()):
                window.tabs.setCurrentIndex(index)
                app.processEvents()
            window.tabs.setCurrentIndex(0)
            if not window.real_workspace.set_dual(True):
                raise RuntimeError("Dual pane initialization failed")
            window.real_workspace.focus_other()
            if not window.set_read_only(True) or not window.set_read_only(False):
                raise RuntimeError("Read-only mode transition failed")
            window.real_workspace.set_dual(False)
            window.close()
            deadline = time.monotonic() + 30
            while window.isVisible() and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.005)
            if window.isVisible():
                raise RuntimeError("Application shutdown timed out")
            return 0
        except (OSError, RuntimeError, ValueError, TypeError, AttributeError) as error:
            if sys.stderr is not None:
                print(str(error), file=sys.stderr)
            return 1
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
