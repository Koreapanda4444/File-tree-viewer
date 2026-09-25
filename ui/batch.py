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
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
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
from persistence import load_rule, organization_presets, save_rule
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
            preset_row = QHBoxLayout()
            self.preset = QComboBox()
            self.preset.addItem("Choose a preset…", "")
            for preset_name in organization_presets():
                self.preset.addItem(preset_name, preset_name)
            self.load_preset_button = QPushButton("Load Preset")
            self.save_rule_button = QPushButton("Save Rule")
            self.load_rule_button = QPushButton("Load Rule")
            preset_row.addWidget(self.preset, 1)
            preset_row.addWidget(self.load_preset_button)
            preset_row.addWidget(self.save_rule_button)
            preset_row.addWidget(self.load_rule_button)
            form.addRow("Reusable rule", preset_row)
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
            self.load_preset_button.clicked.connect(self.load_selected_preset)
            self.save_rule_button.clicked.connect(self.save_current_rule)
            self.load_rule_button.clicked.connect(self.load_saved_rule)
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
                options = self.current_rule()
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

    def current_rule(self) -> OrganizationRule:
        if self.rename:
            raise ValueError("Batch rename settings are not organization rules")
        try:
            minimum = int(self.minimum.text() or "0")
            maximum = int(self.maximum.text()) if self.maximum.text() else None
        except ValueError as error:
            raise ValueError("File sizes must be whole numbers") from error
        try:
            after = date.fromisoformat(self.after.text()) if self.after.text() else None
            before = (
                date.fromisoformat(self.before.text()) if self.before.text() else None
            )
        except ValueError as error:
            raise ValueError("Dates must use YYYY-MM-DD") from error
        return OrganizationRule(
            destination=self.destination.text(),
            pattern=self.pattern.text(),
            extensions=self.extensions.text(),
            minimum=minimum,
            maximum=maximum,
            after=after,
            before=before,
            grouping=self.grouping.currentText(),
        )

    def set_rule(self, rule: OrganizationRule) -> None:
        self.destination.setText(rule.destination)
        self.pattern.setText(rule.pattern)
        self.extensions.setText(rule.extensions)
        self.minimum.setText(str(rule.minimum) if rule.minimum else "")
        self.maximum.setText(str(rule.maximum) if rule.maximum is not None else "")
        self.after.setText(rule.after.isoformat() if rule.after is not None else "")
        self.before.setText(rule.before.isoformat() if rule.before is not None else "")
        self.grouping.setCurrentText(rule.grouping)
        self.invalidate()

    def load_selected_preset(self) -> None:
        name = self.preset.currentData()
        if not name:
            return
        self.set_rule(organization_presets()[name])
        self.status.setText(f"Loaded {name} preset. Preview to review its operations.")

    def save_current_rule(self) -> None:
        try:
            rule = self.current_rule()
        except ValueError as error:
            QMessageBox.warning(self, "Cannot save rule", str(error))
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Organization Rule",
            "organization-rule.json",
            "File Tree Viewer Rules (*.json);;All Files (*)",
        )
        if not path:
            return
        try:
            save_rule(path, rule)
        except (OSError, TypeError, ValueError) as error:
            QMessageBox.warning(self, "Cannot save rule", str(error))
            return
        self.status.setText("Organization rule saved.")

    def load_saved_rule(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load Organization Rule",
            "",
            "File Tree Viewer Rules (*.json);;All Files (*)",
        )
        if not path:
            return
        try:
            rule = load_rule(path)
        except (OSError, UnicodeError, TypeError, ValueError) as error:
            QMessageBox.warning(self, "Cannot load rule", str(error))
            return
        self.set_rule(rule)
        self.preset.setCurrentIndex(0)
        self.status.setText("Organization rule loaded. Preview before adding it.")

    def accept(self):
        if self.iterator is None and self.add_button.isEnabled():
            super().accept()

    def done(self, result):
        self._stop()
        if result != QDialog.DialogCode.Accepted:
            self.model.clear()
        super().done(result)
