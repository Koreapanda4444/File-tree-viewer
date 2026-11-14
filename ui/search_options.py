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

from search.options import SearchOptions
from ui.i18n import add_choice, add_choices, choice_text, set_choice_text, tr
from ui.widgets import PlainLabel


class SearchOptionsDialog(QDialog):
    def __init__(self, options, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Advanced Search Filters"))
        self.resize(520, 460)
        layout = QFormLayout(self)
        self.options = options
        self.extensions = QLineEdit(";".join(options.extensions))
        self.extensions.setPlaceholderText(tr("txt;py;tar.gz (blank = all)"))
        layout.addRow(tr("Extensions"), self.extensions)
        self.mode = QComboBox()
        for label, value in (
            ("Contains text", "literal"),
            ("Wildcard (*, ?, [abc])", "wildcard"),
            ("Regular expression", "regex"),
        ):
            add_choice(self.mode, label, value)
        self.mode.setCurrentIndex(self.mode.findData(options.name_mode))
        layout.addRow(tr("Name matching"), self.mode)
        self.case_sensitive = QCheckBox(tr("Case sensitive"))
        self.case_sensitive.setChecked(options.case_sensitive)
        layout.addRow(self.case_sensitive)
        self.content_query = QLineEdit(options.content_query)
        layout.addRow(tr("File contents contain"), self.content_query)
        self.encoding = QComboBox()
        add_choices(self.encoding, ("auto", "utf-8", "utf-16", "cp949", "cp1252"))
        set_choice_text(self.encoding, options.content_encoding)
        layout.addRow(tr("Content encoding"), self.encoding)
        limits = PlainLabel(
            tr(
                "Content search: at most 64 MiB per file, 128 KiB per line and 20 matching lines per file. Binary, inaccessible and unsupported text files are counted as skipped."
            )
        )
        limits.setWordWrap(True)
        layout.addRow(limits)
        self.kind = QComboBox()
        for label, value in (
            ("Files and folders", "all"),
            ("Files", "files"),
            ("Folders", "folders"),
        ):
            add_choice(self.kind, label, value)
        self.kind.setCurrentIndex(self.kind.findData(options.kind))
        layout.addRow(tr("Type"), self.kind)
        self.scope = QComboBox()
        for label, value in (
            ("Current folder and descendants", "recursive"),
            ("Current folder only", "current"),
            ("Entire filesystem / drive root", "filesystem"),
        ):
            add_choice(self.scope, label, value)
        self.scope.setCurrentIndex(self.scope.findData(options.scope))
        layout.addRow(tr("Scope"), self.scope)
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
            tr(
                "Creation dates use filesystem birth time. Items without a birth time do not match creation-date filters. All selected conditions must match."
            )
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
                content_encoding=choice_text(self.encoding),
                name_mode=self.mode.currentData(),
                case_sensitive=self.case_sensitive.isChecked(),
                extensions=tuple(self.extensions.text().split(";")),
                kind=self.kind.currentData(),
                scope=self.scope.currentData(),
                **values,
            )
        except (ValueError, OverflowError) as error:
            QMessageBox.warning(self, tr("Invalid search filters"), str(error))
            return
        self.accept()
