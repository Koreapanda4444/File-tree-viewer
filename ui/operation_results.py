from __future__ import annotations

import time
from collections import Counter

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
    QTimer,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QPushButton,
    QTableView,
    QVBoxLayout,
)

from ui.widgets import PlainLabel

INVALID_INDEX = QModelIndex()


class OperationResultsModel(QAbstractTableModel):
    headers = ("Status", "Source", "Target", "Details")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items = []
        self.counts = Counter()
        self.action = ""
        self.total = 0
        self.started = time.monotonic()
        self.elapsed = None

    def reset_operation(self, action, total):
        self.beginResetModel()
        self.items.clear()
        self.counts.clear()
        self.action, self.total = action, total
        self.started, self.elapsed = time.monotonic(), None
        self.endResetModel()

    def append_items(self, items):
        if not items:
            return
        start = len(self.items)
        self.beginInsertRows(QModelIndex(), start, start + len(items) - 1)
        self.items.extend(items)
        self.counts.update(item.status for item in items)
        self.endInsertRows()

    def finish(self):
        self.elapsed = time.monotonic() - self.started

    def summary(self):
        counts = ", ".join(
            f"{self.counts[status]:,} {status}"
            for status in ("success", "failed", "skipped", "cancelled")
        )
        elapsed = (
            self.elapsed
            if self.elapsed is not None
            else time.monotonic() - self.started
        )
        return f"{self.action.title()}: {len(self.items):,}/{self.total:,} items reviewed · {counts} · {elapsed:.1f}s"

    def rowCount(self, parent=INVALID_INDEX):
        return 0 if parent.isValid() else len(self.items)

    def columnCount(self, parent=INVALID_INDEX):
        return len(self.headers)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.items):
            return None
        item = self.items[index.row()]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return (
                item.status,
                str(item.source or ""),
                str(item.target or ""),
                item.message,
            )[index.column()]
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
            and 0 <= section < len(self.headers)
        ):
            return self.headers[section]
        return None


class OperationResultsDialog(QDialog):
    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.setWindowTitle("File Operation Results")
        self.resize(1100, 650)
        layout = QVBoxLayout(self)
        self.summary = PlainLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        note = PlainLabel(
            "Cancellation takes effect between selected items. Completed copies and moves can be undone. Failed partial copies are preserved; review their target paths."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        row = QHBoxLayout()
        self.filter = QComboBox()
        self.filter.addItems(("All", "success", "failed", "skipped", "cancelled"))
        row.addWidget(self.filter)
        self.cancel = QPushButton("Cancel After Current Item")
        self.cancel.clicked.connect(self.cancel_operation)
        row.addWidget(self.cancel)
        row.addStretch(1)
        layout.addLayout(row)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(page.operation_results)
        self.proxy.setFilterKeyColumn(0)
        self.filter.currentTextChanged.connect(
            lambda text: self.proxy.setFilterFixedString("" if text == "All" else text)
        )
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        for column, width in enumerate((100, 300, 300)):
            self.table.setColumnWidth(column, width)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.cancelling = False
        self.refresh()

    def refresh(self):
        self.summary.setText(self.page.operation_results.summary())
        self.cancel.setEnabled(self.page.operation_is_running and not self.cancelling)

    def cancel_operation(self):
        worker = self.page.operation_worker
        if worker is not None:
            worker.cancel()
            self.cancelling = True
            self.cancel.setText("Cancellation requested…")
            self.page.status_changed.emit(
                "Finishing the current item safely; remaining items will be cancelled"
            )
            self.refresh()
