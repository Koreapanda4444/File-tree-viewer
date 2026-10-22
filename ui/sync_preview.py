from __future__ import annotations

import sqlite3

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QSplitter,
    QTabWidget,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from snapshot import SnapshotCancelled
from sync_preview import build_sync_preview
from ui.tasks import CancellableWorker, prepare_worker_thread
from ui.widgets import PlainLabel


class SyncPreviewWorker(CancellableWorker):
    progress = Signal(str, int)
    finished = Signal(object, str)

    def __init__(self, plan):
        super().__init__()
        self.plan = plan

    def run(self):
        try:
            result = build_sync_preview(
                self.plan, cancelled=self._cancelled, progress=self.progress.emit
            )
        except SnapshotCancelled:
            self.finished.emit(None, "Preview cancelled")
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
            self.finished.emit(None, str(error))
        else:
            self.finished.emit(result, "")


class SyncPreviewDialog(QDialog):
    status_changed = Signal(str)

    def __init__(self, plan, parent=None):
        super().__init__(parent)
        self.preview = self.outcome = None
        self.close_requested = False
        self.trees = []
        self.setWindowTitle("Synchronization Before / After Structure")
        self.resize(1150, 740)
        layout = QVBoxLayout(self)
        self.summary = PlainLabel(
            "Building preview from the staged comparison. Original files are unchanged. This shows planned structure, not a fresh scan."
        )
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.worker = SyncPreviewWorker(plan)
        self.worker.progress.connect(self.progress)
        self.worker.finished.connect(self.receive)
        self.status_changed.connect(self.summary.setText)
        self.thread = prepare_worker_thread(
            self, self.worker, on_finished=self.thread_finished
        )
        self.thread.start()

    @Slot(str, int)
    def progress(self, phase, count):
        self.summary.setText(f"{phase}: {count:,}")

    def receive(self, result, error):
        self.outcome = result, error

    def thread_finished(self):
        self.thread.wait()
        self.thread = self.worker = None
        self.preview, error = self.outcome or (
            None,
            "Preview worker stopped unexpectedly",
        )
        self.outcome = None
        if self.close_requested:
            self.done(QDialog.DialogCode.Rejected)
            return
        if self.preview is None:
            self.summary.setText(error)
            return
        from ui.plan import SnapshotTreeModel

        for side in (0, 1):
            split = QSplitter(Qt.Orientation.Horizontal)
            for title, snapshot in (
                ("Before", self.preview.before[side]),
                ("After Synchronization", self.preview.after[side]),
            ):
                panel = QWidget()
                column = QVBoxLayout(panel)
                column.addWidget(PlainLabel(f"{title}: {snapshot.entry_count:,} items"))
                tree = QTreeView()
                tree.setUniformRowHeights(True)
                model = SnapshotTreeModel(snapshot, tree)
                tree.setModel(model)
                tree.setDragEnabled(False)
                tree.setAcceptDrops(False)
                tree.setColumnWidth(0, 300)
                index = model.index(0, 0)
                model.fetchMore(index)
                tree.expand(index)
                self.trees.append(tree)
                column.addWidget(tree)
                split.addWidget(panel)
            self.tabs.addTab(split, "Left root" if side == 0 else "Right root")
            self.tabs.setTabToolTip(side, str(self.preview.before[side].root))
        self.summary.setText(
            "Before / after structure from the staged comparison. Files are unchanged. Apply revalidates both roots before making changes."
        )

    def done(self, result):
        if self.thread is not None:
            self.close_requested = True
            self.worker.cancel()
            return
        for tree in self.trees:
            model = tree.model()
            tree.setModel(None)
            if model is not None:
                model.deleteLater()
        self.trees.clear()
        if self.preview is not None:
            self.preview.close()
            self.preview = None
        super().done(result)

    def closeEvent(self, event):
        self.done(QDialog.DialogCode.Rejected)
        if self.thread is not None:
            event.ignore()
        else:
            event.accept()
