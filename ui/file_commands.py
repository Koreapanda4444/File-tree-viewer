from __future__ import annotations

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication

from fs_ops import require_unchanged
from fs_paths import normalized_path
from real.file_clipboard import file_mime, read_file_mime


class FileCommands(QObject):
    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.pending_cut_token = None
        self.actions = {}
        for name, shortcut, callback in (
            ("Copy", "Ctrl+C", lambda: self.copy(False)),
            ("Cut", "Ctrl+X", lambda: self.copy(True)),
            ("Paste", "Ctrl+V", self.paste),
        ):
            self.bind(name, shortcut, callback)
        QApplication.clipboard().dataChanged.connect(self.clipboard_changed)
        page.transfer_completed.connect(self.completed)
        self.clipboard_changed()

    @property
    def views(self):
        return self.page.tree, self.page.folder_view, self.page.folder_icons

    def bind(self, name, shortcut, callback):
        action = QAction(name, self)
        action.setShortcut(shortcut)
        action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        action.triggered.connect(callback)
        for view in self.views:
            view.addAction(action)
        self.actions[name] = action
        return action

    def copy(self, cut=False):
        if self.page.operation_is_running or self.page.search_is_running:
            return
        paths = self.page._operable_selected_paths()
        if not paths:
            return
        try:
            data = file_mime(paths, cut=cut)
        except (OSError, ValueError) as error:
            self.page._show_operation_error("Cannot copy file references", error)
            return
        QApplication.clipboard().setMimeData(data)
        self.page.status_changed.emit(
            f"{'Cut' if cut else 'Copied'} {len(paths):,} file reference(s); paste to transfer"
        )

    def paste(self, destination=None):
        if isinstance(destination, bool):
            destination = None
        try:
            paths, cut, payload = read_file_mime(QApplication.clipboard().mimeData())
            if payload:
                for path, item in zip(paths, payload["entries"], strict=True):
                    require_unchanged(path, item["signature"])
        except (OSError, ValueError) as error:
            self.page._show_operation_error("Cannot paste files", error)
            return
        target = destination or self.page.model.root_path
        token = payload["token"] if payload and cut else None
        self.transfer(paths, "move" if cut else "copy", target, cut_token=token)

    def transfer(self, paths, action, destination, *, cut_token=None):
        page = self.page
        if (
            action not in ("copy", "move")
            or destination is None
            or page.operation_is_running
            or page.search_is_running
        ):
            return False
        if not page._confirm_editor_transition():
            return False
        self.pending_cut_token = cut_token
        page._start_file_operation(action, sources=paths, destination=destination)
        return True

    def completed(self, action, changes):
        token, self.pending_cut_token = self.pending_cut_token, None
        if action != "move" or not token or not changes:
            return
        try:
            _paths, cut, payload = read_file_mime(QApplication.clipboard().mimeData())
        except ValueError:
            return
        if not cut or payload is None or payload["token"] != token:
            return
        moved = {
            normalized_path(change.source)
            for change in changes
            if change.source is not None
        }
        remaining = [
            item
            for item in payload["entries"]
            if normalized_path(item["path"]) not in moved
        ]
        if remaining:
            QApplication.clipboard().setMimeData(
                file_mime([], cut=True, entries=remaining)
            )
        else:
            QApplication.clipboard().clear()

    def clipboard_changed(self):
        try:
            paths, cut, _payload = read_file_mime(QApplication.clipboard().mimeData())
        except ValueError:
            paths, cut = [], False
        self.page.browse_model.cut_paths = (
            {normalized_path(path) for path in paths} if cut else set()
        )
        for view in self.views:
            view.viewport().update()
        self.actions["Paste"].setEnabled(bool(paths))
