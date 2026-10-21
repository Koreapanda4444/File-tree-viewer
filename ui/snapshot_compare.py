from __future__ import annotations

import json
from collections import OrderedDict

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QSplitter,
    QTableView,
    QVBoxLayout,
)

from snapshot_compare import SnapshotChangeKind, SnapshotComparison

INVALID_INDEX = QModelIndex()


class ComparisonTableModel(QAbstractTableModel):
    headers = ("Change", "Before path", "After path", "Details")

    def __init__(self, comparison: SnapshotComparison, parent=None) -> None:
        super().__init__(parent)
        self.comparison = comparison
        self.kind = None
        self._cache = OrderedDict()

    def set_kind(self, kind) -> None:
        self.beginResetModel()
        self.kind = kind
        self._cache.clear()
        self.endResetModel()

    def rowCount(self, parent=INVALID_INDEX) -> int:
        return (
            0
            if parent.isValid()
            else self.comparison.count(None if self.kind is None else [self.kind])
        )

    def columnCount(self, parent=INVALID_INDEX) -> int:
        return len(self.headers)

    def change(self, row):
        if row < 0 or row >= self.rowCount():
            return None
        page = row // 256
        if page not in self._cache:
            self._cache[page] = self.comparison.page(
                kinds=None if self.kind is None else [self.kind],
                offset=page * 256,
                limit=256,
            )
            while len(self._cache) > 8:
                self._cache.popitem(last=False)
        self._cache.move_to_end(page)
        return self._cache[page][row % 256]

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        change = self.change(index.row())
        if change is None:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return (
                change.kind.value.replace("_", " "),
                change.source or "—",
                change.target or "—",
                change.details,
            )[index.column()]
        if role == Qt.ItemDataRole.UserRole:
            return change
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
            and 0 <= section < len(self.headers)
        ):
            return self.headers[section]
        return None


class SnapshotComparisonDialog(QDialog):
    def __init__(self, comparison: SnapshotComparison, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Snapshot Comparison")
        self.resize(1100, 760)
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(f"Before: {comparison.before_name} · {comparison.before_root}")
        )
        layout.addWidget(
            QLabel(f"After: {comparison.after_name} · {comparison.after_root}")
        )
        note = QLabel(
            "Compares stored metadata. Move and rename matches are candidates; file contents are not hashed."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        row = QHBoxLayout()
        self.filter = QComboBox()
        self.filter.addItem("All changes", None)
        for kind in SnapshotChangeKind:
            self.filter.addItem(
                f"{kind.value.replace('_', ' ').title()} ({comparison.counts[kind]:,})",
                kind,
            )
        self.count_label = QLabel()
        row.addWidget(self.filter)
        row.addWidget(self.count_label)
        row.addStretch(1)
        layout.addLayout(row)
        self.model = ComparisonTableModel(comparison, self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        for column, width in enumerate((150, 280, 280)):
            self.table.setColumnWidth(column, width)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.table)
        details = QSplitter(Qt.Orientation.Horizontal)
        self.before = QPlainTextEdit()
        self.after = QPlainTextEdit()
        for editor in (self.before, self.after):
            editor.setReadOnly(True)
            details.addWidget(editor)
        split.addWidget(details)
        split.setSizes([430, 220])
        layout.addWidget(split, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.filter.currentIndexChanged.connect(self.apply_filter)
        self.table.selectionModel().currentRowChanged.connect(self.show_details)
        self.apply_filter()

    def apply_filter(self) -> None:
        self.model.set_kind(self.filter.currentData())
        self.count_label.setText(f"{self.model.rowCount():,} change(s)")
        self.before.clear()
        self.after.clear()
        if self.model.rowCount():
            self.table.setCurrentIndex(self.model.index(0, 0))

    def show_details(self, current, previous) -> None:
        change = self.model.change(current.row()) if current.isValid() else None
        for editor, value in (
            (self.before, change.before if change else None),
            (self.after, change.after if change else None),
        ):
            editor.setPlainText(
                json.dumps(value, ensure_ascii=False, indent=2) if value else "No item"
            )
