from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ui.preferences import application_settings


@dataclass(slots=True)
class Command:
    key: str
    title: str
    callback: object
    available: object
    dangerous: bool = False


def load_keys(settings, key):
    try:
        values = json.loads(settings.value("commands/" + key, "[]"))
        return (
            list(
                dict.fromkeys(
                    value
                    for value in values
                    if isinstance(value, str) and len(value) < 1024
                )
            )[:100]
            if isinstance(values, list)
            else []
        )
    except (TypeError, ValueError):
        return []


def collect_commands(window):
    commands, seen = [], set()
    for menu_action in window.menuBar().actions():
        menu = menu_action.menu()
        if menu is None:
            continue
        for action in menu.actions():
            if action.isSeparator() or action.menu() is not None:
                continue
            title = (
                menu.title().replace("&", "") + " / " + action.text().replace("&", "")
            )
            if title in seen:
                continue
            seen.add(title)
            commands.append(
                Command(
                    title,
                    title,
                    action.trigger,
                    action.isEnabled,
                    any(
                        word in title.lower()
                        for word in ("delete", "reset", "undo", "recover", "apply")
                    ),
                )
            )
    page = window.tabs.currentWidget()
    for button in page.findChildren(QPushButton):
        title = button.text().replace("&", "")
        key = window.tabs.tabText(window.tabs.currentIndex()) + " / " + title
        if not title or key in seen or not button.isVisibleTo(page):
            continue
        seen.add(key)
        commands.append(
            Command(
                key,
                key,
                button.click,
                button.isEnabled,
                any(
                    word in title.lower()
                    for word in (
                        "delete",
                        "reset",
                        "undo",
                        "recover",
                        "apply",
                        "remove",
                    )
                ),
            )
        )
    return commands


class CommandPalette(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.settings = application_settings()
        self.recent = load_keys(self.settings, "recent")
        self.favorites = load_keys(self.settings, "favorites")
        self.commands = collect_commands(window)
        for key in dict.fromkeys([*self.favorites, *self.recent]):
            if key.startswith("folder:"):
                path = Path(key[len("folder:") :])
                self.commands.append(
                    Command(
                        key,
                        "Go to folder: " + str(path),
                        lambda p=path: self.navigate(p),
                        lambda: True,
                    )
                )
        self.visible_commands = []
        self.setWindowTitle("Quick Commands")
        self.resize(780, 520)
        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search commands or enter a folder path…")
        self.search.installEventFilter(self)
        self.search.textChanged.connect(self.filter_commands)
        self.search.returnPressed.connect(self.run_selected)
        layout.addWidget(self.search)
        self.list = QListWidget()
        self.list.itemActivated.connect(lambda item: self.run_selected())
        layout.addWidget(self.list, 1)
        controls = QHBoxLayout()
        self.favorite_button = QPushButton("Toggle Favorite")
        self.favorite_button.clicked.connect(self.toggle_favorite)
        self.run_button = QPushButton("Run Command")
        self.run_button.clicked.connect(self.run_selected)
        controls.addWidget(self.favorite_button)
        controls.addWidget(self.run_button)
        layout.addLayout(controls)
        self.filter_commands()
        self.search.setFocus()

    def filter_commands(self):
        query = self.search.text().strip()
        terms = query.casefold().split()
        self.visible_commands = [
            command
            for command in self.commands
            if all(term in command.title.casefold() for term in terms)
        ]
        self.visible_commands.sort(
            key=lambda command: (
                command.key not in self.favorites,
                command.key not in self.recent,
                self.recent.index(command.key) if command.key in self.recent else 100,
                command.title,
            )
        )
        if query and (
            os.path.isabs(os.path.expandvars(query)) or query.startswith("~")
        ):
            path = Path(os.path.expandvars(query)).expanduser()
            if "\x00" not in str(path):
                self.visible_commands.insert(
                    0,
                    Command(
                        "folder:" + str(path),
                        "Go to folder: " + str(path),
                        lambda: self.navigate(path),
                        lambda: True,
                    ),
                )
        self.list.clear()
        for command in self.visible_commands:
            item = QListWidgetItem(
                ("★ " if command.key in self.favorites else "")
                + command.title
                + (" — confirmation required" if command.dangerous else "")
            )
            if not command.available():
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)

    def eventFilter(self, watched, event):
        if (
            watched is self.search
            and event.type() == QEvent.Type.KeyPress
            and event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up)
        ):
            step = 1 if event.key() == Qt.Key.Key_Down else -1
            self.list.setCurrentRow(
                max(0, min(self.list.count() - 1, self.list.currentRow() + step))
            )
            return True
        return super().eventFilter(watched, event)

    def navigate(self, path):
        self.window.tabs.setCurrentWidget(self.window.real_page)
        self.window.real_page.set_root(path)

    def selected(self):
        index = self.list.currentRow()
        return (
            self.visible_commands[index]
            if 0 <= index < len(self.visible_commands)
            else None
        )

    def save(self):
        self.settings.setValue("commands/recent", json.dumps(self.recent[:30]))
        self.settings.setValue("commands/favorites", json.dumps(self.favorites[:100]))
        self.settings.sync()

    def toggle_favorite(self):
        command = self.selected()
        if command is None:
            return
        if command.key in self.favorites:
            self.favorites.remove(command.key)
        else:
            self.favorites.insert(0, command.key)
        self.save()
        self.filter_commands()

    def run_selected(self):
        command = self.selected()
        if command is None or not command.available():
            return
        if command.dangerous:
            answer = QMessageBox.question(
                self,
                "Confirm Command",
                command.title
                + "? Review any following operation dialog before applying changes.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.recent = [command.key] + [key for key in self.recent if key != command.key]
        self.save()
        self.accept()
        QTimer.singleShot(0, command.callback)


def install_command_palette(window):
    action = QAction("Quick Commands…", window)
    action.setShortcut("Ctrl+P")

    def show():
        dialog = CommandPalette(window)
        dialog.exec()
        dialog.deleteLater()

    action.triggered.connect(show)
    window.addAction(action)
    return action
