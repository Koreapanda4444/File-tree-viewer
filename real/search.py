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
    QModelIndex,
    QObject,
    Qt,
    Signal,
    Slot,
)

from content_search import find_file_content
from file_content import ContentCancelled
from fs_paths import is_hidden, is_reparse_point
from real.file_clipboard import model_file_mime
from real.icons import FileIconCache
from search_matching import NameMatcher, SearchPatternError
from search_options import SearchOptions
from ui.tasks import CancellableWorker

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
    line: int = 0
    column: int = 0
    snippet: str = ""


class SearchWorker(CancellableWorker):
    batch_found = Signal(object)
    progress = Signal(int, int)
    failed = Signal(str)
    finished = Signal(int, int, int, bool)

    def __init__(
        self,
        root: Path,
        query: str,
        show_hidden: bool,
        options: SearchOptions | None = None,
    ) -> None:
        super().__init__()
        self.root = root
        self.query = query
        self.show_hidden = show_hidden
        self.options = options or SearchOptions()
        self._pending_batches = threading.Semaphore(MAX_PENDING_BATCHES)

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

        def open_directory(directory: Path, details=None):
            nonlocal skipped
            try:
                if details is None:
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
            matcher = NameMatcher(
                self.query, self.options.name_mode, self.options.case_sensitive
            )
            content_matcher = (
                NameMatcher(
                    self.options.content_query,
                    case_sensitive=self.options.case_sensitive,
                )
                if self.options.content_query
                else None
            )
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
                if entry.name.casefold() == ".file-tree-viewer":
                    continue
                if not self.show_hidden and is_hidden(entry):
                    continue

                try:
                    is_directory = entry.is_dir(follow_symlinks=False)
                except OSError:
                    skipped += 1
                    continue

                try:
                    matches = matcher.matches(entry.name) and self.options.matches(
                        entry.name, is_directory, entry.stat(follow_symlinks=False)
                    )
                except OSError:
                    skipped += 1
                    matches = False
                if matches:
                    if content_matcher is None:
                        batch.append(SearchResult(entry.path, entry.name, is_directory))
                        matched += 1
                    elif not is_directory and not is_reparse_point(entry):
                        try:
                            content_matches = find_file_content(
                                Path(entry.path),
                                content_matcher,
                                encoding=self.options.content_encoding,
                                cancelled=self._cancelled,
                            )
                        except ContentCancelled:
                            break
                        except SearchPatternError:
                            raise
                        except (OSError, UnicodeError, ValueError):
                            skipped += 1
                        else:
                            for match in content_matches:
                                batch.append(
                                    SearchResult(
                                        entry.path,
                                        entry.name,
                                        False,
                                        match.line,
                                        match.column,
                                        match.snippet,
                                    )
                                )
                                matched += 1

                if (
                    self.options.scope != "current"
                    and is_directory
                    and not is_reparse_point(entry)
                ):
                    try:
                        child_iterator = open_directory(
                            Path(entry.path), entry.stat(follow_symlinks=False)
                        )
                    except OSError:
                        skipped += 1
                        child_iterator = None
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
    drag_error = Signal(str)
    headers = ("Name", "Path", "Type", "Line:Column", "Matching Text")
    category_names = ("Folders", "Files")

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._temporary_directory = tempfile.TemporaryDirectory(
            prefix="file-tree-viewer-search-"
        )
        self.database_path = Path(self._temporary_directory.name) / "results.sqlite3"
        self._database = sqlite3.connect(self.database_path)
        self._database.execute("PRAGMA journal_mode=WAL")
        self._database.execute("PRAGMA synchronous=OFF")
        self._database.execute("PRAGMA locking_mode=NORMAL")
        self._database.execute("PRAGMA cache_size=-8192")
        self._database.execute(
            "CREATE TABLE results ("
            "category INTEGER NOT NULL, "
            "position INTEGER NOT NULL, "
            "name TEXT NOT NULL, "
            "path TEXT NOT NULL, "
            "line INTEGER NOT NULL, column_number INTEGER NOT NULL, snippet TEXT NOT NULL, "
            "PRIMARY KEY(category, position)) WITHOUT ROWID"
        )
        self._counts = [0, 0]
        self._cache: OrderedDict[tuple[int, int], list[tuple]] = OrderedDict()
        self._closed = False
        self.icons = FileIconCache()

    @property
    def total_count(self) -> int:
        return sum(self._counts)

    @Slot(object)
    def append_results(self, results: list[SearchResult]) -> None:
        grouped = ([], [])
        for result in results:
            category = 0 if result.is_directory else 1
            grouped[category].append(
                (result.name, result.path, result.line, result.column, result.snippet)
            )

        with self._database:
            self._database.executemany(
                "INSERT INTO results(category, position, name, path, line, column_number, snippet) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    (category, self._counts[category] + offset, *values)
                    for category, rows in enumerate(grouped)
                    for offset, values in enumerate(rows)
                ),
            )
        for category, rows in enumerate(grouped):
            if not rows:
                continue
            parent = self.index(category, 0)
            first = self._counts[category]
            last = first + len(rows) - 1
            page = first // CACHE_PAGE_SIZE
            for key in tuple(self._cache):
                if key[0] == category and key[1] >= page:
                    del self._cache[key]
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
        self.icons.clear()
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
        name, path, line, column, snippet = result

        if role == Qt.ItemDataRole.DisplayRole:
            if index.column() == 0:
                return name
            if index.column() == 1:
                return path
            if index.column() == 2:
                return "Folder" if category == 0 else "File"
            if index.column() == 3:
                return f"{line}:{column}" if line else ""
            if index.column() == 4:
                return snippet
        if role == Qt.ItemDataRole.DecorationRole and index.column() == 0:
            return self.icons.icon(Path(path), is_directory=category == 0)
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
        return (
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsDragEnabled
        )

    def mimeTypes(self):
        return ["text/uri-list"]

    def supportedDragActions(self):
        return Qt.DropAction.CopyAction | Qt.DropAction.MoveAction

    def mimeData(self, indexes):
        return model_file_mime(self, indexes, on_error=self.drag_error.emit)

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

    def _result_at(self, category: int, row: int) -> tuple | None:
        if category not in (0, 1) or row < 0 or row >= self._counts[category]:
            return None
        page = row // CACHE_PAGE_SIZE
        key = (category, page)
        results = self._cache.get(key)
        if results is None:
            results = list(
                self._database.execute(
                    "SELECT name, path, line, column_number, snippet FROM results "
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
