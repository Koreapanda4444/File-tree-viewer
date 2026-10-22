from __future__ import annotations

from datetime import datetime, timedelta, timezone

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
)

from search_options import SearchOptions
from ui.widgets import PlainLabel


class SearchOptionsDialog(QDialog):
    def __init__(self, options, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Advanced Search Filters")
        self.resize(520, 460)
        layout = QFormLayout(self)
        self.options = options
        self.extensions = QLineEdit(";".join(options.extensions))
        self.extensions.setPlaceholderText("txt;py;tar.gz (blank = all)")
        layout.addRow("Extensions", self.extensions)
        self.mode = QComboBox()
        for label, value in (
            ("Contains text", "literal"),
            ("Wildcard (*, ?, [abc])", "wildcard"),
            ("Regular expression", "regex"),
        ):
            self.mode.addItem(label, value)
        self.mode.setCurrentIndex(self.mode.findData(options.name_mode))
        layout.addRow("Name matching", self.mode)
        self.case_sensitive = QCheckBox("Case sensitive")
        self.case_sensitive.setChecked(options.case_sensitive)
        layout.addRow(self.case_sensitive)
        self.content_query = QLineEdit(options.content_query)
        layout.addRow("File contents contain", self.content_query)
        self.encoding = QComboBox()
        self.encoding.addItems(("auto", "utf-8", "utf-16", "cp949", "cp1252"))
        self.encoding.setCurrentText(options.content_encoding)
        layout.addRow("Content encoding", self.encoding)
        limits = PlainLabel(
            "Content search: at most 64 MiB per file, 128 KiB per line and 20 matching lines per file. Binary, inaccessible and unsupported text files are counted as skipped."
        )
        limits.setWordWrap(True)
        layout.addRow(limits)
        self.kind = QComboBox()
        for label, value in (
            ("Files and folders", "all"),
            ("Files", "files"),
            ("Folders", "folders"),
        ):
            self.kind.addItem(label, value)
        self.kind.setCurrentIndex(self.kind.findData(options.kind))
        layout.addRow("Type", self.kind)
        self.scope = QComboBox()
        for label, value in (
            ("Current folder and descendants", "recursive"),
            ("Current folder only", "current"),
            ("Entire filesystem / drive root", "filesystem"),
        ):
            self.scope.addItem(label, value)
        self.scope.setCurrentIndex(self.scope.findData(options.scope))
        layout.addRow("Scope", self.scope)
        self.fields = {}
        for name, label in (
            ("minimum_size", "Minimum bytes"),
            ("maximum_size", "Maximum bytes"),
            ("modified_after", "Modified from (UTC)"),
            ("modified_before", "Modified through (UTC)"),
            ("created_after", "Created from (UTC)"),
            ("created_before", "Created through (UTC)"),
        ):
            value = getattr(options, name)
            text = str(value) if value is not None else ""
            if value is not None and name not in {"minimum_size", "maximum_size"}:
                text = (
                    (
                        datetime(1970, 1, 1, tzinfo=timezone.utc)
                        + timedelta(microseconds=value // 1000)
                    )
                    .date()
                    .isoformat()
                )
            field = self.fields[name] = QLineEdit(text)
            field.setPlaceholderText(
                "No limit" if "size" in name else "YYYY-MM-DD, blank = no limit"
            )
            layout.addRow(label, field)
        note = PlainLabel(
            "Creation dates use filesystem birth time. Items without a birth time do not match creation-date filters. All selected conditions must match."
        )
        note.setWordWrap(True)
        layout.addRow(note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Reset
        )
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Reset).clicked.connect(
            self.reset
        )
        layout.addRow(buttons)

    def reset(self):
        self.content_query.clear()
        self.encoding.setCurrentIndex(0)
        self.mode.setCurrentIndex(0)
        self.case_sensitive.setChecked(False)
        self.extensions.clear()
        self.kind.setCurrentIndex(0)
        self.scope.setCurrentIndex(0)
        for field in self.fields.values():
            field.clear()

    def save(self):
        try:
            values = {}
            for name, field in self.fields.items():
                text = field.text().strip()
                if not text:
                    values[name] = None
                elif "size" in name:
                    values[name] = int(text)
                else:
                    day = datetime.strptime(text, "%Y-%m-%d").replace(
                        tzinfo=timezone.utc
                    )
                    seconds = (
                        day - datetime(1970, 1, 1, tzinfo=timezone.utc)
                    ).days * 86400
                    values[name] = (
                        seconds + (86400 if name.endswith("before") else 0)
                    ) * 1_000_000_000 - (1 if name.endswith("before") else 0)
            self.options = SearchOptions(
                content_query=self.content_query.text(),
                content_encoding=self.encoding.currentText(),
                name_mode=self.mode.currentData(),
                case_sensitive=self.case_sensitive.isChecked(),
                extensions=tuple(self.extensions.text().split(";")),
                kind=self.kind.currentData(),
                scope=self.scope.currentData(),
                **values,
            )
        except (ValueError, OverflowError) as error:
            QMessageBox.warning(self, "Invalid search filters", str(error))
            return
        self.accept()
