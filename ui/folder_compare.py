from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import QModelIndex, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableView,
    QVBoxLayout,
)

from file_hashing import FileHashCache
from file_metadata import format_timestamp
from folder_compare import FolderStatus, compare_folders
from snapshot import SnapshotCancelled
from ui.file_compare import FileComparisonDialog
from ui.snapshot_compare import ComparisonTableModel
from ui.tasks import CancellableWorker, prepare_worker_thread
from ui.widgets import PlainLabel

INVALID_INDEX = QModelIndex()


class FolderComparisonWorker(CancellableWorker):
    progress = Signal(str, int)
    finished = Signal(object, bool, str)

    def __init__(self, left, right, verify, cache):
        super().__init__()
        self.left, self.right, self.verify, self.cache = left, right, verify, cache

    def run(self):
        try:
            result = compare_folders(
                self.left,
                self.right,
                verify_content=self.verify,
                hash_cache=self.cache,
                cancelled=self._cancelled,
                progress=self.progress.emit,
            )
        except SnapshotCancelled:
            self.finished.emit(None, True, "")
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
            self.finished.emit(None, False, str(error))
        else:
            self.finished.emit(result, False, "")


class FolderTableModel(ComparisonTableModel):
    headers = (
        "Status",
        "Left path",
        "Left bytes",
        "Left modified",
        "Right path",
        "Right bytes",
        "Right modified",
        "Details",
    )

    def rowCount(self, parent=INVALID_INDEX):
        return 0 if parent.isValid() else self.comparison.count(self.kind)

    def change(self, row):
        if not 0 <= row < self.rowCount():
            return None
        page = row // 256
        if page not in self._cache:
            self._cache[page] = self.comparison.page(
                status=self.kind, offset=page * 256
            )
            while len(self._cache) > 8:
                self._cache.popitem(last=False)
        self._cache.move_to_end(page)
        return self._cache[page][row % 256]

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        entry = self.change(index.row())
        if entry is None:
            return None
        left, right = entry.left, entry.right
        if role == Qt.ItemDataRole.DisplayRole:
            return (
                entry.status.value.replace("_", " ").title(),
                entry.path if left else "—",
                left["size"] if left and not left["is_directory"] else "—",
                format_timestamp(left["modified_ns"]) if left else "—",
                entry.path if right else "—",
                right["size"] if right and not right["is_directory"] else "—",
                format_timestamp(right["modified_ns"]) if right else "—",
                entry.details,
            )[index.column()]
        if role == Qt.ItemDataRole.UserRole:
            return entry
        if role == Qt.ItemDataRole.ForegroundRole and index.column() == 0:
            return QColor(
                {
                    FolderStatus.ERROR: "#be123c",
                    FolderStatus.TYPE_CONFLICT: "#be123c",
                    FolderStatus.MODIFIED: "#9a6700",
                    FolderStatus.LEFT_ONLY: "#2563a0",
                    FolderStatus.RIGHT_ONLY: "#2563a0",
                }.get(entry.status, "#23703a")
            )
        if role == Qt.ItemDataRole.ToolTipRole:
            return entry.details
        return None


