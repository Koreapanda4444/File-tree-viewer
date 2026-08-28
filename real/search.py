from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QAbstractItemModel,
    QFileInfo,
    QModelIndex,
    QObject,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtWidgets import QFileIconProvider

from real.tree import is_hidden, is_reparse_point

RESULT_BATCH_SIZE = 1_024
PROGRESS_INTERVAL = 2_048
CACHE_PAGE_SIZE = 256
MAX_CACHE_PAGES = 64
MAX_PENDING_BATCHES = 8
CATEGORY_INDEX_BASE = 1
RESULT_INDEX_BASE = 101
INVALID_INDEX = QModelIndex()


@dataclass(frozen=True, slots=True)
class SearchResult:
    path: str
    name: str
    is_directory: bool


class SearchWorker(QObject):
    batch_found = Signal(object)
    progress = Signal(int, int)
    failed = Signal(str)
    finished = Signal(int, int, int, bool)

    def __init__(self, root: Path, query: str, show_hidden: bool) -> None:
        super().__init__()
        self.root = root
        self.query = query.casefold()
        self.show_hidden = show_hidden
        self._cancelled = threading.Event()
        self._pending_batches = threading.Semaphore(MAX_PENDING_BATCHES)

    def cancel(self) -> None:
        self._cancelled.set()

    def batch_processed(self) -> None:
        self._pending_batches.release()

    def _emit_batch(self, batch: list[SearchResult]) -> bool:
        while not self._cancelled.is_set():
            if self._pending_batches.acquire(timeout=0.1):
                self.batch_found.emit(batch)
                return True
        return False

    @Slot()
    def run(self) -> None:
        batch: list[SearchResult] = []
        scanned = 0
        matched = 0
        skipped = 0
        active_directories: set[tuple[object, ...]] = set()
        iterators = []
        stopped = False

        def open_directory(directory: Path):
            nonlocal skipped
            try:
                details = directory.stat(follow_symlinks=False)
                identity: tuple[object, ...]
                if details.st_ino:
                    identity = ("inode", details.st_dev, details.st_ino)
                else:
                    identity = (
                        "path",
                        os.path.normcase(os.path.abspath(directory)),
                    )
                if identity in active_directories:
                    return None
                iterator = os.scandir(directory)
                active_directories.add(identity)
                return iterator, identity
            except OSError:
                skipped += 1
                return None

        try:
            root_iterator = open_directory(self.root)
            if root_iterator is not None:
                iterators.append(root_iterator)

            while iterators and not self._cancelled.is_set() and not stopped:
                try:
                    entry = next(iterators[-1][0])
                except StopIteration:
                    iterator, identity = iterators.pop()
                    iterator.close()
                    active_directories.discard(identity)
                    continue
                except OSError:
                    skipped += 1
                    iterator, identity = iterators.pop()
                    iterator.close()
                    active_directories.discard(identity)
                    continue

                scanned += 1
                if not self.show_hidden and is_hidden(entry):
                    continue

                try:
                    is_directory = entry.is_dir(follow_symlinks=False)
                except OSError:
                    skipped += 1
                    continue

                if self.query in entry.name.casefold():
                    batch.append(
                        SearchResult(
                            path=entry.path,
                            name=entry.name,
                            is_directory=is_directory,
                        )
                    )
                    matched += 1

                if is_directory and not is_reparse_point(entry):
                    child_iterator = open_directory(Path(entry.path))
                    if child_iterator is not None:
                        iterators.append(child_iterator)

                if len(batch) >= RESULT_BATCH_SIZE:
                    if self._emit_batch(batch):
                        batch = []
                    else:
                        matched -= len(batch)
                        batch = []
                        stopped = True

                if scanned % PROGRESS_INTERVAL == 0:
                    self.progress.emit(scanned, matched)
        except (OSError, RuntimeError, ValueError) as error:
            self.failed.emit(str(error))
        finally:
            while iterators:
                iterator, identity = iterators.pop()
                iterator.close()
                active_directories.discard(identity)

        if batch and not self._emit_batch(batch):
            matched -= len(batch)
        self.progress.emit(scanned, matched)
        self.finished.emit(
            scanned,
            matched,
            skipped,
            self._cancelled.is_set(),
        )


