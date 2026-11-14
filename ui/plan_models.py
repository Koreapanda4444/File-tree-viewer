from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from PySide6.QtCore import QAbstractItemModel, QMimeData, QModelIndex, Qt, Signal
from PySide6.QtWidgets import QApplication, QStyle, QWidget

from planning import ConflictPolicy, FilePlan, basic_dependencies
from snapshot import QUERY_PAGE_SIZE, FileSnapshot, SnapshotEntry
from ui.i18n import tr

INVALID_INDEX = QModelIndex()
PLAN_MIME_TYPE = "application/x-file-tree-viewer-plan-paths"


@dataclass(slots=True)
class SnapshotNode:
    entry: SnapshotEntry | None
    parent: SnapshotNode | None = None
    children: list[SnapshotNode] = field(default_factory=list)
    child_count: int | None = None
    row: int = 0

    @property
    def path(self) -> PurePosixPath | None:
        return self.entry.path if self.entry is not None else None

    @property
    def is_directory(self) -> bool:
        return self.entry is None or self.entry.is_directory


class SnapshotTreeModel(QAbstractItemModel):
    move_requested = Signal(object, object)
    headers = ("Name", "Type", "Size", "Modified")

    def __init__(self, snapshot: FileSnapshot, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.snapshot = snapshot
        self.root = SnapshotNode(None)

    def rowCount(self, parent: QModelIndex = INVALID_INDEX) -> int:
        if parent.column() > 0:
            return 0
        if not parent.isValid():
            return 1
        node = self.node_from_index(parent)
        return len(node.children) if node is not None and node.is_directory else 0

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
            return self.createIndex(0, column, self.root) if row == 0 else QModelIndex()
        parent_node = self.node_from_index(parent)
        if parent_node is None or row >= len(parent_node.children):
            return QModelIndex()
        return self.createIndex(row, column, parent_node.children[row])

    def parent(self, index: QModelIndex) -> QModelIndex:
        node = self.node_from_index(index)
        if node is None or node.parent is None:
            return QModelIndex()
        parent = node.parent
        if parent is self.root:
            return self.createIndex(0, 0, parent)
        if parent.parent is None:
            return QModelIndex()
        return self.createIndex(parent.row, 0, parent)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        node = self.node_from_index(index)
        if node is None:
            return None
        entry = node.entry

        if role == Qt.ItemDataRole.DisplayRole:
            if entry is None:
                return self._root_data(index.column())
            if index.column() == 0:
                return entry.name
            if index.column() == 1:
                return snapshot_type(entry)
            if index.column() == 2:
                return "-" if entry.is_directory else format_size(entry.size)
            if index.column() == 3:
                return format_modified(entry.modified_ns)

        if role == Qt.ItemDataRole.DecorationRole and index.column() == 0:
            icon = (
                QStyle.StandardPixmap.SP_DirIcon
                if node.is_directory
                else QStyle.StandardPixmap.SP_FileIcon
            )
            return QApplication.style().standardIcon(icon)
        if role == Qt.ItemDataRole.ToolTipRole:
            if entry is None:
                return str(self.snapshot.root)
            if entry.error:
                return f"{entry.path.as_posix()}\n{entry.error}"
            return entry.path.as_posix()
        if role == Qt.ItemDataRole.UserRole:
            return node
        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
            and 0 <= section < len(self.headers)
        ):
            return tr(self.headers[section])
        return None

    def hasChildren(self, parent: QModelIndex = INVALID_INDEX) -> bool:
        if parent.column() > 0:
            return False
        if not parent.isValid():
            return True
        node = self.node_from_index(parent)
        return bool(
            node is not None and node.is_directory and self._child_count(node) > 0
        )

    def canFetchMore(self, parent: QModelIndex) -> bool:
        node = self.node_from_index(parent)
        return bool(
            parent.column() == 0
            and node is not None
            and node.is_directory
            and len(node.children) < self._child_count(node)
        )

    def fetchMore(self, parent: QModelIndex) -> None:
        node = self.node_from_index(parent)
        if parent.column() > 0 or node is None or not node.is_directory:
            return
        first = len(node.children)
        remaining = self._child_count(node) - first
        if remaining <= 0:
            return
        entries = self.snapshot.children_after(
            node.path,
            after=node.children[-1].entry if node.children else None,
            limit=min(QUERY_PAGE_SIZE, remaining),
        )
        if not entries:
            node.child_count = first
            return
        self.beginInsertRows(parent, first, first + len(entries) - 1)
        node.children.extend(
            SnapshotNode(entry, node, row=first + offset)
            for offset, entry in enumerate(entries)
        )
        self.endInsertRows()

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.ItemIsDropEnabled
        node = self.node_from_index(index)
        if node is None:
            return Qt.ItemFlag.NoItemFlags
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if node.entry is not None:
            flags |= Qt.ItemFlag.ItemIsDragEnabled
        if node.is_directory:
            flags |= Qt.ItemFlag.ItemIsDropEnabled
        return flags

    def mimeTypes(self) -> list[str]:
        return [PLAN_MIME_TYPE]

    def mimeData(self, indexes: list[QModelIndex]) -> QMimeData:
        paths = []
        seen = set()
        for index in indexes:
            if index.column() != 0:
                continue
            node = self.node_from_index(index)
            if node is None or node.path is None:
                continue
            path = node.path.as_posix()
            if path not in seen:
                paths.append(path)
                seen.add(path)
        data = QMimeData()
        data.setData(PLAN_MIME_TYPE, json.dumps(paths).encode("utf-8"))
        return data

    def dropMimeData(
        self,
        data: QMimeData,
        action: Qt.DropAction,
        row: int,
        column: int,
        parent: QModelIndex,
    ) -> bool:
        if action == Qt.DropAction.IgnoreAction:
            return True
        if action != Qt.DropAction.MoveAction or not data.hasFormat(PLAN_MIME_TYPE):
            return False
        target = self.node_from_index(parent) if parent.isValid() else self.root
        if target is None or not target.is_directory:
            return False
        try:
            raw_paths = json.loads(bytes(data.data(PLAN_MIME_TYPE)).decode("utf-8"))
            sources = tuple(PurePosixPath(path) for path in raw_paths)
        except (TypeError, ValueError, UnicodeError, json.JSONDecodeError):
            return False
        if not sources:
            return False
        self.move_requested.emit(sources, target.path)
        return True

    def supportedDropActions(self) -> Qt.DropAction:
        return Qt.DropAction.MoveAction

    def supportedDragActions(self) -> Qt.DropAction:
        return Qt.DropAction.MoveAction

    @staticmethod
    def node_from_index(index: QModelIndex) -> SnapshotNode | None:
        if not index.isValid():
            return None
        node = index.internalPointer()
        return node if isinstance(node, SnapshotNode) else None

    def _child_count(self, node: SnapshotNode) -> int:
        if node.child_count is None:
            node.child_count = self.snapshot.child_count(node.path)
        return node.child_count

    def _root_data(self, column: int) -> str:
        if column == 0:
            return self.snapshot.root.name or str(self.snapshot.root)
        if column == 1:
            return tr("Snapshot Root")
        return "-"


