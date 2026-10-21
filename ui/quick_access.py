from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QDir, QStandardPaths, Qt, Signal
from PySide6.QtWidgets import (
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from fs_paths import normalized_path
from ui.tasks import CancellableWorker, prepare_worker_thread


class LocationsWorker(CancellableWorker):
    finished = Signal(object)

    def __init__(self, favorites):
        super().__init__()
        self.favorites = tuple(favorites)

    def run(self):
        values = [("Favorites", Path(p)) for p in self.favorites]
        for kind in (
            QStandardPaths.StandardLocation.HomeLocation,
            QStandardPaths.StandardLocation.DesktopLocation,
            QStandardPaths.StandardLocation.DocumentsLocation,
            QStandardPaths.StandardLocation.DownloadLocation,
            QStandardPaths.StandardLocation.PicturesLocation,
            QStandardPaths.StandardLocation.MusicLocation,
            QStandardPaths.StandardLocation.MoviesLocation,
        ):
            path = QStandardPaths.writableLocation(kind)
            if path:
                values.append(("User Folders", Path(path)))
        values.extend(
            ("Drives", Path(info.absoluteFilePath())) for info in QDir.drives()
        )
        results, seen = [], set()
        for category, path in values:
            if self._cancelled.is_set():
                break
            key = category, normalized_path(path)
            if key not in seen:
                seen.add(key)
                results.append((category, str(path), path.is_dir()))
        self.finished.emit(results)


class QuickAccess(QWidget):
    idle = Signal()
    status_changed = Signal(str)
    navigate = Signal(object)

    def __init__(self, store):
        super().__init__()
        self.store = store
        self.thread = self.worker = None
        self.generation = 0
        self.active_generation = 0
        self.pending = False
        self.closed = False
        self.current = None
        self.outcome = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.setMaximumHeight(240)
        self.list.itemActivated.connect(self.activate)
        self.list.itemClicked.connect(self.activate)
        self.refresh_button = QPushButton("Refresh Locations")
        self.refresh_button.clicked.connect(self.refresh)
        layout.addWidget(self.list)
        layout.addWidget(self.refresh_button)

    def refresh(self):
        if self.closed:
            return
        self.generation += 1
        self.pending = True
        if self.worker is not None:
            self.worker.cancel()
            return
        self.start()

    def start(self):
        if not self.pending or self.closed:
            return
        self.pending = False
        self.active_generation = self.generation
        self.outcome = None
        self.worker = LocationsWorker(self.store.favorites)
        self.worker.finished.connect(self.receive)
        self.thread = prepare_worker_thread(
            self, self.worker, on_finished=self.finished
        )
        self.thread.start()

    def receive(self, result):
        self.outcome = result

    def finished(self):
        self.thread = self.worker = None
        if (
            self.outcome is not None
            and not self.closed
            and self.active_generation == self.generation
        ):
            self.populate(self.outcome)
        self.start()
        if self.thread is None:
            self.idle.emit()

    def populate(self, items):
        self.list.clear()
        section = None
        for category, path, available in items:
            if category != section:
                title = QListWidgetItem(category)
                font = title.font()
                font.setBold(True)
                title.setFont(font)
                title.setFlags(Qt.ItemFlag.NoItemFlags)
                self.list.addItem(title)
                section = category
            item = QListWidgetItem(
                (Path(path).name or path) + (" (unavailable)" if not available else "")
            )
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            self.list.addItem(item)
        self.select_path(self.current)

    def activate(self, item):
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            self.navigate.emit(Path(path))

    def select_path(self, path):
        self.current = path
        self.list.clearSelection()
        if path is None:
            return
        for row in range(self.list.count()):
            item = self.list.item(row)
            value = item.data(Qt.ItemDataRole.UserRole)
            if value and normalized_path(value) == normalized_path(path):
                item.setSelected(True)
                self.list.scrollToItem(item)
                break

    def stop(self):
        self.closed = True
        self.pending = False
        if self.worker is not None:
            self.worker.cancel()
        return self.thread is None
