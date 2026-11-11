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
            QPalette.ColorRole.Window: "#202724" if dark else "#eeeee8",
            QPalette.ColorRole.WindowText: "#e4eae5" if dark else "#25352d",
            QPalette.ColorRole.Base: "#171e1b" if dark else "#fcfcf8",
            QPalette.ColorRole.AlternateBase: "#202a24" if dark else "#f3f4ee",
            QPalette.ColorRole.Text: "#e4eae5" if dark else "#25352d",
            QPalette.ColorRole.Button: "#303d34" if dark else "#f7f7f1",
            QPalette.ColorRole.ButtonText: "#e4eae5" if dark else "#25352d",
            QPalette.ColorRole.Highlight: "#235f43" if dark else "#d5e7d9",
            QPalette.ColorRole.HighlightedText: "#ffffff" if dark else "#123c26",
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
        border = "#435147" if dark else "#b8c0b5"
        surface = "#28332c" if dark else "#f5f5ef"
        accent = "#88c9a2" if dark else "#215b3c"
        self.app.setStyleSheet(f"""
            QWidget {{ font-size: 10pt; }}
            QMainWindow {{ background: palette(window); }}
            #workspaceHeader {{ background: #16432d; border-bottom: 3px solid #c4ae62; }}
            #applicationTitle {{ color: #fffef4; font-size: 17pt; font-weight: 600; }}
            #applicationSubtitle, #workspaceContext {{ color: #c4d8c9; font-size: 9pt; }}
            QMenuBar {{ padding: 3px 8px; }}
            QMenu {{ padding: 5px; }}
            QMenu::item {{ padding: 7px 24px 7px 12px; }}
            QTabWidget::pane {{ border: 0; border-top: 1px solid {border}; }}
            QTabBar::tab {{ padding: 8px 18px; margin-right: 2px; background: {surface}; border: 1px solid {border}; }}
            QTabBar::tab:selected {{ color: {accent}; border-bottom: 3px solid {accent}; font-weight: 600; }}
            QPushButton, QToolButton {{ padding: 5px 9px; min-height: 20px; border: 1px solid {border}; border-radius: 2px; background: {surface}; }}
            QPushButton:hover, QToolButton:hover {{ border-color: {accent}; }}
            QPushButton:pressed, QToolButton:pressed {{ background: palette(highlight); }}
            QPushButton:focus, QToolButton:focus, QLineEdit:focus, QComboBox:focus {{ border: 1px solid {accent}; }}
            QPushButton[primary="true"] {{ background: #215b3c; color: white; border-color: #215b3c; font-weight: 600; }}
            QPushButton[primary="true"]:disabled {{ background: {surface}; color: palette(disabled, button-text); border-color: {border}; }}
            QPushButton[danger="true"]:enabled {{ color: {"#ffa59b" if dark else "#9b332b"}; }}
            QLineEdit, QSpinBox, QComboBox {{ padding: 5px 7px; min-height: 20px; border: 1px solid {border}; border-radius: 2px; background: palette(base); }}
            QComboBox {{ padding-right: 24px; }}
            QComboBox::drop-down {{ width: 20px; border-left: 1px solid {border}; }}
            QTreeView, QListView, QTableView, QPlainTextEdit {{ border: 1px solid {border}; outline: 0; }}
            QTreeView::item, QListView::item, QTableView::item {{ min-height: 26px; }}
            QTreeView::item:selected, QListView::item:selected, QTableView::item:selected {{ background: palette(highlight); color: palette(highlighted-text); }}
            QHeaderView::section {{ background: {surface}; border: 0; border-bottom: 1px solid {border}; border-right: 1px solid {border}; padding: 6px; font-weight: 600; }}
            QGroupBox {{ border: 1px solid {border}; border-radius: 2px; margin-top: 12px; padding: 10px; font-weight: 600; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {accent}; }}
            QSplitter::handle {{ background: palette(window); width: 7px; height: 7px; }}
            QSplitter::handle:hover {{ background: palette(highlight); }}
            QStatusBar {{ border-top: 1px solid {border}; padding: 3px; }}
            QDialogButtonBox {{ padding-top: 8px; }}
            QToolTip {{ padding: 5px; border: 1px solid {border}; }}
        """)
        for widget in self.app.topLevelWidgets():
            widget.update()
