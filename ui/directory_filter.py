from __future__ import annotations

import re

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from real.directory_index import DirectoryFilter
from ui.i18n import add_choice, tr
from ui.widgets import FlowLayout


class DirectoryFilterBar(QWidget):
    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.restoring = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = FlowLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText(tr("Filter root folder by name…"))
        self.name.setMaximumWidth(260)
        self.extensions = QLineEdit()
        self.extensions.setPlaceholderText(tr("Extensions: .txt, .png"))
        self.extensions.setMaximumWidth(180)
        self.kind = QComboBox()
        for title, value in (
            ("All items", "all"),
            ("Files only", "files"),
            ("Folders only", "folders"),
        ):
            add_choice(self.kind, title, value)
        self.retain = QCheckBox(tr("Keep on navigation"))
        self.clear = QPushButton(tr("Clear folder filter"))
        self.count = QLabel()
        for widget in (
            self.name,
            self.extensions,
            self.kind,
            self.retain,
            self.clear,
            self.count,
        ):
            row.addWidget(widget)
        layout.addLayout(row)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(120)
        self.timer.timeout.connect(self.apply)
        page.location_changed.connect(
            lambda path: self.setToolTip(
                tr(
                    "Filters direct children of the explorer root: {path}",
                    path=path or "—",
                )
            )
        )
        self.name.textChanged.connect(self.changed)
        self.extensions.textChanged.connect(self.changed)
        self.kind.currentIndexChanged.connect(self.changed)
        self.clear.clicked.connect(self.reset)
        page.model.listing_counts.connect(self.show_counts)

    def options(self):
        extensions = tuple(
            dict.fromkeys(
                "." + part.lower().lstrip(".")
                for part in re.split(r"[,;\s]+", self.extensions.text().strip())
                if part.strip(".")
            )
        )
        return DirectoryFilter(
            self.name.text()[:1024], extensions[:100], self.kind.currentData()
        )

    def changed(self):
        if not self.restoring:
            self.count.setText(tr("Updating folder filter…"))
            self.timer.start()

    def apply(self):
        if self.page.close_requested:
            return
        self.page.model.set_local_filter(self.options())
        self.page._expand_root()

    def show_counts(self, matched, total):
        self.count.setText(
            tr("Matches: {matched:,} / {total:,}", matched=matched, total=total)
        )

    def reset(self, *, defer=False):
        self.timer.stop()
        self.restoring = True
        self.name.clear()
        self.extensions.clear()
        self.kind.setCurrentIndex(0)
        self.restoring = False
        if defer:
            self.page.model.local_filter = self.options()
        else:
            self.apply()

    def before_navigation(self, path):
        if path != self.page.model.root_path and not self.retain.isChecked():
            self.reset(defer=True)

    def state(self):
        return {
            "name": self.name.text()[:1024],
            "extensions": self.extensions.text()[:1024],
            "kind": self.kind.currentData(),
            "retain": self.retain.isChecked(),
        }

    def restore(self, state):
        if not isinstance(state, dict):
            return
        self.restoring = True
        self.name.setText(str(state.get("name", ""))[:1024])
        self.extensions.setText(str(state.get("extensions", ""))[:1024])
        self.kind.setCurrentIndex(max(0, self.kind.findData(state.get("kind", "all"))))
        self.retain.setChecked(state.get("retain") is True)
        self.restoring = False
        self.apply()
