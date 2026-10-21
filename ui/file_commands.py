from __future__ import annotations

from PySide6.QtCore import QModelIndex, QObject, Qt, QTimer
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
        self.has_files = False
        self.selection_timer = QTimer(self)
        self.selection_timer.setInterval(5)
        self.selection_timer.timeout.connect(self.continue_select_all)
        page.model.modelAboutToBeReset.connect(self.stop)
        for name, shortcut, callback in (
            ("Copy", "Ctrl+C", lambda: self.copy(False)),
            ("Cut", "Ctrl+X", lambda: self.copy(True)),
            ("Paste", "Ctrl+V", self.paste),
        ):
            self.bind(name, shortcut, callback)
        for name, shortcut, callback in (
            ("Rename", "F2", lambda: page.rename_selected()),
            ("Delete", "Delete", lambda: page.delete_selected()),
            ("Select All", "Ctrl+A", self.select_all),
            ("Search", "Ctrl+F", self.focus_search),
            ("Refresh", "F5", lambda: page.refresh_root()),
            ("Undo", "Ctrl+Z", lambda: page.undo_last_operation()),
            ("Open", "Return", self.open_selected),
        ):
            self.bind(
                name,
                shortcut,
                lambda checked=False, command=callback: self.dispatch(command),
            )
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
        self.has_files = bool(paths)
        self.update_actions()

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

    def dispatch(self, callback):
        if (
            not self.page.operation_is_running
            and not self.page.search_is_running
            and not self.page.close_requested
        ):
            callback()

    def update_actions(self):
        page = self.page
        ready = (
            not page.operation_is_running
            and not page.search_is_running
            and not page.close_requested
        )
        selected = page._operable_selected_paths()
        states = {
            "Copy": ready and bool(selected),
            "Cut": ready and bool(selected),
            "Paste": ready and page.model.root_path is not None and self.has_files,
            "Rename": ready and len(selected) == 1,
            "Delete": ready and bool(selected),
            "Select All": ready and page.model.root_path is not None,
            "Search": ready and page.model.root_path is not None,
            "Refresh": ready and page.model.root_path is not None,
            "Undo": ready and bool(page.undo_stack),
            "Open": ready and len(page.selected_paths()) == 1,
        }
        for name, enabled in states.items():
            self.actions[name].setEnabled(enabled)

    def focus_search(self):
        self.page.search_input.setFocus()
        self.page.search_input.selectAll()

    def select_all(self):
        if not self.page._confirm_editor_transition():
            return
        if self.page.search_view_active:
            self.page.tree.selectAll()
            return
        if self.page.model.root_path is not None:
            self.page.status_changed.emit(
                "Loading the current folder before selecting all items…"
            )
            self.selection_timer.start()

    def continue_select_all(self):
        page = self.page
        if page.close_requested or page.operation_is_running or page.search_view_active:
            self.stop()
            return
        root = page.model.index(0, 0)
        node = page.model.node_from_index(root)
        if node is None:
            self.stop()
        elif page.model.canFetchMore(root):
            page.model.fetchMore(root)
        elif not node.fetch_pending:
            self.stop()
            page.folder_panel.active_view.selectAll()
            page.status_changed.emit(f"Selected {len(page.selected_paths()):,} item(s)")

    def stop(self):
        self.selection_timer.stop()
