from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from operation_history import HistoryStore
from reports import export_operation_report
from snapshot import SnapshotCancelled
from ui.tasks import CancellableWorker, prepare_worker_thread
from ui.widgets import PlainLabel


class HistoryQueryWorker(CancellableWorker):
    finished = Signal(object, str)

    def __init__(self, store, filters, export_path=None):
        super().__init__()
        self.store, self.filters = store, filters
        self.export_path = export_path

    @Slot()
    def run(self):
        try:
            if self.export_path is not None:
                rows = str(
                    export_operation_report(
                        self.store,
                        self.export_path,
                        format=self.export_path.suffix.lower().lstrip("."),
                        filters=self.filters,
                        cancelled=self._cancelled,
                    )
                )
            else:
                rows = self.store.rows(**self.filters, cancelled=self._cancelled)
        except SnapshotCancelled:
            self.finished.emit([], "History task cancelled")
        except (OSError, ValueError, sqlite3.Error) as error:
            self.finished.emit([], str(error))
        else:
            self.finished.emit(rows, "")


class HistoryDialog(QDialog):
    reveal_requested = Signal(object)
    status_changed = Signal(str)

    def __init__(self, parent=None, *, store=None):
        super().__init__(parent)
        self.store = store or HistoryStore()
        self.thread = self.worker = self.outcome = None
        self.close_requested = False
        self.rows = []
        self.offset = 0
        self.exporting = False
        self.setWindowTitle("Operation History")
        self.resize(1100, 750)
        self.layout = QVBoxLayout(self)
        filters = QHBoxLayout()
        self.category = QComboBox()
        self.category.addItems(("All types", "real", "plan", "sync"))
        self.status = QComboBox()
        self.status.addItems(
            (
                "All statuses",
                "success",
                "completed",
                "failed",
                "skipped",
                "cancelled",
                "rolled_back",
                "rollback_failed",
                "undone",
                "undo_failed",
                "recovered",
                "recovery_failed",
            )
        )
        self.since = QLineEdit()
        self.since.setPlaceholderText("From UTC YYYY-MM-DD")
        self.until = QLineEdit()
        self.until.setPlaceholderText("Through UTC YYYY-MM-DD")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Path or action")
        self.refresh_button = QPushButton("Search / Refresh")
        self.refresh_button.clicked.connect(lambda: self.refresh(reset=True))
        self.export_button = QPushButton("Export Filtered Report…")
        self.export_button.clicked.connect(self.export_report)
        self.controls = [
            self.category,
            self.status,
            self.since,
            self.until,
            self.search,
            self.refresh_button,
            self.export_button,
        ]
        for control in self.controls:
            filters.addWidget(control)
        self.layout.addLayout(filters)
        self.summary = PlainLabel("Loading operation history…")
        self.summary.setWordWrap(True)
        self.layout.addWidget(self.summary)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            (
                "Time (UTC)",
                "Type",
                "Action",
                "Status",
                "Source",
                "Target",
                "Transaction",
            )
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.itemSelectionChanged.connect(self.select_row)
        self.table.setColumnWidth(0, 200)
        self.table.setColumnWidth(4, 240)
        self.table.setColumnWidth(5, 240)
        self.layout.addWidget(self.table, 1)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(170)
        self.layout.addWidget(self.details)
        actions = QHBoxLayout()
        self.previous_button = QPushButton("Previous 250")
        self.next_button = QPushButton("Next 250")
        self.reveal_button = QPushButton("Show File Location")
        self.previous_button.clicked.connect(lambda: self.change_page(-250))
        self.next_button.clicked.connect(lambda: self.change_page(250))
        self.reveal_button.clicked.connect(self.reveal)
        for button in (self.previous_button, self.next_button, self.reveal_button):
            actions.addWidget(button)
        self.layout.addLayout(actions)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        self.layout.addWidget(buttons)
        self.status_changed.connect(self.summary.setText)
        self.refresh()

    def filters(self):
        since = (
            date.fromisoformat(self.since.text()).isoformat()
            if self.since.text()
            else ""
        )
        until = (
            (date.fromisoformat(self.until.text()) + timedelta(days=1)).isoformat()
            if self.until.text()
            else ""
        )
        if since and until and since >= until:
            raise ValueError("The start date must not be after the end date")
        return {
            "category": self.category.currentText()
            if self.category.currentIndex()
            else "",
            "status": self.status.currentText() if self.status.currentIndex() else "",
            "since": since,
            "until": until,
            "text": self.search.text(),
            "limit": 251,
            "offset": self.offset,
        }

    def refresh(self, *, reset=False):
        if self.thread is not None:
            return
        if reset:
            self.offset = 0
        try:
            filters = self.filters()
        except (ValueError, OverflowError) as error:
            self.summary.setText(str(error))
            return
        for control in [
            *self.controls,
            self.previous_button,
            self.next_button,
            self.reveal_button,
        ]:
            control.setEnabled(False)
        self.summary.setText("Loading operation history…")
        self.worker = HistoryQueryWorker(self.store, filters)
        self.worker.finished.connect(self.receive)
        self.thread = prepare_worker_thread(
            self, self.worker, on_finished=self.thread_finished
        )
        self.thread.start()

    def export_report(self):
        if self.thread is not None:
            return
        try:
            filters = self.filters()
        except (ValueError, OverflowError) as error:
            self.summary.setText(str(error))
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Operation Report",
            "operations.json",
            "JSON (*.json);;CSV (*.csv);;HTML (*.html)",
        )
        if not path:
            return
        self.exporting = True
        for control in [
            *self.controls,
            self.previous_button,
            self.next_button,
            self.reveal_button,
        ]:
            control.setEnabled(False)
        self.summary.setText("Exporting all events matching the current filters…")
        self.worker = HistoryQueryWorker(self.store, filters, Path(path))
        self.worker.finished.connect(self.receive)
        self.thread = prepare_worker_thread(
            self, self.worker, on_finished=self.thread_finished
        )
        self.thread.start()

    def receive(self, rows, error):
        self.outcome = rows, error

    def thread_finished(self):
        self.thread.wait()
        self.thread = self.worker = None
        rows, error = self.outcome or ([], "History query stopped unexpectedly")
        self.outcome = None
        if self.close_requested:
            self.done(QDialog.DialogCode.Rejected)
            return
        if self.exporting:
            self.exporting = False
            for control in self.controls:
                control.setEnabled(True)
            self.previous_button.setEnabled(self.offset > 0)
            self.next_button.setEnabled(len(self.rows) == 250)
            self.select_row()
            self.summary.setText(error or f"Exported filtered history: {rows}")
            return
        self.next_button.setEnabled(len(rows) > 250)
        self.previous_button.setEnabled(self.offset > 0)
        self.rows = rows[:250]
        for control in self.controls:
            control.setEnabled(True)
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.rows))
        for row, event in enumerate(self.rows):
            for col, key in enumerate(
                (
                    "time",
                    "category",
                    "action",
                    "status",
                    "source",
                    "target",
                    "transaction_id",
                )
            ):
                item = QTableWidgetItem(event[key])
                item.setToolTip(event[key])
                self.table.setItem(row, col, item)
        self.table.clearSelection()
        self.table.blockSignals(False)
        self.details.clear()
        self.reveal_button.setEnabled(False)
        self.summary.setText(
            error
            or f"{len(self.rows):,} events · offset {self.offset:,} · history is an audit index; restoration uses the original Journal"
        )

    def change_page(self, delta):
        if self.thread is None:
            self.offset = max(0, self.offset + delta)
            self.refresh()

    def selected_event(self):
        row = self.table.currentRow()
        return self.rows[row] if 0 <= row < len(self.rows) else None

    def select_row(self):
        event = self.selected_event()
        self.reveal_button.setEnabled(
            event is not None
            and bool(event["target"] or event["source"] or event["root"])
        )
        self.details.setPlainText(
            json.dumps(event, ensure_ascii=False, indent=2) if event else ""
        )

    def reveal(self):
        event = self.selected_event()
        if event:
            for key in ("target", "source", "root"):
                if event[key] and Path(event[key]).exists():
                    path = Path(event[key])
                    self.accept()
                    self.reveal_requested.emit(path)
                    return
            self.summary.setText("Recorded locations are no longer available")

    def done(self, result):
        if self.thread is not None:
            self.close_requested = True
            self.worker.cancel()
            return
        super().done(result)

    def closeEvent(self, event):
        self.done(QDialog.DialogCode.Rejected)
        event.ignore() if self.thread is not None else event.accept()
