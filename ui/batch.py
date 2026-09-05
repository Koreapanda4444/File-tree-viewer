from __future__ import annotations

import sqlite3
import time
from datetime import date

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableView,
    QVBoxLayout,
)

from organization import (
    OrganizationRule,
    RenameOptions,
    batch_operations,
    scope_entries,
)
from planning import FilePlan, PlanOperation
from snapshot import FileSnapshot, SnapshotEntry

INVALID_INDEX = QModelIndex()


class BatchPreviewModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.operations: list[PlanOperation] = []

    def rowCount(self, parent=INVALID_INDEX):
        return 0 if parent.isValid() else len(self.operations)

    def columnCount(self, parent=INVALID_INDEX):
        return 3

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role not in (
            Qt.ItemDataRole.DisplayRole,
            Qt.ItemDataRole.ToolTipRole,
        ):
            return None
        operation = self.operations[index.row()]
        return (
            operation.action.value.upper(),
            str(operation.source or "-"),
            str(operation.target or "-"),
        )[index.column()]

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
            and 0 <= section < 3
        ):
            return ("Action", "Source", "Target")[section]
        return None

    def clear(self):
        self.beginResetModel()
        self.operations.clear()
        self.endResetModel()

    def extend(self, operations):
        if not operations:
            return
        first = len(self.operations)
        self.beginInsertRows(QModelIndex(), first, first + len(operations) - 1)
        self.operations.extend(operations)
        self.endInsertRows()


