from __future__ import annotations

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


def is_dark():
    return QApplication.palette().color(QPalette.ColorRole.Window).lightness() < 128


def status_color(status):
    kind = str(getattr(status, "value", status)).lower()
    dark = is_dark()
    if any(word in kind for word in ("error", "failed", "conflict")):
        return QColor("#ff9bad" if dark else "#a71035")
    if any(word in kind for word in ("modified", "skip", "cancel", "rollback")):
        return QColor("#f4ca78" if dark else "#865900")
    if "only" in kind:
        return QColor("#91c4ff" if dark else "#225c99")
    return QColor("#8bdbab" if dark else "#23703a")


def difference_background(kind):
    colors = (
        ("#51462b", "#542f3b", "#274637")
        if is_dark()
        else ("#f6e3b4", "#f8d7da", "#d4edda")
    )
    return QColor(colors[0 if kind == "replace" else 1 if kind == "delete" else 2])


class ThemeManager(QObject):
    def __init__(self, parent):
        super().__init__(parent)
        self.mode = "System"
        self.app = QApplication.instance()
        self.app.styleHints().colorSchemeChanged.connect(self.system_changed)

    def system_changed(self, scheme):
        if self.mode == "System":
            self.apply(self.mode)

    def apply(self, mode):
        self.mode = mode if mode in {"Light", "Dark", "System"} else "System"
        dark = self.mode == "Dark" or (
            self.mode == "System"
            and self.app.styleHints().colorScheme() == Qt.ColorScheme.Dark
        )
        palette = QPalette()
        colors = {
            QPalette.ColorRole.Window: "#20242b" if dark else "#f4f6f9",
            QPalette.ColorRole.WindowText: "#e4e9f1" if dark else "#17243a",
            QPalette.ColorRole.Base: "#171b21" if dark else "#ffffff",
            QPalette.ColorRole.AlternateBase: "#252b34" if dark else "#edf1f7",
            QPalette.ColorRole.Text: "#e4e9f1" if dark else "#17243a",
            QPalette.ColorRole.Button: "#303743" if dark else "#e8edf5",
            QPalette.ColorRole.ButtonText: "#e4e9f1" if dark else "#17243a",
            QPalette.ColorRole.Highlight: "#315c91" if dark else "#bdd6fa",
            QPalette.ColorRole.HighlightedText: "#ffffff" if dark else "#102844",
            QPalette.ColorRole.ToolTipBase: "#303743" if dark else "#ffffff",
            QPalette.ColorRole.ToolTipText: "#e4e9f1" if dark else "#17243a",
            QPalette.ColorRole.Link: "#91c4ff" if dark else "#225c99",
            QPalette.ColorRole.PlaceholderText: "#a2abba" if dark else "#68778a",
        }
        for role, color in colors.items():
            palette.setColor(role, QColor(color))
        for role in (
            QPalette.ColorRole.Text,
            QPalette.ColorRole.WindowText,
            QPalette.ColorRole.ButtonText,
        ):
            palette.setColor(
                QPalette.ColorGroup.Disabled,
                role,
                QColor("#87909d" if dark else "#7c8794"),
            )
        self.app.setPalette(palette)
        for widget in self.app.topLevelWidgets():
            widget.update()
