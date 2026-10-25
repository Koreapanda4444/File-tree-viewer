from __future__ import annotations

import sqlite3
from collections import OrderedDict

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTableView,
    QVBoxLayout,
)

from operation_history import record_events, record_result
from snapshot import SnapshotCancelled
from sync_plan import plan_bidirectional_sync, plan_one_way_sync
from ui.preferences import application_settings
from ui.sync_preview import SyncPreviewDialog
from ui.tasks import CancellableWorker, prepare_worker_thread
from ui.widgets import PlainLabel

INVALID_INDEX = QModelIndex()


class SyncPlanningWorker(CancellableWorker):
    progress = Signal(str, int)
    finished = Signal(object, bool, str)

    def __init__(
        self,
        comparison,
        source_side,
        delete_extra,
        *,
        conflict_policy="block",
        initialize=False,
    ):
        super().__init__()
        self.comparison = comparison
        self.source_side = source_side
        self.delete_extra = delete_extra
        self.conflict_policy, self.initialize = conflict_policy, initialize

    def run(self):
        try:
            if self.source_side is None:
                result = plan_bidirectional_sync(
                    self.comparison,
                    conflict_policy=self.conflict_policy,
                    initialize=self.initialize,
                    cancelled=self._cancelled,
                    progress=self.progress.emit,
                )
            else:
                result = plan_one_way_sync(
                    self.comparison,
                    source_side=self.source_side,
                    delete_extra=self.delete_extra,
                    cancelled=self._cancelled,
                    progress=self.progress.emit,
                )
        except SnapshotCancelled:
            self.finished.emit(None, True, "")
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
            self.finished.emit(None, False, str(error))
        else:
            self.finished.emit(result, False, "")


class SyncTableModel(QAbstractTableModel):
    headers = ("Action", "Source", "Destination", "Review")

    def __init__(self, plan, parent=None):
        super().__init__(parent)
        self.plan = plan
        self.cache = OrderedDict()

    def rowCount(self, parent=INVALID_INDEX):
        return 0 if parent.isValid() else self.plan.count

    def columnCount(self, parent=INVALID_INDEX):
        return len(self.headers)

    def operation(self, row):
        page = row // 256
        if page not in self.cache:
            self.cache[page] = tuple(self.plan.operations(offset=page * 256, limit=256))
            while len(self.cache) > 8:
                self.cache.popitem(last=False)
        return self.cache[page][row % 256]

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        operation = self.operation(index.row())
        source = (
            str(self.plan.roots[operation.source_side] / operation.path)
            if operation.source_side is not None
            else "—"
        )
        return (
            operation.action.value,
            source,
            str(self.plan.roots[operation.destination_side] / operation.path),
            "Overwrite with backup"
            if operation.target and operation.action.value == "copy"
            else "Backup deletion"
            if operation.action.value == "delete"
            else "Create",
        )[index.column()]

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
            and 0 <= section < len(self.headers)
        ):
            return self.headers[section]
        return None


