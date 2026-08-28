from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from PySide6.QtCore import (
    QAbstractItemModel,
    QModelIndex,
    QObject,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtWidgets import QApplication, QStyle

INVALID_INDEX = QModelIndex()
WORKSPACE_FORMAT = "file-tree-viewer-virtual-v1"


@dataclass(slots=True)
class VirtualNode:
    name: str
    is_directory: bool
    parent: VirtualNode | None = None
    content: str = ""
    modified: float = field(default_factory=time.time)
    children: list[VirtualNode] = field(default_factory=list)


@dataclass(slots=True)
class VirtualUndoRecord:
    action: str
    items: list[Any]


class VirtualTreeModel(QAbstractItemModel):
    headers = ("Name", "Type", "Size", "Modified")

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.root = VirtualNode("VM:", True)

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
        return self.createIndex(parent.parent.children.index(parent), 0, parent)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        node = self.node_from_index(index)
        if node is None:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            if index.column() == 0:
                return node.name
            if index.column() == 1:
                return file_type(node)
            if index.column() == 2:
                return "-" if node.is_directory else format_size(node_size(node))
            if index.column() == 3:
                return time.strftime(
                    "%Y-%m-%d %H:%M:%S",
                    time.localtime(node.modified),
                )
        if role == Qt.ItemDataRole.DecorationRole and index.column() == 0:
            icon = (
                QStyle.StandardPixmap.SP_DirIcon
                if node.is_directory
                else QStyle.StandardPixmap.SP_FileIcon
            )
            return QApplication.style().standardIcon(icon)
        if role == Qt.ItemDataRole.ToolTipRole:
            return self.path_for(node)
        if role == Qt.ItemDataRole.UserRole:
            return node
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

    @staticmethod
    def node_from_index(index: QModelIndex) -> VirtualNode | None:
        if not index.isValid():
            return None
        node = index.internalPointer()
        return node if isinstance(node, VirtualNode) else None

    def index_for_node(self, node: VirtualNode, column: int = 0) -> QModelIndex:
        if node is self.root:
            return self.createIndex(0, column, node)
        if node.parent is None:
            return QModelIndex()
        try:
            row = node.parent.children.index(node)
        except ValueError:
            return QModelIndex()
        return self.createIndex(row, column, node)

    def path_for(self, node: VirtualNode) -> str:
        if node is self.root:
            return "VM:/"
        names = []
        current = node
        while current is not self.root:
            names.append(current.name)
            if current.parent is None:
                break
            current = current.parent
        return "VM:/" + "/".join(reversed(names))

    def resolve_path(self, path: str) -> VirtualNode | None:
        normalized = path.strip().replace("\\", "/")
        if normalized in {"VM:", "VM:/", "/", ""}:
            return self.root
        if normalized.casefold().startswith("vm:/"):
            normalized = normalized[4:]
        elif normalized.startswith("/"):
            normalized = normalized[1:]
        current = self.root
        for name in (part for part in normalized.split("/") if part):
            current = next(
                (child for child in current.children if child.name == name),
                None,
            )
            if current is None:
                return None
        return current

    def create_item(
        self,
        parent: VirtualNode,
        name: str,
        *,
        directory: bool,
    ) -> VirtualUndoRecord:
        self._require_directory(parent)
        checked = validate_name(name)
        self._require_available(parent, checked)
        node = VirtualNode(checked, directory, parent=parent)
        self.beginResetModel()
        parent.children.append(node)
        parent.modified = time.time()
        self.endResetModel()
        return VirtualUndoRecord("create", [node])

    def rename_item(self, node: VirtualNode, name: str) -> VirtualUndoRecord:
        if node is self.root or node.parent is None:
            raise ValueError("The virtual root cannot be renamed")
        checked = validate_name(name)
        if checked == node.name:
            raise ValueError("The new name is unchanged")
        self._require_available(node.parent, checked, ignore=node)
        old_name = node.name
        self.beginResetModel()
        node.name = checked
        node.modified = time.time()
        node.parent.modified = node.modified
        self.endResetModel()
        return VirtualUndoRecord("rename", [(node, old_name)])

    def copy_items(
        self,
        nodes: list[VirtualNode],
        destination: VirtualNode,
    ) -> VirtualUndoRecord:
        selected = normalize_nodes(nodes, self.root)
        self._validate_transfer(selected, destination, moving=False)
        copies = [clone_tree(node, destination) for node in selected]
        self.beginResetModel()
        destination.children.extend(copies)
        destination.modified = time.time()
        self.endResetModel()
        return VirtualUndoRecord("copy", copies)

    def move_items(
        self,
        nodes: list[VirtualNode],
        destination: VirtualNode,
    ) -> VirtualUndoRecord:
        selected = normalize_nodes(nodes, self.root)
        self._validate_transfer(selected, destination, moving=True)
        positions = [
            (node, node.parent, node.parent.children.index(node))
            for node in selected
            if node.parent is not None
        ]
        self.beginResetModel()
        for node, parent, row in sorted(
            positions, key=lambda item: item[2], reverse=True
        ):
            parent.children.pop(row)
            parent.modified = time.time()
        for node in selected:
            node.parent = destination
            destination.children.append(node)
        destination.modified = time.time()
        self.endResetModel()
        return VirtualUndoRecord("move", positions)

    def delete_items(self, nodes: list[VirtualNode]) -> VirtualUndoRecord:
        selected = normalize_nodes(nodes, self.root)
        positions = [
            (node, node.parent, node.parent.children.index(node))
            for node in selected
            if node.parent is not None
        ]
        self.beginResetModel()
        for node, parent, row in sorted(
            positions, key=lambda item: item[2], reverse=True
        ):
            parent.children.pop(row)
            parent.modified = time.time()
            node.parent = None
        self.endResetModel()
        return VirtualUndoRecord("delete", positions)

    def update_content(self, node: VirtualNode, content: str) -> VirtualUndoRecord:
        if node.is_directory:
            raise IsADirectoryError(node.name)
        old_content = node.content
        self.beginResetModel()
        node.content = content
        node.modified = time.time()
        self.endResetModel()
        return VirtualUndoRecord("edit", [(node, old_content)])

    def undo(self, record: VirtualUndoRecord) -> None:
        self.beginResetModel()
        if record.action in {"create", "copy"}:
            for node in record.items:
                if node.parent is not None and node in node.parent.children:
                    node.parent.children.remove(node)
                    node.parent.modified = time.time()
                    node.parent = None
        elif record.action == "rename":
            for node, old_name in record.items:
                node.name = old_name
                node.modified = time.time()
        elif record.action in {"move", "delete"}:
            for node, _, _ in record.items:
                if node.parent is not None and node in node.parent.children:
                    node.parent.children.remove(node)
            grouped: dict[int, tuple[VirtualNode, list[tuple[int, VirtualNode]]]] = {}
            for node, parent, row in record.items:
                key = id(parent)
                if key not in grouped:
                    grouped[key] = (parent, [])
                grouped[key][1].append((row, node))
            for parent, entries in grouped.values():
                for row, node in sorted(entries):
                    node.parent = parent
                    parent.children.insert(min(row, len(parent.children)), node)
                parent.modified = time.time()
        elif record.action == "edit":
            for node, old_content in record.items:
                node.content = old_content
                node.modified = time.time()
        self.endResetModel()

    def reset_workspace(self) -> None:
        self.beginResetModel()
        self.root = VirtualNode("VM:", True)
        self.endResetModel()

    def replace_workspace(self, data: dict[str, Any]) -> None:
        self.replace_root(node_from_data(data))

    def replace_root(self, root: VirtualNode) -> None:
        root.name = "VM:"
        root.parent = None
        root.is_directory = True
        self.beginResetModel()
        self.root = root
        self.endResetModel()

    def to_data(self) -> dict[str, Any]:
        return node_to_data(self.root)

    @staticmethod
    def _require_directory(node: VirtualNode) -> None:
        if not node.is_directory:
            raise NotADirectoryError(node.name)

    @staticmethod
    def _require_available(
        parent: VirtualNode,
        name: str,
        *,
        ignore: VirtualNode | None = None,
    ) -> None:
        if any(child is not ignore and child.name == name for child in parent.children):
            raise FileExistsError(name)

    def _validate_transfer(
        self,
        nodes: list[VirtualNode],
        destination: VirtualNode,
        *,
        moving: bool,
    ) -> None:
        self._require_directory(destination)
        if not nodes:
            raise ValueError("Select at least one item")
        names = set()
        for node in nodes:
            if node is self.root:
                raise ValueError("The virtual root cannot be copied or moved")
            if node.name in names:
                raise FileExistsError(node.name)
            names.add(node.name)
            if node is destination or is_ancestor(node, destination):
                raise ValueError("A folder cannot be placed inside itself")
            if moving and node.parent is destination:
                raise ValueError(f"{node.name} is already in that folder")
            self._require_available(
                destination, node.name, ignore=node if moving else None
            )


def normalize_nodes(nodes: list[VirtualNode], root: VirtualNode) -> list[VirtualNode]:
    unique = []
    seen = set()
    for node in nodes:
        if node is root or id(node) in seen:
            continue
        seen.add(id(node))
        unique.append(node)
    selected = set(map(id, unique))
    result = []
    for node in unique:
        parent = node.parent
        nested = False
        while parent is not None:
            if id(parent) in selected:
                nested = True
                break
            parent = parent.parent
        if not nested:
            result.append(node)
    return result


def is_ancestor(node: VirtualNode, possible_child: VirtualNode) -> bool:
    current = possible_child.parent
    while current is not None:
        if current is node:
            return True
        current = current.parent
    return False


def clone_tree(source: VirtualNode, parent: VirtualNode) -> VirtualNode:
    root = VirtualNode(
        source.name,
        source.is_directory,
        parent=parent,
        content=source.content,
        modified=source.modified,
    )
    stack = [(source, root)]
    while stack:
        source_node, target_node = stack.pop()
        for child in source_node.children:
            clone = VirtualNode(
                child.name,
                child.is_directory,
                parent=target_node,
                content=child.content,
                modified=child.modified,
            )
            target_node.children.append(clone)
            stack.append((child, clone))
    return root


def validate_name(name: str) -> str:
    checked = name.strip()
    if not checked or checked in {".", ".."}:
        raise ValueError("Enter a valid name")
    if any(ord(character) < 32 for character in checked):
        raise ValueError("The name contains a control character")
    if any(character in checked for character in '<>:"/\\|?*'):
        raise ValueError("The name contains an invalid character")
    if checked.endswith((".", " ")):
        raise ValueError("The name cannot end with a dot or space")
    stem = checked.split(".", 1)[0].casefold()
    reserved = {"con", "prn", "aux", "nul"}
    reserved.update(f"com{number}" for number in range(1, 10))
    reserved.update(f"lpt{number}" for number in range(1, 10))
    if stem in reserved:
        raise ValueError("The name is reserved by Windows")
    return checked


def node_size(node: VirtualNode) -> int:
    return len(node.content.encode("utf-8"))


def file_type(node: VirtualNode) -> str:
    if node.is_directory:
        return "Folder"
    suffix = Path(node.name).suffix
    return f"{suffix[1:].upper()} File" if suffix else "File"


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1_024 or unit == "TB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1_024
    return "-"


def node_to_data(root: VirtualNode) -> dict[str, Any]:
    result = {
        "name": root.name,
        "directory": root.is_directory,
        "content": root.content,
        "modified": root.modified,
        "children": [],
    }
    stack = [(root, result)]
    while stack:
        node, target = stack.pop()
        for child in node.children:
            child_data = {
                "name": child.name,
                "directory": child.is_directory,
                "content": child.content,
                "modified": child.modified,
                "children": [],
            }
            target["children"].append(child_data)
            stack.append((child, child_data))
    return result


def node_from_data(data: dict[str, Any]) -> VirtualNode:
    root = node_from_entry(data, None)
    stack = [(root, data)]
    while stack:
        parent, entry = stack.pop()
        children = entry.get("children", [])
        if not isinstance(children, list):
            raise TypeError("Invalid workspace children")
        if not parent.is_directory and children:
            raise ValueError("A file cannot contain child items")
        names = set()
        for child_entry in children:
            if not isinstance(child_entry, dict):
                raise TypeError("Invalid workspace item")
            child = node_from_entry(child_entry, parent)
            if child.name in names:
                raise ValueError(f"Duplicate item name: {child.name}")
            names.add(child.name)
            parent.children.append(child)
            stack.append((child, child_entry))
    return root


def node_from_entry(data: dict[str, Any], parent: VirtualNode | None) -> VirtualNode:
    name = data.get("name")
    directory = data.get("directory")
    content = data.get("content", "")
    modified = data.get("modified", time.time())
    if not isinstance(name, str) or not isinstance(directory, bool):
        raise TypeError("Invalid workspace item")
    if parent is not None:
        name = validate_name(name)
    if not isinstance(content, str) or not isinstance(modified, (int, float)):
        raise TypeError("Invalid workspace content")
    return VirtualNode(name, directory, parent, content, float(modified))


def workspace_document(root_data: dict[str, Any]) -> dict[str, Any]:
    return {"format": WORKSPACE_FORMAT, "root": root_data}


def save_workspace(path: Path, root_data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
            json.dump(
                workspace_document(root_data),
                file,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, path)
    except (OSError, TypeError, ValueError):
        temporary_path.unlink(missing_ok=True)
        raise


def save_workspace_root(path: Path, root: VirtualNode) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
            file.write('{"format":')
            json.dump(WORKSPACE_FORMAT, file)
            file.write(',"root":')
            write_node_json(file, root)
            file.write("}")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, path)
    except (OSError, TypeError, ValueError):
        temporary_path.unlink(missing_ok=True)
        raise


def write_node_json(file: TextIO, root: VirtualNode) -> None:
    def write_start(node: VirtualNode) -> None:
        file.write('{"name":')
        json.dump(node.name, file, ensure_ascii=False)
        file.write(',"directory":')
        file.write("true" if node.is_directory else "false")
        file.write(',"content":')
        json.dump(node.content, file, ensure_ascii=False)
        file.write(',"modified":')
        json.dump(node.modified, file)
        file.write(',"children":[')

    write_start(root)
    stack = [(root, 0)]
    while stack:
        node, position = stack[-1]
        if position >= len(node.children):
            file.write("]}")
            stack.pop()
            continue
        if position:
            file.write(",")
        child = node.children[position]
        stack[-1] = (node, position + 1)
        write_start(child)
        stack.append((child, 0))


def load_workspace(path: Path) -> dict[str, Any]:
    root = load_workspace_data(path)
    node_from_data(root)
    return root


def load_workspace_root(path: Path) -> VirtualNode:
    return node_from_data(load_workspace_data(path))


def load_workspace_data(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        document = json.load(file)
    if not isinstance(document, dict) or document.get("format") != WORKSPACE_FORMAT:
        raise ValueError("Unsupported virtual workspace file")
    root = document.get("root")
    if not isinstance(root, dict):
        raise TypeError("The workspace root is missing")
    return root


def export_workspace(destination: Path, root_data: dict[str, Any]) -> Path:
    if not destination.is_dir():
        raise NotADirectoryError(destination)
    output = destination / "Virtual Workspace"
    if output.exists():
        raise FileExistsError(output)
    temporary = destination / f".file-tree-viewer-export-{uuid.uuid4().hex}"
    temporary.mkdir()
    try:
        stack = [(temporary, root_data)]
        while stack:
            parent_path, entry = stack.pop()
            children = entry.get("children", [])
            for child in children:
                target = parent_path / child["name"]
                if child["directory"]:
                    target.mkdir()
                    stack.append((target, child))
                else:
                    target.write_text(child["content"], encoding="utf-8", newline="")
        os.replace(temporary, output)
    except (OSError, TypeError, ValueError, KeyError):
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return output


def export_workspace_root(destination: Path, root: VirtualNode) -> Path:
    if not destination.is_dir():
        raise NotADirectoryError(destination)
    output = destination / "Virtual Workspace"
    if output.exists():
        raise FileExistsError(output)
    temporary = destination / f".file-tree-viewer-export-{uuid.uuid4().hex}"
    temporary.mkdir()
    try:
        stack = [(temporary, root)]
        while stack:
            parent_path, node = stack.pop()
            for child in node.children:
                target = parent_path / child.name
                if child.is_directory:
                    target.mkdir()
                    stack.append((target, child))
                else:
                    target.write_text(child.content, encoding="utf-8", newline="")
        os.replace(temporary, output)
    except (OSError, TypeError, ValueError):
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return output


class VirtualTaskWorker(QObject):
    finished = Signal(str, object, str)

    def __init__(
        self,
        action: str,
        path: Path,
        root_data: dict[str, Any] | VirtualNode | None = None,
    ) -> None:
        super().__init__()
        self.action = action
        self.path = path
        self.root_data = root_data

    @Slot()
    def run(self) -> None:
        try:
            if self.action == "save":
                if self.root_data is None:
                    raise ValueError("Workspace data is missing")
                if isinstance(self.root_data, VirtualNode):
                    save_workspace_root(self.path, self.root_data)
                else:
                    save_workspace(self.path, self.root_data)
                result: object = self.path
            elif self.action == "load":
                result = load_workspace_root(self.path)
            elif self.action == "export":
                if self.root_data is None:
                    raise ValueError("Workspace data is missing")
                if isinstance(self.root_data, VirtualNode):
                    result = export_workspace_root(self.path, self.root_data)
                else:
                    result = export_workspace(self.path, self.root_data)
            else:
                raise ValueError(f"Unknown virtual task: {self.action}")
            self.finished.emit(self.action, result, "")
        except (
            OSError,
            RuntimeError,
            UnicodeError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as error:
            self.finished.emit(self.action, None, str(error))