class SearchResultsModel(QAbstractItemModel):
    headers = ("Name", "Path", "Type")
    category_names = ("Folders", "Files")

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._temporary_directory = tempfile.TemporaryDirectory(
            prefix="file-tree-viewer-search-"
        )
        database_path = Path(self._temporary_directory.name) / "results.sqlite3"
        self._database = sqlite3.connect(database_path)
        self._database.execute("PRAGMA journal_mode=OFF")
        self._database.execute("PRAGMA synchronous=OFF")
        self._database.execute("PRAGMA locking_mode=EXCLUSIVE")
        self._database.execute("PRAGMA cache_size=-8192")
        self._database.execute(
            "CREATE TABLE results ("
            "category INTEGER NOT NULL, "
            "position INTEGER NOT NULL, "
            "name TEXT NOT NULL, "
            "path TEXT NOT NULL, "
            "PRIMARY KEY(category, position)) WITHOUT ROWID"
        )
        self._counts = [0, 0]
        self._cache: OrderedDict[tuple[int, int], list[tuple[str, str]]] = OrderedDict()
        self._closed = False
        self.icons = QFileIconProvider()

    @property
    def total_count(self) -> int:
        return sum(self._counts)

    @Slot(object)
    def append_results(self, results: list[SearchResult]) -> None:
        grouped = ([], [])
        for result in results:
            category = 0 if result.is_directory else 1
            grouped[category].append((result.name, result.path))

        for category, rows in enumerate(grouped):
            if not rows:
                continue
            parent = self.index(category, 0)
            first = self._counts[category]
            last = first + len(rows) - 1
            first_changed_page = first // CACHE_PAGE_SIZE
            for key in tuple(self._cache):
                if key[0] == category and key[1] >= first_changed_page:
                    del self._cache[key]
            database_rows = [
                (category, first + offset, name, path)
                for offset, (name, path) in enumerate(rows)
            ]
            self._database.executemany(
                "INSERT INTO results(category, position, name, path) "
                "VALUES (?, ?, ?, ?)",
                database_rows,
            )
            self._database.commit()
            self.beginInsertRows(parent, first, last)
            self._counts[category] += len(rows)
            self.endInsertRows()
            self.dataChanged.emit(parent, parent, [Qt.ItemDataRole.DisplayRole])

    def clear(self) -> None:
        self.beginResetModel()
        self._database.execute("DELETE FROM results")
        self._database.commit()
        self._counts = [0, 0]
        self._cache.clear()
        self.endResetModel()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._database.close()
        self._temporary_directory.cleanup()

    def rowCount(self, parent: QModelIndex = INVALID_INDEX) -> int:
        if parent.column() > 0:
            return 0
        if not parent.isValid():
            return len(self.category_names)

        category = self._category_from_parent(parent)
        return self._counts[category] if category is not None else 0

    def columnCount(self, parent: QModelIndex = INVALID_INDEX) -> int:
        return len(self.headers)

    def index(
        self,
        row: int,
        column: int,
        parent: QModelIndex = INVALID_INDEX,
    ) -> QModelIndex:
        if row < 0 or column < 0 or column >= len(self.headers):
            return QModelIndex()

        if not parent.isValid():
            if row < len(self.category_names):
                return self.createIndex(row, column, CATEGORY_INDEX_BASE + row)
            return QModelIndex()

        category = self._category_from_parent(parent)
        if category is None or row >= self._counts[category]:
            return QModelIndex()
        return self.createIndex(row, column, RESULT_INDEX_BASE + category)

    def parent(self, index: QModelIndex) -> QModelIndex:
        if not index.isValid() or index.internalId() < RESULT_INDEX_BASE:
            return QModelIndex()

        category = index.internalId() - RESULT_INDEX_BASE
        if category not in (0, 1):
            return QModelIndex()
        return self.createIndex(category, 0, CATEGORY_INDEX_BASE + category)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None

        internal_id = index.internalId()
        if internal_id < RESULT_INDEX_BASE:
            category = internal_id - CATEGORY_INDEX_BASE
            if role == Qt.ItemDataRole.DisplayRole and index.column() == 0:
                return f"{self.category_names[category]} ({self._counts[category]})"
            return None

        category = internal_id - RESULT_INDEX_BASE
        result = self._result_at(category, index.row())
        if result is None:
            return None
        name, path = result

        if role == Qt.ItemDataRole.DisplayRole:
            if index.column() == 0:
                return name
            if index.column() == 1:
                return path
            if index.column() == 2:
                return "Folder" if category == 0 else "File"
        if role == Qt.ItemDataRole.DecorationRole and index.column() == 0:
            return self.icons.icon(QFileInfo(path))
        if role in (Qt.ItemDataRole.ToolTipRole, Qt.ItemDataRole.UserRole):
            return path
        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
            and 0 <= section < len(self.headers)
        ):
            return self.headers[section]
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        if index.internalId() < RESULT_INDEX_BASE:
            return Qt.ItemFlag.ItemIsEnabled
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def path_from_index(self, index: QModelIndex) -> Path | None:
        if not index.isValid() or index.internalId() < RESULT_INDEX_BASE:
            return None
        result = self._result_at(index.internalId() - RESULT_INDEX_BASE, index.row())
        return Path(result[1]) if result is not None else None

    @staticmethod
    def _category_from_parent(parent: QModelIndex) -> int | None:
        internal_id = parent.internalId()
        if CATEGORY_INDEX_BASE <= internal_id < RESULT_INDEX_BASE:
            category = internal_id - CATEGORY_INDEX_BASE
            return category if category in (0, 1) else None
        return None

    def _result_at(self, category: int, row: int) -> tuple[str, str] | None:
        page = row // CACHE_PAGE_SIZE
        key = (category, page)
        results = self._cache.get(key)
        if results is None:
            results = list(
                self._database.execute(
                    "SELECT name, path FROM results "
                    "WHERE category = ? AND position >= ? AND position < ? "
                    "ORDER BY position",
                    (
                        category,
                        page * CACHE_PAGE_SIZE,
                        (page + 1) * CACHE_PAGE_SIZE,
                    ),
                )
            )
            self._cache[key] = results
            while len(self._cache) > MAX_CACHE_PAGES:
                self._cache.popitem(last=False)
        else:
            self._cache.move_to_end(key)

        offset = row % CACHE_PAGE_SIZE
        return results[offset] if offset < len(results) else None
