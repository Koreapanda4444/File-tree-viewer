from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QMenu,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QWidget,
)


class AddressBar(QWidget):
    navigate = Signal(object)

    def __init__(self, edit):
        super().__init__()
        self.edit = edit
        self.path = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        self.stack.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.stack.setMinimumWidth(1)
        self.crumbs = QWidget()
        self.crumb_layout = QHBoxLayout(self.crumbs)
        self.crumb_layout.setContentsMargins(0, 0, 0, 0)
        self.crumb_layout.setSpacing(2)
        self.stack.addWidget(edit)
        self.stack.addWidget(self.crumbs)
        layout.addWidget(self.stack, 1)
        self.edit_button = QToolButton()
        self.edit_button.setText("Edit Path")
        self.edit_button.setToolTip("Enter a path (Alt+D or Ctrl+L)")
        self.edit_button.clicked.connect(self.edit_path)
        layout.addWidget(self.edit_button)
        edit.installEventFilter(self)

    def edit_path(self):
        self.stack.setCurrentWidget(self.edit)
        self.edit.setFocus()
        self.edit.selectAll()

    def set_path(self, path):
        self.path = Path(path) if path is not None else None
        self.edit.setText(str(path or ""))
        while self.crumb_layout.count():
            item = self.crumb_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        if self.path is None:
            self.stack.setCurrentWidget(self.edit)
            return
        ancestors = [*reversed(self.path.parents), self.path]
        self.paths = tuple(ancestors)
        for index, ancestor in enumerate(ancestors):
            if len(ancestors) > 5 and 0 < index < len(ancestors) - 3:
                if index == 1:
                    overflow = QToolButton()
                    overflow.setText("…")
                    menu = QMenu(overflow)
                    for hidden in ancestors[1:-3]:
                        action = menu.addAction(str(hidden).replace("&", "&&"))
                        action.triggered.connect(
                            lambda checked=False, target=hidden: self.navigate.emit(
                                target
                            )
                        )
                    overflow.setMenu(menu)
                    overflow.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
                    self.crumb_layout.addWidget(overflow)
                continue
            button = QToolButton()
            name = ancestor.name or str(ancestor)
            button.setText(
                (name[:29] + "…" if len(name) > 32 else name).replace("&", "&&")
            )
            button.setToolTip(str(ancestor))
            button.clicked.connect(
                lambda checked=False, target=ancestor: self.navigate.emit(target)
            )
            self.crumb_layout.addWidget(button)
        self.crumb_layout.addStretch(1)
        self.stack.setCurrentWidget(self.crumbs)
        self.setToolTip(str(self.path))

    def eventFilter(self, watched, event):
        if (
            watched is self.edit
            and event.type() == QEvent.Type.KeyPress
            and event.key() == Qt.Key.Key_Escape
            and self.path is not None
        ):
            self.set_path(self.path)
            return True
        return super().eventFilter(watched, event)