class SyncReviewDialog(QDialog):
    filesystem_changed = Signal(object)
    ready_to_close = Signal()
    status_changed = Signal(str)

    def __init__(self, plan, parent=None, *, application_result=None):
        super().__init__(parent)
        self.plan = plan
        self.thread = self.worker = self.outcome = None
        self.application_result = application_result
        self.close_requested = False
        self.action = ""
        self.preview_dialog = None
        self.setWindowTitle("Staged Multi-root Synchronization Plan")
        self.resize(1100, 720)
        layout = QVBoxLayout(self)
        self.summary = PlainLabel(
            f"{plan.direction}\n{plan.roots[0]}\n{plan.roots[1]}\n{plan.count:,} planned changes; {plan.issue_count:,} blocking issues"
        )
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.table = QTableView()
        self.table.setModel(SyncTableModel(plan, self))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setColumnWidth(1, 370)
        self.table.setColumnWidth(2, 370)
        layout.addWidget(self.table, 1)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setPlainText(
            "\n".join(plan.issues)
            or "Review the separate source and destination roots. This staged plan has not changed any files."
        )
        self.details.setMaximumHeight(160)
        layout.addWidget(self.details)
        self.reviewed = QCheckBox(
            "I reviewed creates, overwrites and deletions on BOTH roots"
        )
        layout.addWidget(self.reviewed)
        controls = QHBoxLayout()
        self.preview_button = QPushButton("Preview Both Folder Trees…")
        self.preview_button.setEnabled(plan.can_apply)
        self.preview_button.clicked.connect(self.show_preview)
        controls.addWidget(self.preview_button)
        self.apply_button = QPushButton(
            "Apply Synchronization…" if plan.count else "Commit Verified Baseline…"
        )
        self.apply_button.setEnabled(False)
        self.reviewed.toggled.connect(
            lambda checked: self.apply_button.setEnabled(
                checked
                and plan.can_apply
                and self.application_result is None
                and self.thread is None
            )
        )
        self.apply_button.clicked.connect(lambda: self.start_execution("apply"))
        self.undo_button = QPushButton("Undo Both Roots…")
        self.recover_button = QPushButton("Recover Both Roots…")
        self.cancel_button = QPushButton("Cancel after current step")
        for button in (self.undo_button, self.recover_button, self.cancel_button):
            button.setEnabled(False)
        if application_result is not None:
            self.reviewed.setEnabled(False)
            self.undo_button.setEnabled(application_result.status == "completed")
            self.recover_button.setEnabled(bool(application_result.recovery_errors))
            self.summary.setText(
                f"Synchronization {application_result.status}: {application_result.completed:,} processed item(s)"
            )
        self.undo_button.clicked.connect(lambda: self.start_execution("undo"))
        self.recover_button.clicked.connect(lambda: self.start_execution("recover"))
        self.cancel_button.clicked.connect(self.cancel)
        for button in (
            self.apply_button,
            self.undo_button,
            self.recover_button,
            self.cancel_button,
        ):
            controls.addWidget(button)
        layout.addLayout(controls)
        self.bar = QProgressBar()
        self.bar.hide()
        layout.addWidget(self.bar)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def show_preview(self):
        if (
            self.thread is not None
            or self.preview_dialog is not None
            or not self.plan.can_apply
        ):
            return
        dialog = self.preview_dialog = SyncPreviewDialog(self.plan, self)
        try:
            dialog.exec()
        finally:
            self.preview_dialog = None
            dialog.deleteLater()
            if self.close_requested:
                self.done(QDialog.DialogCode.Rejected)

    def start_execution(self, action):
        if self.thread is not None or (
            action == "apply"
            and (
                not self.reviewed.isChecked()
                or not self.plan.can_apply
                or self.application_result is not None
            )
        ):
            return
        if action != "apply" and (
            self.application_result is None or not self.application_result.journals
        ):
            return
        answer = QMessageBox.warning(
            self,
            "Confirm synchronization"
            if action == "apply"
            else "Confirm coordinated restoration",
            f"{action.title()} on BOTH roots?\n{self.plan.roots[0]}\n{self.plan.roots[1]}\n\nCopies replace reviewed targets with backups. Deleted items remain in transaction backups. All participants must stay available for recovery.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        settings = application_settings()
        settings.setValue("recovery/last_root", str(self.plan.roots[0]))
        settings.sync()
        if settings.status() != settings.Status.NoError:
            self.summary.setText(
                "Cannot save the recovery location. Check application settings permissions before synchronization."
            )
            return
        marker = (
            self.application_result.journals[0].parent / "sync.json"
            if self.application_result
            else None
        )
        self.worker = SyncExecutionWorker(self.plan, action, marker)
        self.worker.progress.connect(self.progress)
        self.worker.finished.connect(self.receive)
        self.thread = prepare_worker_thread(
            self, self.worker, on_finished=self.execution_thread_finished
        )
        self.action = action
        self.outcome = None
        for button in (
            self.apply_button,
            self.undo_button,
            self.recover_button,
            self.reviewed,
            self.preview_button,
        ):
            button.setEnabled(False)
        self.cancel_button.setEnabled(action == "apply")
        self.bar.show()
        self.bar.setRange(0, 0)
        self.thread.start()

    def progress(self, phase, completed, total, name):
        if total:
            self.bar.setRange(0, total)
            self.bar.setValue(completed)
        else:
            self.bar.setRange(0, 0)
        self.summary.setText(f"{phase}: {completed:,}/{total:,}\n{name}")

    def receive(self, result, error):
        self.outcome = result, error

    def execution_thread_finished(self):
        self.thread = self.worker = None
        result, error = self.outcome or (
            None,
            "Synchronization worker stopped unexpectedly",
        )
        self.cancel_button.setEnabled(False)
        self.preview_button.setEnabled(self.plan.can_apply)
        self.bar.hide()
        if result is not None:
            self.application_result = result
            import json
            from dataclasses import asdict

            self.details.setPlainText(json.dumps(asdict(result), default=str, indent=2))
            self.summary.setText(
                f"Synchronization {result.status}: {result.completed:,} processed item(s)"
                + (f"\n{result.error}" if result.error else "")
            )
            for root in result.roots:
                self.filesystem_changed.emit(root)
            self.undo_button.setEnabled(result.status == "completed")
            self.recover_button.setEnabled(bool(result.recovery_errors))
        else:
            self.summary.setText(error)
            self.reviewed.setEnabled(self.application_result is None)
            self.apply_button.setEnabled(
                self.application_result is None
                and self.reviewed.isChecked()
                and self.plan.can_apply
            )
        if self.close_requested:
            self.done(QDialog.DialogCode.Rejected)

    def cancel(self):
        if self.worker is not None and self.action == "apply":
            self.worker.cancel()
            self.summary.setText(
                "Cancelling after the current step, then restoring all participant roots…"
            )

    def done(self, result):
        if self.preview_dialog is not None:
            self.close_requested = True
            self.preview_dialog.reject()
            return
        if self.thread is not None:
            self.close_requested = True
            self.cancel()
            return
        self.table.setModel(None)
        super().done(result)
        self.ready_to_close.emit()

    def closeEvent(self, event):
        if self.thread is not None or self.preview_dialog is not None:
            self.done(QDialog.DialogCode.Rejected)
            event.ignore()
        else:
            self.done(QDialog.DialogCode.Rejected)
            event.accept()


class SyncExecutionWorker(CancellableWorker):
    progress = Signal(str, int, int, str)
    finished = Signal(object, str)

    def __init__(self, plan, action="apply", marker=None):
        super().__init__()
        self.plan, self.action, self.marker = plan, action, marker

    def run(self):
        from sync_apply import apply_sync_plan, recover_sync_group

        try:
            result = (
                apply_sync_plan(
                    self.plan, cancelled=self._cancelled, progress=self.progress.emit
                )
                if self.action == "apply"
                else recover_sync_group(
                    self.marker, undo=self.action == "undo", progress=self.progress.emit
                )
            )
        except SnapshotCancelled:
            record_events(
                [{"category": "sync", "action": self.action, "status": "cancelled"}]
            )
            self.finished.emit(None, "Synchronization cancelled before changes")
        except (
            OSError,
            ValueError,
            RuntimeError,
            TypeError,
            KeyError,
            sqlite3.Error,
        ) as error:
            record_events(
                [
                    {
                        "category": "sync",
                        "action": self.action,
                        "status": "failed",
                        "details": {"error": str(error)},
                    }
                ]
            )
            self.finished.emit(None, str(error))
        else:
            record_result(self.action, result)
            self.finished.emit(result, "")
