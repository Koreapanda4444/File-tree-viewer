from __future__ import annotations

import os
import stat
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QAbstractItemModel,
    QFileInfo,
    QModelIndex,
    QObject,
    Qt,
    Signal,
)
from PySide6.QtWidgets import QFileIconProvider

PAGE_SIZE = 256
SCAN_BATCH_SIZE = 1_024
INVALID_INDEX = QModelIndex()


@dataclass(slots=True)
class FileNode:
    path: Path
    name: str
    is_directory: bool
    parent: FileNode | None
    row: int = 0
    children: list[FileNode] = field(default_factory=list)
    iterator: Iterator[os.DirEntry[str]] | None = None
    fully_loaded: bool = False
    details_loaded: bool = False
    size: int | None = None
    modified: float | None = None

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
        iterator = self.iterator
        self.iterator = None
        close = getattr(iterator, "close", None)
        if callable(close):
            close()


class FileTreeModel(QAbstractItemModel):
    directory_error = Signal(str)
    headers = ("Name", "Type", "Size", "Modified")

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        page_size: int = PAGE_SIZE,
    ) -> None:
        super().__init__(parent)
        self.page_size = page_size
        self.show_hidden = False
        self.root: FileNode | None = None
        self.icons = QFileIconProvider()

    @property
    def root_path(self) -> Path | None:
        return self.root.path if self.root is not None else None

    def set_root(self, path: Path) -> None:
        root_path = path.expanduser().resolve(strict=True)
        if not root_path.is_dir():
            raise NotADirectoryError(root_path)

        self.beginResetModel()
        self._close_current_tree()
        self.root = FileNode(
            path=root_path,
            name=str(root_path),
            is_directory=True,
            parent=None,
        )
        self.endResetModel()

    def refresh(self) -> None:
        if self.root is not None:
            self.set_root(self.root.path)

    def set_show_hidden(self, enabled: bool) -> None:
        if self.show_hidden == enabled:
            return
        self.show_hidden = enabled
        self.refresh()

    def node_from_index(self, index: QModelIndex) -> FileNode | None:
        if not index.isValid():
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
        if not parent.isValid():
            return self.root is not None

        node = self.node_from_index(parent)
        if node is None or not node.is_directory:
            return False
        return bool(node.children) or not node.fully_loaded

    def canFetchMore(self, parent: QModelIndex) -> bool:
        node = self.node_from_index(parent)
        return bool(node and node.is_directory and not node.fully_loaded)

    def fetchMore(self, parent: QModelIndex) -> None:
        node = self.node_from_index(parent)
        if node is None or not node.is_directory or node.fully_loaded:
            return

        if node.iterator is None:
            try:
                node.iterator = os.scandir(node.path)
            except OSError as error:
                node.fully_loaded = True
                self.directory_error.emit(f"Cannot read {node.path}: {error}")
                return

        children: list[FileNode] = []
        scanned = 0
        assert node.iterator is not None

        while len(children) < self.page_size and scanned < SCAN_BATCH_SIZE:
            try:
                entry = next(node.iterator)
            except StopIteration:
                node.close_iterator()
                node.fully_loaded = True
                break
            except OSError as error:
                node.close_iterator()
                node.fully_loaded = True
                self.directory_error.emit(f"Cannot finish reading {node.path}: {error}")
                break

            scanned += 1
            if not self.show_hidden and _is_hidden(entry):
                continue

            try:
                is_directory = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue

            children.append(
                FileNode(
                    path=Path(entry.path),
                    name=entry.name,
                    is_directory=is_directory,
                    parent=node,
                )
            )

        if not children:
            return

        first = len(node.children)
        last = first + len(children) - 1
        self.beginInsertRows(parent, first, last)
        for row, child in enumerate(children, start=first):
            child.row = row
            node.children.append(child)
        self.endInsertRows()

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
            return self.icons.icon(QFileInfo(str(node.path)))
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
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

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


def _is_hidden(entry: os.DirEntry[str]) -> bool:
    if entry.name.startswith("."):
        return True
    if os.name != "nt":
        return False

    try:
        attributes = entry.stat(follow_symlinks=False).st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_HIDDEN)


def _file_type(node: FileNode) -> str:
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
