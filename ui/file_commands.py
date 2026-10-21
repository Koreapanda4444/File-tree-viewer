from __future__ import annotations

from PySide6.QtCore import QModelIndex, QObject, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu

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
        for view in self.views:
            view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            view.customContextMenuRequested.connect(
                lambda point, target=view: self.show_context(target, point)
            )

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

    def open_selected(self):
        paths = self.page.selected_paths()
        if (
            len(paths) != 1
            or self.page.operation_is_running
            or self.page.search_is_running
        ):
            return
        path = paths[0]
        if path.is_dir() and not path.is_symlink():
            self.page.open_folder_index(self.page.tree.currentIndex())
        elif self.page._confirm_editor_transition():
            if path != self.page.current_preview_path:
                self.page._show_path(path)
            self.page.open_current_external()

    def copy_paths(self):
        paths = self.page.selected_paths()
        if paths:
            QApplication.clipboard().setText("\n".join(str(path) for path in paths))
            self.page.status_changed.emit(f"Copied {len(paths):,} path(s)")

    def inspect_selected(self):
        paths = self.page.selected_paths()
        if len(paths) == 1:
            self.page._update_details(paths[0])
            self.page.show_file_metadata()

    def show_context(self, view, point):
        index = view.indexAt(point)
        if index.isValid():
            if not view.selectionModel().isSelected(index):
                view.setCurrentIndex(index)
        else:
            view.selectionModel().clearSelection()
            view.setCurrentIndex(QModelIndex())
        if index.isValid() and not view.selectionModel().isSelected(index):
            return
        if not index.isValid() and view.currentIndex().isValid():
            return
        menu = self.build_menu()
        menu.exec(view.viewport().mapToGlobal(point))
        menu.deleteLater()

    def build_menu(self):
        page = self.page
        paths = page.selected_paths()
        mutable = page._operable_selected_paths()
        busy = page.operation_is_running or page.search_is_running
        ready = not busy and page.model.root_path is not None
        menu = QMenu(page)

        def add(name, callback, enabled):
            action = menu.addAction(name)
            action.setEnabled(enabled)
            action.triggered.connect(callback)
            return action

        add("Open", self.open_selected, ready and len(paths) == 1)
        add("Inspect Metadata", self.inspect_selected, not busy and len(paths) == 1)
        add("Copy Path", self.copy_paths, bool(paths))
        menu.addSeparator()
        add("Copy", lambda: self.copy(False), ready and bool(mutable))
        add("Cut", lambda: self.copy(True), ready and bool(mutable))
        target = (
            paths[0]
            if len(paths) == 1 and paths[0].is_dir() and not paths[0].is_symlink()
            else page.model.root_path
        )
        add(
            "Paste Into Folder" if target != page.model.root_path else "Paste",
            lambda: self.paste(target),
            ready and self.actions["Paste"].isEnabled(),
        )
        add("Copy To…", lambda: page.transfer_selected("copy"), ready and bool(mutable))
        add("Move To…", lambda: page.transfer_selected("move"), ready and bool(mutable))
        add("Rename", page.rename_selected, ready and len(mutable) == 1)
        add("Delete", page.delete_selected, ready and bool(mutable))
        menu.addSeparator()
        add("New File", page.create_new_file, ready)
        add("New Folder", page.create_new_folder, ready)
        add("Refresh", page.refresh_root, ready)
        return menu
