from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal


class DirectoryMonitor(QObject):
    changed = Signal(object)
    warning = Signal(str)
    limit = 512

    def __init__(self, model):
        super().__init__(model)
        self.model = model
        self.watcher = QFileSystemWatcher(self)
        self.pending: set[Path] = set()
        self.failed: set[Path] = set()
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(300)
        self.debounce.timeout.connect(self.flush)
        self.fallback = QTimer(self)
        self.fallback.setInterval(30_000)
        self.fallback.timeout.connect(self.poll_unwatched)
        self.watcher.directoryChanged.connect(self.queue)
        model.modelAboutToBeReset.connect(self.clear)
        model.modelReset.connect(self.sync)
        model.rowsInserted.connect(self.sync)
        model.rowsRemoved.connect(self.sync)

    def directories(self):
        if self.model.root is None:
            return set()
        result, stack = {self.model.root.path}, [self.model.root]
        while stack:
            node = stack.pop()
            if node.is_directory and (node.children or node.fully_loaded):
                result.add(node.path)
                stack.extend(node.children)
        return result

    def sync(self, *args):
        desired = self.directories()
        active = set(map(Path, self.watcher.directories()))
        removed = active - desired
        if removed:
            self.watcher.removePaths([str(path) for path in removed])
        active -= removed
        available = sorted(
            desired - active, key=lambda path: (len(path.parts), str(path))
        )
        additions = available[: max(0, self.limit - len(active))]
        failures = (
            set(map(Path, self.watcher.addPaths(list(map(str, additions)))))
            if additions
            else set()
        )
        previous = self.failed
        self.failed = failures | set(available[len(additions) :])
        if failures - previous:
            self.warning.emit(
                "Some directories cannot be watched; periodic refresh is enabled"
            )
        if desired:
            self.fallback.start()
        else:
            self.fallback.stop()

    def queue(self, path):
        self.pending.add(Path(path))
        self.debounce.start()

    def flush(self):
        paths, self.pending = self.pending, set()
        if paths:
            self.changed.emit(paths)
        self.sync()

    def poll_unwatched(self):
        self.sync()
        for path in self.failed:
            self.queue(path)

    def clear(self):
        self.debounce.stop()
        self.fallback.stop()
        self.pending.clear()
        self.failed.clear()
        paths = self.watcher.directories()
        if paths:
            self.watcher.removePaths(paths)
