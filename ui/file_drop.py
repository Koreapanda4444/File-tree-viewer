from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QAbstractItemView, QMenu

from fs_ops import require_unchanged
from real.file_clipboard import read_file_mime


class FileDropController(QObject):
    def __init__(self, page, commands):
        super().__init__(page)
        self.page, self.commands = page, commands
        self.views = commands.views
        self.targets = tuple((view.viewport(), view) for view in self.views)
        for view in self.views:
            view.setDragEnabled(True)
            view.setAcceptDrops(True)
            view.viewport().setAcceptDrops(True)
            view.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
            view.setDefaultDropAction(Qt.DropAction.CopyAction)
            view.viewport().installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() not in (
            QEvent.Type.DragEnter,
            QEvent.Type.DragMove,
            QEvent.Type.Drop,
        ):
            return super().eventFilter(watched, event)
        view = next(
            (view for viewport, view in self.targets if viewport is watched), None
        )
        if view is None:
            return super().eventFilter(watched, event)
        if (
            self.page.operation_is_running
            or self.page.search_is_running
            or not event.mimeData().hasUrls()
        ):
            event.ignore()
            return True
        if event.type() != QEvent.Type.Drop:
            if event.possibleActions() & Qt.DropAction.CopyAction:
                event.setDropAction(Qt.DropAction.CopyAction)
                event.accept()
            else:
                event.ignore()
            return True
        self.drop_files(view, event)
        return True

    def choose_action(self, view, event):
        modifiers = event.modifiers()
        if (
            modifiers & Qt.KeyboardModifier.ControlModifier
            and modifiers & Qt.KeyboardModifier.ShiftModifier
        ):
            return None
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            return "copy"
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            return (
                "move" if event.possibleActions() & Qt.DropAction.MoveAction else None
            )
        menu = QMenu(view)
        copy = menu.addAction("Copy Here")
        move = menu.addAction("Move Here")
        move.setEnabled(bool(event.possibleActions() & Qt.DropAction.MoveAction))
        menu.addSeparator()
        menu.addAction("Cancel")
        chosen = menu.exec(view.viewport().mapToGlobal(event.position().toPoint()))
        return "copy" if chosen is copy else "move" if chosen is move else None

    def drop_files(self, view, event):
        event.ignore()
        if not event.possibleActions() & Qt.DropAction.CopyAction:
            return
        try:
            paths, _cut, payload = read_file_mime(event.mimeData())
            if payload:
                for path, item in zip(paths, payload["entries"], strict=True):
                    require_unchanged(path, item["signature"])
            index = view.indexAt(event.position().toPoint())
            target = self.page._path_from_index(index)
            destination = (
                target
                if target is not None and target.is_dir() and not target.is_symlink()
                else self.page.model.root_path
            )
            action = self.choose_action(view, event)
            if action is not None and self.commands.transfer(
                paths, action, destination
            ):
                self.accept_worker_owned_transfer(event)
        except (OSError, ValueError) as error:
            self.page._show_operation_error("Cannot drop files", error)

    @staticmethod
    def accept_worker_owned_transfer(event):
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