class FolderComparisonDialog(QDialog):
    ready_to_close = Signal()
    status_changed = Signal(str)

    def __init__(self, initial=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Side-by-side Folder Comparison")
        self.resize(1250, 800)
        self.thread = self.worker = self.outcome = self.comparison = None
        self.close_requested = False
        self.hash_cache = FileHashCache()
        layout = QVBoxLayout(self)
        self.path_inputs = []
        roots = QHBoxLayout()
        for side in ("Left", "Right"):
            column = QVBoxLayout()
            column.addWidget(PlainLabel(side + " folder"))
            row = QHBoxLayout()
            edit = QLineEdit(str(initial or "") if side == "Left" else "")
            edit.setPlaceholderText("Choose a folder, including another drive")
            self.path_inputs.append(edit)
            browse = QPushButton("Browse…")
            browse.clicked.connect(lambda checked=False, field=edit: self.browse(field))
            row.addWidget(edit, 1)
            row.addWidget(browse)
            column.addLayout(row)
            roots.addLayout(column, 1)
        layout.addLayout(roots)
        controls = QHBoxLayout()
        self.verify = QCheckBox("Verify all file contents with SHA-256")
        self.start_button = QPushButton("Compare")
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.start_button.clicked.connect(self.start_compare)
        self.cancel_button.clicked.connect(self.cancel)
        self.filter = QComboBox()
        self.filter.addItem("All items", None)
        for status in FolderStatus:
            self.filter.addItem(status.value.replace("_", " ").title(), status)
        self.filter.currentIndexChanged.connect(self.filter_changed)
        for widget in (self.verify, self.start_button, self.cancel_button, self.filter):
            controls.addWidget(widget)
        layout.addLayout(controls)
        self.summary = PlainLabel(
            "Compare metadata, or enable SHA-256 to verify file contents. Links are shown for review."
        )
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.status_changed.connect(self.summary.setText)
        self.bar = QProgressBar()
        self.bar.hide()
        layout.addWidget(self.bar)
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableView()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        splitter.addWidget(self.table)
        splitter.addWidget(self.details)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)
        row = QHBoxLayout()
        self.compare_files_button = QPushButton("Compare Selected Files…")
        self.compare_files_button.setEnabled(False)
        self.compare_files_button.clicked.connect(self.compare_files)
        row.addWidget(self.compare_files_button)
        self.extra_controls = row
        row.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        row.addWidget(buttons)
        layout.addLayout(row)

    def browse(self, field):
        if self.thread is not None:
            return
        selected = QFileDialog.getExistingDirectory(
            self, "Compare Folder", field.text()
        )
        if selected:
            field.setText(selected)

    def set_running(self, running):
        for widget in (*self.path_inputs, self.start_button, self.verify, self.filter):
            widget.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        self.table.setEnabled(not running)
        self.bar.setVisible(running)
        self.bar.setRange(0, 0)
        self.compare_files_button.setEnabled(False)

    def start_compare(self):
        if self.thread is not None:
            return
        if not all(edit.text().strip() for edit in self.path_inputs):
            self.summary.setText("Choose both folder roots")
            return
        self.worker = FolderComparisonWorker(
            *(Path(edit.text()).expanduser().absolute() for edit in self.path_inputs),
            self.verify.isChecked(),
            self.hash_cache,
        )
        self.worker.progress.connect(
            lambda message, count: self.summary.setText(f"{message}: {count:,}")
        )
        self.worker.finished.connect(self.receive)
        self.thread = prepare_worker_thread(
            self, self.worker, on_finished=self.comparison_thread_finished
        )
        self.outcome = None
        self.set_running(True)
        self.summary.setText("Scanning both folders…")
        self.thread.start()

    def receive(self, result, cancelled, error):
        self.outcome = (result, cancelled, error)

    def comparison_thread_finished(self):
        self.thread = self.worker = None
        self.set_running(False)
        result, cancelled, error = self.outcome or (
            None,
            False,
            "Comparison stopped unexpectedly",
        )
        if self.close_requested:
            if result is not None:
                result.close()
            self.done(QDialog.DialogCode.Rejected)
            return
        if result is None:
            self.summary.setText(
                error or ("Comparison cancelled" if cancelled else "Comparison failed")
            )
            return
        previous = self.comparison
        self.comparison = result
        old_model = self.table.model()
        self.table.setModel(FolderTableModel(result, self))
        self.table.selectionModel().currentChanged.connect(self.selected)
        self.table.setColumnWidth(1, 220)
        self.table.setColumnWidth(4, 220)
        if old_model is not None:
            old_model.deleteLater()
        if previous is not None:
            previous.close()
        self.filter_changed()

    def filter_changed(self):
        if self.comparison is None:
            return
        self.table.model().set_kind(self.filter.currentData())
        self.details.clear()
        self.compare_files_button.setEnabled(False)
        summary = ", ".join(
            f"{status.value.replace('_', ' ')}: {count:,}"
            for status, count in self.comparison.counts.items()
        )
        self.summary.setText(
            f"{'SHA-256' if self.comparison.hash_verified else 'Metadata'} comparison — {summary}; scan errors: {self.comparison.error_count:,}"
        )

    def selected(self, current, previous=INVALID_INDEX):
        entry = self.table.model().change(current.row()) if current.isValid() else None
        self.details.setPlainText(
            json.dumps(asdict(entry), ensure_ascii=False, default=str, indent=2)
            if entry
            else ""
        )
        valid = (
            entry
            and entry.left
            and entry.right
            and not entry.left["is_directory"]
            and not entry.right["is_directory"]
            and entry.status is not FolderStatus.ERROR
        )
        self.compare_files_button.setEnabled(bool(valid) and self.thread is None)

    def compare_files(self):
        current = self.table.currentIndex()
        entry = self.table.model().change(current.row()) if current.isValid() else None
        if entry and self.compare_files_button.isEnabled():
            dialog = FileComparisonDialog(
                self.comparison.left_root / entry.path,
                self.comparison.right_root / entry.path,
                self,
            )
            dialog.exec()
            dialog.deleteLater()

    def cancel(self):
        if self.worker is not None:
            self.worker.cancel()
            self.summary.setText("Cancelling comparison…")

    def done(self, result):
        if self.thread is not None:
            self.close_requested = True
            self.cancel()
            return
        if self.comparison is not None:
            old_model = self.table.model()
            self.table.setModel(None)
            if old_model is not None:
                old_model.deleteLater()
            self.comparison.close()
            self.comparison = None
        super().done(result)
        self.ready_to_close.emit()

    def closeEvent(self, event):
        if self.thread is not None:
            self.close_requested = True
            self.cancel()
            event.ignore()
        else:
            self.done(QDialog.DialogCode.Rejected)
            event.accept()
