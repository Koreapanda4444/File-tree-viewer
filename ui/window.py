from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTreeView,
    QVBoxLayout,
    QWidget,
)


class ExplorerPage(QWidget):
    def __init__(self, *, virtual: bool) -> None:
        super().__init__()
        self.virtual = virtual

        layout = QVBoxLayout(self)
        layout.addLayout(self._create_location_row())
        layout.addLayout(self._create_search_row())
        layout.addWidget(self._create_content(), 1)
        layout.addLayout(self._create_action_row())

    def _create_location_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(QLabel("Workspace" if self.virtual else "Root"))

        self.location = QLineEdit()
        self.location.setReadOnly(True)
        if self.virtual:
            self.location.setText("VM:/")
        else:
            self.location.setPlaceholderText("Select a folder or drive")
        row.addWidget(self.location, 1)

        if self.virtual:
            button_names = ("Save", "Load", "Export", "Reset")
        else:
            button_names = ("Select Root", "Refresh")

        self.location_buttons = []
        for name in button_names:
            button = QPushButton(name)
            button.setEnabled(False)
            self.location_buttons.append(button)
            row.addWidget(button)

        self.show_hidden = QCheckBox("Show hidden")
        self.show_hidden.setVisible(not self.virtual)
        self.show_hidden.setEnabled(False)
        row.addWidget(self.show_hidden)
        return row

    def _create_search_row(self) -> QHBoxLayout:
        row = QHBoxLayout()

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search files and folders")
        self.search_input.setEnabled(False)

        self.search_button = QPushButton("Search")
        self.clear_search_button = QPushButton("Clear")
        self.search_button.setEnabled(False)
        self.clear_search_button.setEnabled(False)

        row.addWidget(self.search_input, 1)
        row.addWidget(self.search_button)
        row.addWidget(self.clear_search_button)
        return row

    def _create_content(self) -> QSplitter:
        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.tree = QTreeView()
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tree.setUniformRowHeights(True)

        empty_model = QStandardItemModel(self.tree)
        empty_model.setHorizontalHeaderLabels(("Name", "Type", "Size", "Modified"))
        self.tree.setModel(empty_model)
        self.tree.header().resizeSection(0, 360)
        splitter.addWidget(self.tree)

        details = QWidget()
        details_layout = QVBoxLayout(details)
        details_layout.setContentsMargins(0, 0, 0, 0)

        details_group = QGroupBox("Details")
        details_form = QFormLayout(details_group)
        self.detail_values = {}
        for label in ("Name", "Path", "Type", "Size", "Modified"):
            value = QLabel("-")
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setWordWrap(label == "Path")
            self.detail_values[label.lower()] = value
            details_form.addRow(f"{label}:", value)
        details_layout.addWidget(details_group)

        preview_group = QGroupBox("Preview")
        preview_layout = QVBoxLayout(preview_group)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("Select a file to preview it")
        preview_layout.addWidget(self.preview)
        details_layout.addWidget(preview_group, 1)

        splitter.addWidget(details)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        return splitter

    def _create_action_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        action_names = (
            "New File",
            "New Folder",
            "Rename",
            "Copy",
            "Move",
            "Delete",
            "Undo",
        )

        self.action_buttons = []
        for name in action_names:
            button = QPushButton(name)
            button.setEnabled(False)
            self.action_buttons.append(button)
            row.addWidget(button)

        row.addStretch(1)
        return row


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("File Tree Viewer")
        self.resize(1400, 850)
        self.setMinimumSize(1000, 650)

        self.tabs = QTabWidget()
        self.real_page = ExplorerPage(virtual=False)
        self.virtual_page = ExplorerPage(virtual=True)
        self.tabs.addTab(self.real_page, "Real File System")
        self.tabs.addTab(self.virtual_page, "Virtual File System")

        self.setCentralWidget(self.tabs)
        self.statusBar().showMessage("Ready")
