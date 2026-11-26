from __future__ import annotations

from pathlib import Path
from time import monotonic

from PySide6.QtCore import QObject, QTimer, Signal

from ui.i18n import tr


class FileRevealer(QObject):
    started = Signal()
    found = Signal(object)
    failed = Signal(str)
    max_items = 20_000
    max_depth = 128

    def __init__(self, model):
        super().__init__(model)
        self.model = model
        self.path = None
        self.timer = QTimer(self)
        self.timer.setInterval(5)
        self.timer.timeout.connect(self.step)
        model.modelAboutToBeReset.connect(self.stop)

    def start(self, path, *, parent=None):
        self.stop()
        anchor = (
            self.model.path_from_index(parent)
            if parent is not None
            else self.model.root_path
        )
        path = Path(path)
        if (
            anchor is None
            or not path.is_relative_to(anchor)
            or len(path.relative_to(anchor).parts) > self.max_depth
        ):
            self.failed.emit(
                tr(
                    "Path is outside the explorer root or exceeds the reveal depth limit"
                )
            )
            return False
        self.started.emit()
        if not self.model.reserve_budget(self, self.max_items):
            return False
        self.path, self.cursor = path, anchor
        self.parts = list(path.relative_to(anchor).parts)
        self.position = self.loaded = 0
        self.started_at = monotonic()
        self.timer.start()
        return True

    def stop(self):
        self.timer.stop()
        self.model.release_budget(self)
        self.path = None

    def fail(self, message):
        path = self.path
        self.stop()
        self.failed.emit(tr(message, path=path))

    def step(self):
        if self.path is None:
            return
        if monotonic() - self.started_at > 15 or self.model.load_budget == 0:
            self.fail("Path reveal reached its time or item limit: {path}")
            return
        for _ in range(16):
            source = self.model.index_for_path(self.cursor)
            node = self.model.node_from_index(source)
            if node is None:
                self.fail("Item is unavailable or hidden: {path}")
                return
            if self.position == len(self.parts):
                self.stop()
                self.found.emit(source)
                return
            if not node.is_directory or node.is_link:
                self.fail("Cannot follow a link or inaccessible parent: {path}")
                return
            target = self.cursor / self.parts[self.position]
            if self.model.index_for_path(target).isValid():
                self.cursor = target
                self.position += 1
                continue
            if node.fetch_pending:
                return
            if self.model.canFetchMore(source):
                before = len(node.children)
                self.model.fetchMore(source)
                self.loaded += max(1, len(node.children) - before)
                return
            self.fail("Item is unavailable or hidden: {path}")
            return
