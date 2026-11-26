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


def verify_exploration(app, window):
    import tempfile
    import zipfile
    from pathlib import Path

    from PySide6.QtGui import QImage, QPainter, QPdfWriter

    from archive import extract_members
    from disk_usage import FolderSizeCache
    from ui.archive_browser import ArchiveDialog
    from ui.folder_size import FolderSizeDialog
    from ui.quick_look import QuickLookDialog

    def wait(condition):
        deadline = time.monotonic() + 20
        while not condition() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.002)
        if not condition():
            raise RuntimeError("Exploration verification timed out")

    errors, dialogs = [], []
    original_hook = sys.excepthook
    sys.excepthook = lambda kind, value, traceback: errors.append(str(value))
    temporary = tempfile.TemporaryDirectory(prefix="ftv-exploration-")
    try:
        root = Path(temporary.name)
        (root / "text.txt").write_text("Quick Look 한글", encoding="utf-8")
        (root / "binary.bin").write_bytes(b"\0\1\2")
        image = QImage(80, 40, QImage.Format.Format_RGB32)
        image.fill(0xFF3B7957)
        if not image.save(str(root / "image.png")):
            raise RuntimeError("PNG encoder is missing")
        writer = QPdfWriter(str(root / "document.pdf"))
        painter = QPainter(writer)
        painter.drawText(100, 200, "PDF Quick Look")
        painter.end()
        del painter, writer
        with zipfile.ZipFile(root / "자료.zip", "w") as archive:
            archive.write(root / "text.txt", "folder/text.txt")
            archive.writestr("empty/", "")
        archive = ArchiveDialog(root / "자료.zip", window)
        dialogs.append(archive)
        archive.show()
        wait(lambda: archive.thread is None)
        if archive.archive is None:
            raise RuntimeError(archive.status.text())
        archive.navigate("folder")
        archive.request_preview("folder/text.txt")
        wait(lambda: archive.preview_thread is None)
        if archive.preview.toPlainText() != "Quick Look 한글":
            raise RuntimeError("ZIP content preview failed")
        extract_members(archive.archive, [""], root / "extracted")
        if (root / "extracted/folder/text.txt").read_text(
            encoding="utf-8"
        ) != "Quick Look 한글":
            raise RuntimeError("ZIP extraction failed")
        archive.reject()
        for name in ("text.txt", "binary.bin", "image.png", "document.pdf"):
            look = QuickLookDialog(root / name, window)
            dialogs.append(look)
            look.show()
            wait(lambda current=look: current.session.thread is None)
            if look.session.result is None:
                raise RuntimeError(look.status.text())
            if name.endswith((".png", ".pdf")) and look.session.result.image is None:
                raise RuntimeError("Packaged image/PDF decoder is missing")
            look.reject()
        folder = FolderSizeDialog(
            root / "extracted", FolderSizeCache(), window, summary=True
        )
        dialogs.append(folder)
        folder.show()
        wait(lambda: folder.thread is None)
        if (
            folder.result is None
            or not folder.result.complete
            or folder.result.files != 1
        ):
            raise RuntimeError("Packaged folder inspection failed")
        folder.reject()
        if folder.result.maximum_folder_depth < 1 or not folder.result.extensions:
            raise RuntimeError("Packaged subtree summary failed")
        page = window.real_page
        if not page.set_root(root):
            raise RuntimeError("Tree fixture root failed")
        for mode in ("Tree Focus", "Compact", "Explorer", "Tree Focus"):
            if not page.explorer_layout.set_mode(mode):
                raise RuntimeError("Packaged explorer layout failed")
        page.jump_to_item(str(root / "extracted/folder/text.txt"))
        wait(lambda: page.current_preview_path == root / "extracted/folder/text.txt")
        wait(lambda: not page.preview_is_running)
        if page.model.root_path != root:
            raise RuntimeError("Path reveal changed the explorer root")
        if not page.tree_navigation.start(2, path=root):
            raise RuntimeError("Packaged bounded tree expansion failed")
        wait(lambda: not page.tree_navigation.running)
        if not window.real_workspace.set_dual(True):
            raise RuntimeError("Tree dual-pane mode failed")
        if any(
            p.navigation_splitter.widget(0).isHidden()
            for p in window.real_workspace.panes
        ):
            raise RuntimeError("Dual-pane trees disappeared")
        window.real_workspace.right.explorer_layout.set_mode("Tree Focus")
        window.real_workspace.set_dual(False)
        page.set_root(None)
        if errors:
            raise RuntimeError("; ".join(errors))
    finally:
        for dialog in dialogs:
            dialog.reject()
            session = getattr(dialog, "session", None)
            wait(
                lambda d=dialog, s=session: (
                    vars(d).get("thread") is None
                    and vars(d).get("preview_thread") is None
                    and (s is None or s.thread is None)
                )
            )
            dialog.deleteLater()
        app.processEvents()
        sys.excepthook = original_hook
        temporary.cleanup()


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
            verify_exploration(app, window)
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
