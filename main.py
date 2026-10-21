from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from ui.window import MainWindow


def main() -> int:
    parser = argparse.ArgumentParser(description="File Tree Viewer desktop application")
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="check workspace startup and shutdown with temporary preferences",
    )
    options, qt_arguments = parser.parse_known_args()
    if options.smoke_test:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication([sys.argv[0], *qt_arguments])
    app.setApplicationName("FileTreeViewer")
    app.setOrganizationName("FileTreeViewer")
    if options.smoke_test:
        with tempfile.TemporaryDirectory(
            prefix="file-tree-viewer-startup-"
        ) as directory:
            QSettings.setDefaultFormat(QSettings.Format.IniFormat)
            QSettings.setPath(
                QSettings.Format.IniFormat, QSettings.Scope.UserScope, directory
            )
            window = MainWindow()
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
        return 0
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
