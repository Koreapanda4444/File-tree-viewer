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

from fs_ops import create_exclusive, move_no_replace
from fs_paths import checked_boundary
from fs_paths import validate_windows_name as validate_name

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
            if child.name.casefold() in names:
                raise ValueError(f"Duplicate item name: {child.name}")
            names.add(child.name.casefold())
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
    checked_boundary(destination, destination, allow_leaf_link=False)
    if not destination.is_dir():
        raise NotADirectoryError(destination)
    output = destination / "Virtual Workspace"
    if os.path.lexists(output):
        raise FileExistsError(output)
    temporary = destination / f".file-tree-viewer-export-{uuid.uuid4().hex}"
    temporary.mkdir()
    try:
        stack = [(temporary, root_data)]
        while stack:
            parent_path, entry = stack.pop()
            children = entry.get("children", [])
            for child in children:
                target = parent_path / validate_name(child["name"])
                if child["directory"]:
                    create_exclusive(target, directory=True)
                    stack.append((target, child))
                else:
                    target.write_text(child["content"], encoding="utf-8", newline="")
        move_no_replace(temporary, output, allow_cross_device=False)
    except (OSError, TypeError, ValueError, KeyError):
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return output


def export_workspace_root(destination: Path, root: VirtualNode) -> Path:
    checked_boundary(destination, destination, allow_leaf_link=False)
    if not destination.is_dir():
        raise NotADirectoryError(destination)
    output = destination / "Virtual Workspace"
    if os.path.lexists(output):
        raise FileExistsError(output)
    temporary = destination / f".file-tree-viewer-export-{uuid.uuid4().hex}"
    temporary.mkdir()
    try:
        stack = [(temporary, root)]
        while stack:
            parent_path, node = stack.pop()
            for child in node.children:
                target = parent_path / validate_name(child.name)
                if child.is_directory:
                    create_exclusive(target, directory=True)
                    stack.append((target, child))
                else:
                    target.write_text(child.content, encoding="utf-8", newline="")
        move_no_replace(temporary, output, allow_cross_device=False)
    except (OSError, TypeError, ValueError):
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return output
