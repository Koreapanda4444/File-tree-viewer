from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QAbstractItemModel,
    QModelIndex,
    QObject,
    Qt,
    QThreadPool,
    Signal,
    Slot,
)

from real.directory_index import DirectoryIndexJob
from real.file_clipboard import model_file_mime
from real.icons import FileIconCache

PAGE_SIZE = 256
INVALID_INDEX = QModelIndex()


@dataclass(slots=True)
class FileNode:
    path: Path
    name: str
    is_directory: bool
    parent: FileNode | None
    is_link: bool = False
    row: int = 0
    children: list[FileNode] = field(default_factory=list)
    entries: list | None = None
    job: DirectoryIndexJob | None = None
    fully_loaded: bool = False
    details_loaded: bool = False
    size: int | None = None
    modified: float | None = None
    load_generation: int = 0
    fetch_pending: bool = False

    def load_details(self) -> None:
        if self.details_loaded:
            return

        try:
            details = self.path.stat(follow_symlinks=False)
            self.size = None if self.is_directory else details.st_size
            self.modified = details.st_mtime
        except OSError:
            self.size = None
            self.modified = None
        self.details_loaded = True

    def close_iterator(self) -> None:
        if self.job is not None:
            self.job.cancelled.set()
            self.job = None
        self.entries = None


