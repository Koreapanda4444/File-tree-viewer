from __future__ import annotations

from PySide6.QtCore import QModelIndex, QObject, QPersistentModelIndex, QTimer, Signal


class FileRevealer(QObject):
    found = Signal(object)
    failed = Signal(str)

    def __init__(self, model):
        super().__init__(model)
        self.model = model
        self.path = None
        self.row = 0
        self.root = QPersistentModelIndex()
        self.timer = QTimer(self)
        self.timer.setInterval(1)
        self.timer.timeout.connect(self.step)
        model.modelAboutToBeReset.connect(self.stop)

    def start(self, path):
        self.stop()
        self.path, self.row = path, 0
        self.root = QPersistentModelIndex(self.model.index(0, 0))
        self.timer.start()

    def stop(self):
        self.timer.stop()
        self.path = None

    def step(self):
        if self.path is None or not self.root.isValid():
            self.stop()
            return
        root = QModelIndex(self.root)
        last = min(self.model.rowCount(root), self.row + 256)
        while self.row < last:
            index = self.model.index(self.row, 0, root)
            self.row += 1
            if self.model.path_from_index(index) == self.path:
                self.stop()
                self.found.emit(index)
                return
        if self.row < self.model.rowCount(root):
            return
        if self.model.canFetchMore(root):
            self.model.fetchMore(root)
        elif not self.model.node_from_index(root).fetch_pending:
            path = self.path
            self.stop()
            self.failed.emit(f"Item is unavailable or hidden: {path}")