class PlanListModel(QAbstractItemModel):
    headers = ("Action", "Source", "Target", "Policy", "Step", "Dependencies")

    def __init__(self, plan: FilePlan, parent=None) -> None:
        super().__init__(parent)
        self.plan = plan
        self.dependencies = basic_dependencies(plan)

    def set_plan(self, plan: FilePlan) -> None:
        self.beginResetModel()
        self.plan = plan
        self.dependencies = basic_dependencies(plan)
        self.endResetModel()

    def rowCount(self, parent=INVALID_INDEX) -> int:
        return 0 if parent.isValid() else len(self.plan)

    def columnCount(self, parent=INVALID_INDEX) -> int:
        return len(self.headers)

    def index(
        self,
        row: int,
        column: int,
        parent: QModelIndex = INVALID_INDEX,
    ) -> QModelIndex:
        if (
            parent.isValid()
            or row < 0
            or row >= len(self.plan)
            or column < 0
            or column >= len(self.headers)
        ):
            return QModelIndex()
        return self.createIndex(row, column)

    def parent(self, index: QModelIndex) -> QModelIndex:
        return QModelIndex()

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.plan):
            return None
        operation = self.plan.operations[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return (
                tr(operation.action.value.replace("_", " ").title()),
                operation.source.as_posix() if operation.source is not None else "-",
                operation.target.as_posix() if operation.target is not None else "-",
                operation.conflict_policy.value.replace("_", " ").upper()
                if operation.conflict_policy is not ConflictPolicy.ERROR
                else "-",
                str(index.row() + 1),
                str(len(self.dependencies.get(operation.operation_id, ()))),
            )[index.column()]
        if role == Qt.ItemDataRole.ToolTipRole:
            if index.column() == 5:
                dependencies = set(self.dependencies.get(operation.operation_id, ()))
                steps = [
                    str(row + 1)
                    for row, item in enumerate(self.plan.operations)
                    if item.operation_id in dependencies
                ]
                return "Path dependencies on steps: " + (
                    ", ".join(steps[:50]) if steps else "none"
                )
            value = self.data(index, Qt.ItemDataRole.DisplayRole)
            return value if value != "-" else None
        if role == Qt.ItemDataRole.UserRole:
            return operation.operation_id
        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role=Qt.ItemDataRole.DisplayRole,
    ):
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
            and 0 <= section < len(self.headers)
        ):
            return tr(self.headers[section])
        return None

    def operation_id(self, row: int) -> str | None:
        if not 0 <= row < len(self.plan):
            return None
        return self.plan.operations[row].operation_id


def snapshot_type(entry: SnapshotEntry) -> str:
    if entry.is_symlink:
        return tr("Symbolic Link")
    if entry.is_directory:
        return tr("Folder")
    suffix = PurePosixPath(entry.name).suffix
    return (
        tr("{extension} File", extension=suffix[1:].upper()) if suffix else tr("File")
    )


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1_024 or unit == "TB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1_024
    return "-"


def format_modified(modified_ns: int) -> str:
    if modified_ns <= 0:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(modified_ns / 1_000_000_000))
