from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QMenu

from fs_ops import require_unchanged
from real.file_clipboard import read_file_mime


class PlanDropController(QObject):
    def __init__(self, staging):
        super().__init__(staging)
        self.staging = staging
        page = staging.window.plan_page
        self.surfaces = [
            (view.viewport(), view) for view in (page.tree, page.plan_list)
        ]
        for viewport, view in self.surfaces:
            view.setAcceptDrops(True)
            viewport.setAcceptDrops(True)
            viewport.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() not in (
            QEvent.Type.DragEnter,
            QEvent.Type.DragMove,
            QEvent.Type.Drop,
        ):
            return False
        if not event.mimeData().hasUrls():
            return False
        event.ignore()
        if (
            self.staging.busy()
            or not event.possibleActions() & Qt.DropAction.CopyAction
        ):
            return True
        try:
            paths, _cut, payload = read_file_mime(event.mimeData())
            if not paths:
                return True
            if payload:
                for path, item in zip(paths, payload["entries"], strict=True):
                    require_unchanged(path, item["signature"])
        except (OSError, ValueError) as error:
            if event.type() == QEvent.Type.Drop:
                self.staging.show_error(error)
            return True
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        if event.type() == QEvent.Type.Drop:
            self.choose_operation(watched, event.position().toPoint(), paths)
        return True

    def choose_operation(self, viewport, point, paths):
        menu = QMenu(self.staging.window)
        for action in ("copy", "move", "rename", "delete"):
            item = menu.addAction(f"Stage {action.title()}…")
            item.setData(action)
            item.setEnabled(action != "rename" or len(paths) == 1)
        menu.addSeparator()
        menu.addAction("Cancel")
        selected = menu.exec(viewport.mapToGlobal(point))
        operation = selected.data() if selected is not None else None
        menu.deleteLater()
        if operation:
            QTimer.singleShot(0, lambda: self.staging.request(operation, paths))
