from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QHBoxLayout, QTabBar, QToolButton, QWidget

from real.navigation import NavigationHistory


@dataclass(slots=True)
class FolderSession:
    path: object
    history: NavigationHistory
    mode: str = "Details"


class FolderTabs(QWidget):
    def __init__(self, page):
        super().__init__()
        self.page = page
        self.switching = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.bar = QTabBar()
        self.bar.setTabsClosable(True)
        self.bar.setMovable(True)
        self.bar.setExpanding(False)
        self.bar.setUsesScrollButtons(True)
        self.active = FolderSession(page.model.root_path, page.history)
        index = self.bar.addTab("New Tab")
        self.bar.setTabData(index, self.active)
        layout.addWidget(self.bar, 1)
        self.add_button = QToolButton()
        self.add_button.setText("+")
        self.add_button.setToolTip("New folder tab (Ctrl+T)")
        self.add_button.clicked.connect(self.new_tab)
        layout.addWidget(self.add_button)
        self.bar.currentChanged.connect(self.switch)
        self.bar.tabCloseRequested.connect(self.close_tab)
        page.location_changed.connect(self.sync)
        for shortcut, callback in (
            ("Ctrl+T", self.new_tab),
            ("Ctrl+W", lambda: self.close_tab(self.bar.currentIndex())),
            ("Ctrl+Tab", lambda: self.cycle(1)),
            ("Ctrl+Shift+Tab", lambda: self.cycle(-1)),
        ):
            action = QAction(self)
            action.setShortcut(shortcut)
            action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            action.triggered.connect(callback)
            page.addAction(action)

    def index_of(self, session):
        return next(
            (i for i in range(self.bar.count()) if self.bar.tabData(i) is session), -1
        )

    def can_change(self, *, confirm=True):
        page = self.page
        if (
            page.close_requested
            or page.operation_is_running
            or page.search_is_running
            or page.preview_is_running
        ):
            page.status_changed.emit(
                "Wait for Real tasks before switching or closing folder tabs"
            )
            return False
        return not confirm or page._confirm_editor_transition()

    def save_active(self):
        self.active.path = self.page.model.root_path
        self.active.history = self.page.history
        self.active.mode = self.page.folder_panel.mode.currentText()

    def sync(self, path):
        if self.switching:
            return
        self.active.path = path
        index = self.index_of(self.active)
        if index >= 0:
            self.bar.setTabText(
                index,
                (path.name or str(path)).replace("&", "&&")
                if path is not None
                else "New Tab",
            )
            self.bar.setTabToolTip(index, str(path))

    def new_tab(self):
        if self.bar.count() >= 32 or not self.can_change():
            return
        self.save_active()
        path = self.page.model.root_path
        history = NavigationHistory()
        if path is not None:
            history.visit(path)
        session = FolderSession(
            path, history, self.page.folder_panel.mode.currentText()
        )
        index = self.bar.addTab(
            (path.name or str(path)).replace("&", "&&")
            if path is not None
            else "New Tab"
        )
        self.bar.setTabData(index, session)
        self.bar.setCurrentIndex(index)

    def switch(self, index):
        target = self.bar.tabData(index) if index >= 0 else None
        if target is None or target is self.active:
            return
        old_index = self.index_of(self.active)
        if not self.can_change():
            with QSignalBlocker(self.bar):
                self.bar.setCurrentIndex(old_index)
            return
        self.save_active()
        old_history = self.page.history
        self.page.history = target.history
        self.page.history_navigation = self.switching = True
        try:
            success = self.page.set_root(target.path)
        finally:
            self.page.history_navigation = self.switching = False
        if success:
            self.active = target
            self.page.folder_panel.mode.setCurrentText(target.mode)
            if target.path is not None:
                self.sync(target.path)
        else:
            self.page.history = old_history
            with QSignalBlocker(self.bar):
                self.bar.setCurrentIndex(old_index)
        self.page.update_navigation_buttons()

    def close_tab(self, index):
        if self.bar.count() <= 1 or not 0 <= index < self.bar.count():
            return
        is_current = index == self.bar.currentIndex()
        if not self.can_change(confirm=is_current):
            return
        closing = self.bar.tabData(index)
        if is_current:
            self.bar.setCurrentIndex(index - 1 if index else 1)
            if self.active is closing:
                return
        self.bar.removeTab(self.index_of(closing))

    def cycle(self, direction):
        self.bar.setCurrentIndex(
            (self.bar.currentIndex() + direction) % self.bar.count()
        )
