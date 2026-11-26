from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPalette, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHeaderView,
    QListView,
    QStackedWidget,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from ui.i18n import add_choices, choice_text, set_choice_text, tr
from ui.widgets import FlowLayout


class FolderViews(QWidget):
    def __init__(self, details):
        super().__init__()
        self.details = details
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        toolbar = FlowLayout()
        self.mode = QComboBox()
        add_choices(self.mode, ("Details", "List", "Large Icons"))
        self.mode.setToolTip(tr("Folder view mode"))
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
        self.mode.currentIndexChanged.connect(
            lambda: self.set_mode(choice_text(self.mode))
        )

    @property
    def active_view(self):
        return self.stack.currentWidget()

    def set_mode(self, mode):
        if mode not in ("Details", "List", "Large Icons"):
            mode = "Details"
        if choice_text(self.mode) != mode:
            set_choice_text(self.mode, mode)
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


class HierarchyTree(QTreeView):
    """Native branch controls with quiet, palette-aware ancestor guides."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("hierarchyTree")
        self.setIndentation(22)
        self.setAnimated(False)
        self.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.setIconSize(QSize(20, 20))
        self.setAllColumnsShowFocus(True)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.header().setStretchLastSection(False)
        self.header().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)

    def drawBranches(self, painter, rectangle, index):
        painter.save()
        color = self.palette().color(QPalette.ColorRole.Text)
        color.setAlpha(45)
        painter.setPen(QPen(color, 1))
        ancestor, depth = index.parent(), 1
        while ancestor.isValid() and depth <= 128:
            if ancestor.row() + 1 < self.model().rowCount(ancestor.parent()):
                x = (
                    rectangle.right()
                    - self.indentation() * depth
                    + self.indentation() // 2
                )
                if self.layoutDirection() == Qt.LayoutDirection.RightToLeft:
                    x = (
                        rectangle.left()
                        + self.indentation() * depth
                        - self.indentation() // 2
                    )
                painter.drawLine(x, rectangle.top(), x, rectangle.bottom())
            ancestor = ancestor.parent()
            depth += 1
        painter.restore()
        super().drawBranches(painter, rectangle, index)
