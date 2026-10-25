from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QListView,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ui.widgets import FlowLayout


class FolderViews(QWidget):
    def __init__(self, details):
        super().__init__()
        self.details = details
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        toolbar = FlowLayout()
        self.mode = QComboBox()
        self.mode.addItems(("Details", "List", "Large Icons"))
        self.mode.setToolTip("Folder view mode")
        toolbar.addWidget(self.mode)
        layout.addLayout(toolbar)
        self.toolbar = toolbar
        self.icons = QListView()
        self.icons.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.icons.setUniformItemSizes(True)
        self.icons.setLayoutMode(QListView.LayoutMode.Batched)
        self.icons.setBatchSize(128)
        self.icons.setResizeMode(QListView.ResizeMode.Adjust)
        self.stack = QStackedWidget()
        self.stack.addWidget(details)
        self.stack.addWidget(self.icons)
        layout.addWidget(self.stack)
        self.mode.currentTextChanged.connect(self.set_mode)

    @property
    def active_view(self):
        return self.stack.currentWidget()

    def set_mode(self, mode):
        if mode not in ("Details", "List", "Large Icons"):
            mode = "Details"
        if self.mode.currentText() != mode:
            self.mode.setCurrentText(mode)
            return
        if mode == "Details":
            self.stack.setCurrentWidget(self.details)
        else:
            large = mode == "Large Icons"
            self.icons.setViewMode(
                QListView.ViewMode.IconMode if large else QListView.ViewMode.ListMode
            )
            self.icons.setMovement(QListView.Movement.Static)
            self.icons.setWrapping(large)
            self.icons.setWordWrap(large)
            self.icons.setIconSize(QSize(64, 64) if large else QSize(20, 20))
            self.icons.setGridSize(QSize(128, 112) if large else QSize())
            self.icons.setSpacing(4 if large else 1)
            self.stack.setCurrentWidget(self.icons)
        index = self.active_view.currentIndex()
        if index.isValid():
            self.active_view.scrollTo(index)

    def set_model(self, model, selection):
        self.details.setModel(model)
        self.icons.setModel(model)
        for view in (self.details, self.icons):
            view.setSelectionModel(selection)

    def set_root(self, index):
        self.details.setRootIndex(index)
        self.icons.setRootIndex(index)