class FileTreeModel(QAbstractItemModel):
    directory_error = Signal(str)
    directory_loaded = Signal(object)
    headers = ("Name", "Type", "Size", "Modified")

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        page_size: int = PAGE_SIZE,
    ) -> None:
        super().__init__(parent)
        if page_size <= 0:
            raise ValueError("Tree page size must be positive")
        self.page_size = page_size
        self.show_hidden = False
        self.root: FileNode | None = None
        self.icons = FileIconCache()
        self.index_pool = QThreadPool(self)
        self.index_pool.setMaxThreadCount(2)
        self.sort_options = (0, False, "None", True)

    @property
    def root_path(self) -> Path | None:
        return self.root.path if self.root is not None else None

    def set_root(self, path: Path) -> None:
        root_path = path.expanduser().resolve(strict=True)
        if not root_path.is_dir():
            raise NotADirectoryError(root_path)

        with os.scandir(root_path):
            pass
        self.beginResetModel()
        self._close_current_tree()
        self.icons.clear()
        self.root = FileNode(
            path=root_path,
            name=str(root_path),
            is_directory=True,
            parent=None,
        )
        self.endResetModel()

    def clear(self) -> None:
        self.beginResetModel()
        self._close_current_tree()
        self.root = None
        self.icons.clear()
        self.endResetModel()

    def close(self) -> None:
        self._close_current_tree()

    def refresh(self) -> None:
        if self.root is not None:
            self.set_root(self.root.path)

    def index_for_path(self, path: Path) -> QModelIndex:
        if self.root is None:
            return QModelIndex()
        stack = [self.root]
        while stack:
            node = stack.pop()
            if node.path == path:
                return self.createIndex(node.row, 0, node)
            if node.is_directory and path.is_relative_to(node.path):
                stack.extend(node.children)
        return QModelIndex()

    def refresh_directory(self, path: Path) -> None:
        index = self.index_for_path(path)
        node = self.node_from_index(index)
        if node is None or not node.is_directory:
            return
        self.release_children(index)
        self.fetchMore(index)

    def set_show_hidden(self, enabled: bool) -> None:
        if self.show_hidden == enabled:
            return
        self.show_hidden = enabled
        self.refresh()

    def node_from_index(self, index: QModelIndex) -> FileNode | None:
        if not index.isValid() or index.model() is not self:
            return None
        node = index.internalPointer()
        return node if isinstance(node, FileNode) else None

    def path_from_index(self, index: QModelIndex) -> Path | None:
        node = self.node_from_index(index)
        return node.path if node is not None else None

    def release_children(self, parent: QModelIndex) -> None:
        node = self.node_from_index(parent)
        if node is None or not node.is_directory:
            return

        node.close_iterator()
        node.load_generation += 1
        node.fetch_pending = False
        if node.children:
            self.beginRemoveRows(parent, 0, len(node.children) - 1)
            for child in node.children:
                self._close_subtree(child)
            node.children.clear()
            self.endRemoveRows()
        node.fully_loaded = False

    def rowCount(self, parent: QModelIndex = INVALID_INDEX) -> int:
        if parent.column() > 0:
            return 0
        if not parent.isValid():
            return 1 if self.root is not None else 0

        node = self.node_from_index(parent)
        if node is None or not node.is_directory:
            return 0
        return len(node.children)

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

        if parent.isValid() and parent.column() > 0:
            return QModelIndex()
        if not parent.isValid():
            if row == 0 and self.root is not None:
                return self.createIndex(0, column, self.root)
            return QModelIndex()

        parent_node = self.node_from_index(parent)
        if parent_node is None or row >= len(parent_node.children):
            return QModelIndex()
        return self.createIndex(row, column, parent_node.children[row])

    def parent(self, index: QModelIndex) -> QModelIndex:
        node = self.node_from_index(index)
        if node is None or node.parent is None:
            return QModelIndex()

        parent_node = node.parent
        return self.createIndex(parent_node.row, 0, parent_node)

    def hasChildren(self, parent: QModelIndex = INVALID_INDEX) -> bool:
        if parent.column() > 0:
            return False
        if not parent.isValid():
            return self.root is not None

        node = self.node_from_index(parent)
        if node is None or not node.is_directory:
            return False
        return bool(node.children) or not node.fully_loaded

    def canFetchMore(self, parent: QModelIndex) -> bool:
        node = self.node_from_index(parent)
        return bool(
            parent.column() == 0
            and node
            and node.is_directory
            and not node.fully_loaded
            and not node.fetch_pending
        )

    def fetchMore(self, parent: QModelIndex) -> None:
        node = self.node_from_index(parent)
        if (
            parent.column() > 0
            or node is None
            or not node.is_directory
            or node.fully_loaded
            or node.fetch_pending
        ):
            return

        if node.entries is None:
            node.fetch_pending = True
            job = DirectoryIndexJob(
                node, node.load_generation, self.show_hidden, self.sort_options
            )
            node.job = job
            job.signals.finished.connect(self._index_finished)
            self.index_pool.start(job)
            return

        self._publish_page(node, parent)

    @Slot(object, int, object, str)
    def _index_finished(self, node, generation, entries, error):
        if node.load_generation != generation or node.job is None:
            return
        index = self.index_for_path(node.path)
        if self.node_from_index(index) is not node:
            return
        node.job = None
        node.fetch_pending = False
        if entries is None:
            return
        node.entries = entries
        if error:
            self.directory_error.emit(f"Cannot read {node.path}: {error}")
        self._publish_page(node, index)
        self.directory_loaded.emit(node.path)

    def _publish_page(self, node, parent):
        first = len(node.children)
        entries = node.entries[first : first + self.page_size]
        if entries:
            self.beginInsertRows(parent, first, first + len(entries) - 1)
            for row, entry in enumerate(entries, first):
                node.children.append(
                    FileNode(
                        path=entry.path,
                        name=entry.name,
                        is_directory=entry.is_directory,
                        parent=node,
                        is_link=entry.is_link,
                        row=row,
                        size=entry.size,
                        modified=entry.modified,
                        details_loaded=entry.details_loaded,
                    )
                )
            self.endInsertRows()
        node.fully_loaded = len(node.children) == len(node.entries)
        if node.fully_loaded:
            node.entries = []

    def set_sort_options(self, column, descending, grouping, folders_first):
        options = (column, descending, grouping, folders_first)
        if options != self.sort_options:
            self.sort_options = options
            self.refresh()

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        node = self.node_from_index(index)
        if node is None:
            return None

        if role == Qt.ItemDataRole.DisplayRole:
            if index.column() == 0:
                return node.name
            if index.column() == 1:
                return _file_type(node)

            node.load_details()
            if index.column() == 2:
                return _format_size(node.size)
            if index.column() == 3:
                return _format_time(node.modified)

        if role == Qt.ItemDataRole.DecorationRole and index.column() == 0:
            return self.icons.icon(
                node.path, is_directory=node.is_directory, is_link=node.is_link
            )
        if role == Qt.ItemDataRole.ToolTipRole:
            return str(node.path)
        if role == Qt.ItemDataRole.UserRole:
            return str(node.path)
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
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if self.node_from_index(index) is not self.root:
            flags |= Qt.ItemFlag.ItemIsDragEnabled
        return flags

    def mimeTypes(self):
        return ["text/uri-list"]

    def supportedDragActions(self):
        return Qt.DropAction.CopyAction | Qt.DropAction.MoveAction

    def mimeData(self, indexes):
        return model_file_mime(
            self, indexes, excluded=self.root_path, on_error=self.directory_error.emit
        )

    def _close_current_tree(self) -> None:
        if self.root is not None:
            self._close_subtree(self.root)

    @staticmethod
    def _close_subtree(root: FileNode) -> None:
        stack = [root]
        while stack:
            node = stack.pop()
            node.close_iterator()
            stack.extend(node.children)


def _file_type(node: FileNode) -> str:
    if node.is_link:
        return "Symbolic Link"
    if node.is_directory:
        return "Folder"
    suffix = node.path.suffix
    return f"{suffix[1:].upper()} File" if suffix else "File"


def _format_size(size: int | None) -> str:
    if size is None:
        return "-"

    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1_024 or unit == "TB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1_024
    return "-"


def _format_time(timestamp: float | None) -> str:
    if timestamp is None:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(timestamp))
