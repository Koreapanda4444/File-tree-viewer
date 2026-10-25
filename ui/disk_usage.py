from __future__ import annotations

import sqlite3

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from disk_usage import scan_disk_usage
from snapshot import SnapshotCancelled
from ui.analysis import format_size
from ui.tasks import CancellableWorker, prepare_worker_thread
from ui.widgets import PlainLabel


class DiskUsageWorker(CancellableWorker):
    progress = Signal(str, int)
    finished = Signal(object, str)

    def __init__(self, root):
        super().__init__()
        self.root = root

    @Slot()
    def run(self):
        try:
            result = scan_disk_usage(
                self.root, cancelled=self._cancelled, progress=self.progress.emit
            )
        except SnapshotCancelled:
            self.finished.emit(None, "Analysis cancelled")
        except (OSError, ValueError, sqlite3.Error) as error:
            self.finished.emit(None, str(error))
        else:
            self.finished.emit(result, "")


class DiskUsageDialog(QDialog):
    status_changed = Signal(str)

    def __init__(self, root, parent=None):
        super().__init__(parent)
        self.usage = self.outcome = None
        self.close_requested = False
        self.setWindowTitle("Disk Usage Analysis")
        self.resize(1000, 720)
        self.layout = QVBoxLayout(self)
        self.summary = PlainLabel(f"Scanning {root}…")
        self.summary.setWordWrap(True)
        self.layout.addWidget(self.summary)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ("Path", "Logical size", "Unique bytes", "Status")
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setColumnWidth(0, 430)
        self.layout.addWidget(self.table, 1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.buttons.rejected.connect(self.reject)
        self.layout.addWidget(self.buttons)
        self.worker = DiskUsageWorker(root)
        self.worker.progress.connect(self.progress)
        self.worker.finished.connect(self.receive)
        self.status_changed.connect(self.summary.setText)
        self.thread = prepare_worker_thread(
            self, self.worker, on_finished=self.thread_finished
        )
        self.thread.start()

    @Slot(str, int)
    def progress(self, phase, count):
        self.summary.setText(f"{phase}: {count:,} entries")

    def receive(self, result, error):
        self.outcome = result, error

    def thread_finished(self):
        self.thread.wait()
        self.thread = self.worker = None
        self.usage, error = self.outcome or (
            None,
            "Analysis worker stopped unexpectedly",
        )
        self.outcome = None
        if self.close_requested:
            self.done(QDialog.DialogCode.Rejected)
        elif self.usage is None:
            self.summary.setText(error)
        else:
            root = self.usage.entry()
            self.summary.setText(
                f"{self.usage.root}\nLogical: {format_size(root.logical)} · Unique: {format_size(root.unique)} · Allocated estimate: {format_size(root.allocated)} · Skipped/errors: {self.usage.error_count:,}"
            )
            self.show_entries(self.usage.children())

    def show_entries(self, entries):
        self.entries = entries
        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            for column, text in enumerate(
                (
                    entry.path,
                    format_size(entry.logical),
                    format_size(entry.unique),
                    entry.error,
                )
            ):
                self.table.setItem(row, column, QTableWidgetItem(text))

    def done(self, result):
        if self.thread is not None:
            self.close_requested = True
            self.worker.cancel()
            return
        if self.usage is not None:
            self.usage.close()
            self.usage = None
        super().done(result)

    def closeEvent(self, event):
        self.done(QDialog.DialogCode.Rejected)
        event.ignore() if self.thread is not None else event.accept()