class BatchDialog(QDialog):
    def __init__(
        self,
        snapshot: FileSnapshot,
        plan: FilePlan,
        selected: tuple[SnapshotEntry, ...],
        *,
        rename: bool,
        parent=None,
    ):
        super().__init__(parent)
        self.snapshot = snapshot
        self.plan = plan
        self.selected = selected
        self.rename = rename
        self.iterator = None
        self.examined = 0
        self.setWindowTitle("Batch Rename" if rename else "Organization Rule")
        self.resize(850, 720)
        layout = QVBoxLayout(self)
        self.settings = QGroupBox(
            "Rename selected items"
            if rename
            else "Match files (all conditions must match)"
        )
        form = QFormLayout(self.settings)
        layout.addWidget(self.settings)
        if rename:
            self.prefix = self._text(form, "Prefix")
            self.suffix = self._text(form, "Suffix (before number / extension)")
            self.find_text = self._text(form, "Replace text (case-sensitive)")
            self.replace_text = self._text(form, "With")
            self.case = self._combo(form, "Case", ("Keep", "lowercase", "UPPERCASE"))
            self.keep_extension = QCheckBox("Keep file extension unchanged")
            self.keep_extension.setChecked(True)
            form.addRow(self.keep_extension)
            self.number = QCheckBox("Append sequence number (path order)")
            form.addRow(self.number)
            self.start = QSpinBox()
            self.start.setRange(0, 1_000_000_000)
            self.start.setValue(1)
            form.addRow("Start", self.start)
            self.digits = QSpinBox()
            self.digits.setRange(1, 12)
            self.digits.setValue(3)
            form.addRow("Minimum digits", self.digits)
        else:
            self.scope = self._combo(
                form, "Scope", ("Selected items and descendants", "Entire snapshot")
            )
            if not selected:
                self.scope.setCurrentIndex(1)
            self.pattern = self._text(form, "Name pattern", "*")
            self.extensions = self._text(
                form, "Extensions", "", "jpg; png; pdf (blank = all)"
            )
            self.minimum = self._text(
                form, "Minimum bytes", "", "Blank = 0; e.g. 1048576 = 1 MiB"
            )
            self.maximum = self._text(form, "Maximum bytes", "", "Blank = unlimited")
            self.after = self._text(
                form, "Modified from (inclusive)", "", "YYYY-MM-DD, local time"
            )
            self.before = self._text(
                form, "Modified through (inclusive)", "", "YYYY-MM-DD, local time"
            )
            self.destination = self._text(
                form, "Destination under Plan root", "", "e.g. Images or Documents/PDF"
            )
            self.grouping = self._combo(
                form, "Subfolders", ("None", "Extension", "Year", "Year / Month")
            )
        note = QLabel(
            "Preview → Add to Plan. Files stay unchanged. Name conflicts are not resolved here.\nItems already changed in the Plan, links and inaccessible items are skipped."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.preview_button = QPushButton("Preview")
        self.preview_button.clicked.connect(self.build_preview)
        layout.addWidget(self.preview_button)
        self.status = QLabel("Choose settings, then preview.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.model = BatchPreviewModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setColumnWidth(0, 130)
        self.table.setColumnWidth(1, 280)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.add_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.add_button.setText("Add to Plan")
        self.add_button.setEnabled(False)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.timer = QTimer(self)
        self.timer.setInterval(0)
        self.timer.timeout.connect(self._advance)
        for widget in self.settings.findChildren(QLineEdit):
            widget.textChanged.connect(self.invalidate)
        for widget in self.settings.findChildren(QComboBox):
            widget.currentIndexChanged.connect(self.invalidate)
        for widget in self.settings.findChildren(QCheckBox):
            widget.toggled.connect(self.invalidate)
        for widget in self.settings.findChildren(QSpinBox):
            widget.valueChanged.connect(self.invalidate)

    @staticmethod
    def _text(form, label, value="", placeholder=""):
        widget = QLineEdit(value)
        widget.setPlaceholderText(placeholder)
        form.addRow(label, widget)
        return widget

    @staticmethod
    def _combo(form, label, items):
        widget = QComboBox()
        widget.addItems(items)
        form.addRow(label, widget)
        return widget

    @property
    def operations(self):
        return self.model.operations

    def _stop(self):
        self.timer.stop()
        if self.iterator is not None:
            self.iterator.close()
            self.iterator = None
        self.settings.setEnabled(True)
        self.preview_button.setEnabled(True)

    def invalidate(self, *_):
        self._stop()
        self.model.clear()
        self.add_button.setEnabled(False)
        self.status.setText("Settings changed. Preview again.")

    def build_preview(self):
        self.invalidate()
        try:
            if self.rename:
                options = RenameOptions(
                    prefix=self.prefix.text(),
                    suffix=self.suffix.text(),
                    find=self.find_text.text(),
                    replacement=self.replace_text.text(),
                    case=self.case.currentText(),
                    keep_extension=self.keep_extension.isChecked(),
                    number=self.number.isChecked(),
                    start=self.start.value(),
                    digits=self.digits.value(),
                )
                entries = sorted(
                    self.selected,
                    key=lambda entry: (
                        entry.path.as_posix().casefold(),
                        entry.path.as_posix(),
                    ),
                )
            else:
                options = OrganizationRule(
                    destination=self.destination.text(),
                    pattern=self.pattern.text(),
                    extensions=self.extensions.text(),
                    minimum=int(self.minimum.text() or "0"),
                    maximum=int(self.maximum.text()) if self.maximum.text() else None,
                    after=date.fromisoformat(self.after.text())
                    if self.after.text()
                    else None,
                    before=date.fromisoformat(self.before.text())
                    if self.before.text()
                    else None,
                    grouping=self.grouping.currentText(),
                )
                entries = scope_entries(
                    self.snapshot,
                    self.selected if self.scope.currentIndex() == 0 else None,
                )
            self.iterator = batch_operations(self.snapshot, self.plan, entries, options)
        except ValueError as error:
            self.status.setText(str(error))
            return
        self.examined = 0
        self.settings.setEnabled(False)
        self.preview_button.setEnabled(False)
        self.status.setText("Building preview… Cancel discards this batch.")
        self.timer.start()

    def _advance(self):
        operations = []
        complete = False
        deadline = time.monotonic() + 0.008
        try:
            for _ in range(256):
                operation = next(self.iterator)
                self.examined += 1
                if operation is not None:
                    operations.append(operation)
                if time.monotonic() >= deadline:
                    break
        except StopIteration:
            complete = True
        except (ValueError, OSError, RuntimeError, sqlite3.Error) as error:
            self.invalidate()
            self.status.setText(f"Cannot build preview: {error}")
            return
        self.model.extend(operations)
        self.status.setText(
            f"{len(self.operations):,} planned operations; {self.examined:,} steps processed…"
        )
        if complete:
            self._stop()
            self.status.setText(
                f"Preview ready: {len(self.operations):,} operations (including new folders). Add to Plan to stage them."
            )
            self.add_button.setEnabled(bool(self.operations))

    def accept(self):
        if self.iterator is None and self.add_button.isEnabled():
            super().accept()

    def done(self, result):
        self._stop()
        if result != QDialog.DialogCode.Accepted:
            self.model.clear()
        super().done(result)
