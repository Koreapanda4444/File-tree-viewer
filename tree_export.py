from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from fs_paths import INTERNAL_DIRECTORY, checked_boundary, is_hidden, is_reparse_point
from persistence import atomic_text_write
from snapshot_store import check_cancelled


@dataclass(frozen=True, slots=True)
class TreeExportOptions:
    max_depth: int = 8
    show_hidden: bool = False
    excluded_extensions: tuple[str, ...] = ()
    excluded_folders: tuple[str, ...] = ()
    show_sizes: bool = False
    max_entries: int = 100_000

    def __post_init__(self):
        if not 0 <= self.max_depth <= 256 or not 1 <= self.max_entries <= 1_000_000:
            raise ValueError("Depth must be 0–256 and entry limit 1–1,000,000")


def build_tree(source, options=None, *, kind="real", cancelled=None, progress=None):
    options = options or TreeExportOptions()

    def children(parent):
        if kind == "real":
            result = []
            with os.scandir(parent) as iterator:
                for entry in iterator:
                    check_cancelled(cancelled)
                    if not options.show_hidden and is_hidden(entry):
                        continue
                    link = is_reparse_point(entry)
                    directory = entry.is_dir(follow_symlinks=False) and not link
                    size = None
                    if options.show_sizes and not directory:
                        size = entry.stat(follow_symlinks=False).st_size
                    result.append((entry.name, directory, link, size, Path(entry.path)))
                    if len(result) > options.max_entries:
                        raise ValueError("Directory exceeds the export entry limit")
            return result
        if kind == "virtual":
            return [
                (
                    node.name,
                    node.is_directory,
                    False,
                    len(node.content.encode("utf-8"))
                    if options.show_sizes and not node.is_directory
                    else None,
                    node,
                )
                for node in parent.children
            ]
        return [
            (
                entry.name,
                entry.is_directory,
                entry.is_symlink,
                entry.size if options.show_sizes and not entry.is_directory else None,
                entry.path,
            )
            for entry in source.children(parent, limit=options.max_entries + 1)
        ]

    if kind not in {"real", "virtual", "snapshot"}:
        raise ValueError("Unknown tree source")
    if kind == "real":
        source = Path(source).resolve(strict=True)
        if not source.is_dir():
            raise NotADirectoryError(source)
    root = {
        "name": source.name or str(source) if kind != "snapshot" else source.name,
        "type": "folder",
        "children": [],
    }
    stack = [(root, source if kind != "snapshot" else None, 0)]
    count = 1
    extensions = {value.casefold().lstrip(".") for value in options.excluded_extensions}
    folders = {
        value.casefold() for value in (*options.excluded_folders, INTERNAL_DIRECTORY)
    }
    while stack:
        check_cancelled(cancelled)
        node, reference, depth = stack.pop()
        if depth >= (options.max_depth or 256):
            node["depth_limited"] = True
            continue
        try:
            entries = children(reference)
        except OSError as error:
            node["error"] = str(error)
            continue
        entries.sort(key=lambda item: (not item[1], item[0].casefold(), item[0]))
        descend = []
        for name, directory, link, size, reference in entries:
            check_cancelled(cancelled)
            if (
                not options.show_hidden
                and name.startswith(".")
                or directory
                and name.casefold() in folders
                or not directory
                and Path(name).suffix.casefold().lstrip(".") in extensions
            ):
                continue
            count += 1
            if count > options.max_entries:
                raise ValueError(
                    "Tree exceeds the export entry limit; reduce depth or add exclusions"
                )
            child = {
                "name": name,
                "type": "link" if link else "folder" if directory else "file",
            }
            if size is not None:
                child["size"] = size
            node["children"].append(child)
            if directory:
                child["children"] = []
                descend.append((child, reference, depth + 1))
            if progress and count % 512 == 0:
                progress(count)
        stack.extend(reversed(descend))
    return {
        "format": "file-tree-viewer-tree",
        "version": 1,
        "entry_count": count,
        "tree": root,
    }


def tree_lines(document):
    def label(node):
        name = "".join(
            f"\\u{ord(char):04x}" if ord(char) < 32 else char for char in node["name"]
        )
        return (
            name
            + (
                "/"
                if node["type"] == "folder"
                else " @"
                if node["type"] == "link"
                else ""
            )
            + (f" ({node['size']:,} B)" if "size" in node else "")
            + (" [depth limit]" if node.get("depth_limited") else "")
            + (" [unavailable]" if "error" in node else "")
        )

    root = document["tree"]
    yield label(root)
    stack = [
        (child, "", index == len(root.get("children", [])) - 1)
        for index, child in reversed(list(enumerate(root.get("children", []))))
    ]
    while stack:
        node, prefix, last = stack.pop()
        yield prefix + ("└── " if last else "├── ") + label(node)
        children = node.get("children", [])
        next_prefix = prefix + ("    " if last else "│   ")
        stack.extend(
            (child, next_prefix, index == len(children) - 1)
            for index, child in reversed(list(enumerate(children)))
        )


def write_tree(document, destination, format="txt", *, cancelled=None):
    destination = Path(destination).absolute()
    checked_boundary(destination.parent, destination, allow_leaf_link=False)
    if format not in {"txt", "md", "json"}:
        raise ValueError("Choose TXT, Markdown or JSON")

    def write(stream):
        if format == "json":
            for chunk in json.JSONEncoder(ensure_ascii=False, indent=2).iterencode(
                document
            ):
                check_cancelled(cancelled)
                stream.write(chunk)
        else:
            fence = (
                "`"
                * max(
                    3,
                    1
                    + max(
                        (
                            len(match[0])
                            for line in tree_lines(document)
                            for match in re.finditer(r"`+", line)
                        ),
                        default=0,
                    ),
                )
                if format == "md"
                else ""
            )
            if fence:
                stream.write(fence + "text\n")
            for line in tree_lines(document):
                check_cancelled(cancelled)
                stream.write(line + "\n")
            if fence:
                stream.write(fence + "\n")
        check_cancelled(cancelled)

    atomic_text_write(destination, write, overwrite=False)
    return destination
