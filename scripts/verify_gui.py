"""Exercise asynchronous browsing with isolated preferences and temporary files."""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QCoreApplication, QEvent, QItemSelectionModel, QSettings
from PySide6.QtWidgets import QApplication

from application_paths import configure_data_directory
from real.search import SearchWorker
from real.tree import FileTreeModel
from ui.tree_export import TreeExportDialog
from ui.window import RealExplorerPage


def wait_for(app, condition, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return
        time.sleep(0.002)
    raise AssertionError("Asynchronous operation did not finish")


def main():
    app = QApplication([])
    with tempfile.TemporaryDirectory(prefix="ftv-gui-check-") as temporary:
        configure_data_directory(Path(temporary) / "data")
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(
            QSettings.Format.IniFormat, QSettings.Scope.UserScope, temporary
        )
        root = Path(temporary) / "files"
        root.mkdir()
        root = root.resolve(strict=True)
        for number in range(1024):
            (root / f"{1023 - number:05d}.txt").write_bytes(b"x" * (number % 31))
        model = FileTreeModel(page_size=19)
        model.set_root(root)
        model.fetchMore(model.index(0, 0))
        assert model.root.fetch_pending
        wait_for(app, lambda: not model.root.fetch_pending)
        assert [node.name for node in model.root.children] == [
            f"{i:05d}.txt" for i in range(19)
        ]
        model.set_sort_options(2, True, "None", True)
        model.fetchMore(model.index(0, 0))
        wait_for(app, lambda: not model.root.fetch_pending)
        assert all(node.size == 30 for node in model.root.children)
        model.refresh()
        model.fetchMore(model.index(0, 0))
        model.clear()
        wait_for(app, lambda: model.index_pool.activeThreadCount() == 0)
        model.close()
        print(
            "PASS: complete-directory sorting before paging and stale job cancellation"
        )

        page = RealExplorerPage()
        assert page.set_root(root)
        try:
            wait_for(app, lambda: bool(page.model.root.children))
            selected = root / "00007.txt"
            index = page.browse_model.mapFromSource(page.model.index_for_path(selected))
            page.tree.selectionModel().setCurrentIndex(
                index,
                QItemSelectionModel.SelectionFlag.ClearAndSelect
                | QItemSelectionModel.SelectionFlag.Rows,
            )
            wait_for(app, lambda: not page.preview_is_running)
            page.refresh_root()
            wait_for(
                app,
                lambda: (
                    page.explorer_state.pending is None
                    and selected in page.selected_paths()
                ),
            )
            (root / "-external.txt").write_text("external")
            wait_for(
                app, lambda: page.model.index_for_path(root / "-external.txt").isValid()
            )
            wait_for(app, lambda: page.explorer_state.pending is None)
            assert selected in page.selected_paths()
            (root / "-external.txt").rename(root / "-renamed.txt")
            wait_for(
                app, lambda: page.model.index_for_path(root / "-renamed.txt").isValid()
            )
            assert not page.model.index_for_path(root / "-external.txt").isValid()
            print("PASS: filesystem watcher, rename and refresh selection restoration")
        finally:
            page.prepare_close()
            wait_for(app, page.prepare_close)
            page.finalize_close()

        worker = SearchWorker(root, "00007", False)
        results = []
        worker.batch_found.connect(
            lambda batch: (results.extend(batch), worker.batch_processed())
        )
        worker.run()
        assert len(results) == 1 and results[0].name == "00007.txt"
        print("PASS: file search and result queue acknowledgement")

        dialog = TreeExportDialog(root, "real")
        dialog.start()
        wait_for(app, lambda: dialog.thread is None)
        assert dialog.document is not None and dialog.copy.isEnabled()
        dialog.copy_tree()
        assert "00007.txt" in QApplication.clipboard().text()
        dialog.reject()
        QApplication.clipboard().clear()
        worker.batch_found.disconnect()
        for obj in (worker, dialog, page, model):
            obj.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        print("PASS: tree preview worker and clipboard")
    configure_data_directory()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
