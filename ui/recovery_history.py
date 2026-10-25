from __future__ import annotations

import sqlite3

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from recovery_history import check_history_undo, list_recovery_history
from snapshot import SnapshotCancelled
from ui.tasks import CancellableWorker, prepare_worker_thread
from ui.widgets import PlainLabel


class RecoveryHistoryWorker(CancellableWorker):
    finished = Signal(object, str)

    def __init__(self, root, journal=None):
        super().__init__()
        self.root, self.journal = root, journal

    @Slot()
    def run(self):
        try:
            result = (
                check_history_undo(self.root, self.journal)
                if self.journal
                else list_recovery_history(self.root, cancelled=self._cancelled)
            )
        except SnapshotCancelled:
            self.finished.emit(None, "Cancelled")
        except (
            OSError,
            ValueError,
            RuntimeError,
            TypeError,
            KeyError,
            sqlite3.Error,
        ) as error:
            self.finished.emit(None, str(error))
        else:
            self.finished.emit(result, "")


class RecoveryHistoryDialog(QDialog):
    undo_requested = Signal(object)
    recover_requested = Signal(object)
    status_changed = Signal(str)

    def __init__(self, root, parent=None):
        super().__init__(parent)
        self.root = root
        self.rows = []
        self.checked_record = None
        self.thread = self.worker = self.outcome = None
        self.checking = False
        self.close_requested = False
        self.setWindowTitle("Transaction Recovery History")
        self.resize(1000, 700)
        layout = QVBoxLayout(self)
        self.summary = PlainLabel(
            f"{root}\nRecovery files are retained. Undo is revalidated before execution; no backup cleanup is performed here."
        )
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ("Started (UTC)", "Transaction", "Status", "Recovery / Errors")
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.itemSelectionChanged.connect(self.select_row)
        self.table.setColumnWidth(0, 200)
        self.table.setColumnWidth(1, 270)
        layout.addWidget(self.table, 1)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        layout.addWidget(self.details)
        actions = QHBoxLayout()
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(lambda: self.start())
        self.check_button = QPushButton("Check Selected Undo Safety")
        self.check_button.clicked.connect(self.check_selected)
        self.undo_button = QPushButton("Undo Verified Transaction…")
        self.undo_button.clicked.connect(self.undo_selected)
        self.recover_button = QPushButton("Review Interrupted Recovery in Plan…")
        self.recover_button.clicked.connect(self.recover)
        for button in (
            self.refresh_button,
            self.check_button,
            self.undo_button,
            self.recover_button,
        ):
            actions.addWidget(button)
        layout.addLayout(actions)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.status_changed.connect(self.summary.setText)
        self.start()

    def start(self, journal=None):
        if self.thread is not None:
            return
        self.checking = journal is not None
        self.checked_record = None
        for widget in (
            self.table,
            self.refresh_button,
            self.check_button,
            self.undo_button,
            self.recover_button,
        ):
            widget.setEnabled(False)
        self.worker = RecoveryHistoryWorker(self.root, journal)
        self.worker.finished.connect(self.receive)
        self.thread = prepare_worker_thread(
            self, self.worker, on_finished=self.thread_finished
        )
        self.thread.start()

    def receive(self, result, error):
        self.outcome = result, error

    def thread_finished(self):
        self.thread.wait()
        self.thread = self.worker = None
        result, error = self.outcome or (None, "Recovery query stopped unexpectedly")
        self.outcome = None
        if self.close_requested:
            self.done(QDialog.DialogCode.Rejected)
            return
        self.table.setEnabled(True)
        self.refresh_button.setEnabled(True)
        self.recover_button.setEnabled(True)
        if self.checking:
            self.checked_record = result
            self.details.setPlainText(
                error
                or "Undo checks passed for recorded paths and all synchronization participants. The execution checks them again."
            )
            self.undo_button.setEnabled(result is not None)
            self.check_button.setEnabled(self.table.currentRow() >= 0)
            return
        self.rows = result or []
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.rows))
        for index, row in enumerate(self.rows):
            for col, key in enumerate(("time", "id", "status", "details")):
                self.table.setItem(index, col, QTableWidgetItem(row[key]))
        self.table.clearSelection()
        self.table.blockSignals(False)
        self.summary.setText(
            error
            or f"{self.root}\n{len(self.rows):,} transactions shown (maximum 1,000). Recovery data is preserved; selecting a row does not modify files."
        )

    def select_row(self):
        self.checked_record = None
        self.undo_button.setEnabled(False)
        index = self.table.currentRow()
        self.check_button.setEnabled(
            self.thread is None
            and 0 <= index < len(self.rows)
            and self.rows[index]["status"] == "completed"
        )
        self.details.setPlainText(
            str(self.rows[index]) if 0 <= index < len(self.rows) else ""
        )

    def check_selected(self):
        index = self.table.currentRow()
        if 0 <= index < len(self.rows):
            self.start(self.rows[index]["journal"])

    def undo_selected(self):
        record = self.checked_record
        if record is None:
            return
        answer = QMessageBox.warning(
            self,
            "Undo Transaction",
            "Undo this transaction? For synchronization, BOTH roots are restored together. External changes block restoration.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.accept()
            self.undo_requested.emit(record)

    def recover(self):
        self.accept()
        self.recover_requested.emit(self.root)

    def done(self, result):
        if self.thread is not None:
            self.close_requested = True
            self.worker.cancel()
            return
        super().done(result)

    def closeEvent(self, event):
        self.done(QDialog.DialogCode.Rejected)
        event.ignore() if self.thread is not None else event.accept()
