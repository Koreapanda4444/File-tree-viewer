from __future__ import annotations

import sqlite3
from collections import OrderedDict

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QPlainTextEdit,
    QTableView,
    QVBoxLayout,
)

from snapshot import SnapshotCancelled
from sync_plan import plan_bidirectional_sync, plan_one_way_sync
from ui.tasks import CancellableWorker
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
    def __init__(self, plan, parent=None):
        super().__init__(parent)
        self.plan = plan
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
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
