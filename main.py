from __future__ import annotations

import argparse
import sys

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
    _, qt_arguments = parser.parse_known_args()
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
    import_previous_data(startup=True)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
