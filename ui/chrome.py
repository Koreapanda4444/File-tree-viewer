from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMenu,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)


def action_menu(title, buttons):
    control = QToolButton()
    control.setText(title)
    menu = QMenu(control)
    for button in buttons:
        action = QWidgetAction(menu)
        action.setDefaultWidget(button)
        menu.addAction(action)
        button.clicked.connect(menu.close)
    control.setMenu(menu)
    control.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    return control


class WorkspaceHeader(QWidget):
    def __init__(self, tabs):
        super().__init__()
        self.setObjectName("workspaceHeader")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        icon = QLabel()
        icon.setPixmap(QApplication.windowIcon().pixmap(QSize(40, 40)))
        layout.addWidget(icon)
        text = QVBoxLayout()
        text.setSpacing(2)
        title = QLabel("File Tree Viewer")
        title.setObjectName("applicationTitle")
        subtitle = QLabel("Explore. Organize. Plan.")
        subtitle.setObjectName("applicationSubtitle")
        text.addWidget(title)
        text.addWidget(subtitle)
        layout.addLayout(text)
        layout.addStretch()
        self.context = QLabel()
        self.context.setObjectName("workspaceContext")
        self.context.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        layout.addWidget(self.context)
        tabs.currentChanged.connect(self.changed)
        self.changed(0)

    def changed(self, index):
        self.context.setText(
            (
                "Browse actual files",
                "Build a virtual workspace",
                "Review changes before applying",
            )[max(0, min(index, 2))]
        )
