from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot
from send2trash import send2trash

from fs_ops import (
    copy_exclusive,
    create_exclusive,
    file_signature,
    move_no_replace,
    operation_error,
    require_unchanged,
    tree_fingerprint,
)
from fs_paths import checked_boundary, normalized_path, validate_name


@dataclass(frozen=True, slots=True)
class FileChange:
    source: Path | None
    target: Path | None
    fingerprint: str | None = None


@dataclass(slots=True)
class OperationRecord:
    action: str
    changes: list[FileChange]


def create_item(parent: Path, name: str, *, directory: bool) -> OperationRecord:
    clean_name = validate_name(name)
    if not parent.is_dir():
        raise NotADirectoryError(parent)

    checked_boundary(parent, parent, allow_leaf_link=False)
    target = parent / clean_name
    if target.exists() or target.is_symlink():
        raise FileExistsError(target)

    if directory:
        create_exclusive(target, directory=True)
        action = "create_folder"
    else:
        create_exclusive(target, directory=False)
        action = "create_file"
    return OperationRecord(action, [record_change(None, target)])


def rename_item(source: Path, new_name: str) -> OperationRecord:
    clean_name = validate_name(new_name)
    target = source.with_name(clean_name)
    if target == source:
        raise ValueError("The name is unchanged")
    if target.exists() or target.is_symlink():
        raise FileExistsError(target)

    checked_boundary(source.parent, source)
    checked_boundary(source.parent, target, allow_leaf_link=False)
    move_no_replace(source, target)
    return OperationRecord("rename", [record_change(source, target)])


def normalize_sources(paths: list[Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        absolute = Path(os.path.abspath(path))
        key = normalized_path(absolute)
        if key not in seen:
            unique.append(absolute)
            seen.add(key)

    selected_keys = {normalized_path(path) for path in unique}
    result = []
    for path in unique:
        if any(normalized_path(parent) in selected_keys for parent in path.parents):
            continue
        result.append(path)
    return result


class FileOperationWorker(QObject):
    progress = Signal(str, int, int, str)
    finished = Signal(str, object, object)

    def __init__(
        self,
        action: str,
        *,
        sources: list[Path] | None = None,
        destination: Path | None = None,
        record: OperationRecord | None = None,
    ) -> None:
        super().__init__()
        self.action = action
        self.sources = normalize_sources(sources or [])
        self.destination = destination
        self.record = record
        self._expected_sources: dict[Path, dict[str, int] | OSError] = {}
        for source in self.sources:
            try:
                self._expected_sources[source] = file_signature(source)
            except OSError as error:
                self._expected_sources[source] = error

    @Slot()
    def run(self) -> None:
        changes: list[FileChange] = []
        errors: list[str] = []
        items: list[Path] | list[FileChange]
        if self.action == "undo":
            items = list(reversed(self.record.changes)) if self.record else []
        else:
            items = self.sources

        total = len(items)
        for position, item in enumerate(items, start=1):
            name = self._item_name(item)
            self.progress.emit(self.action, position, total, name)
            try:
                change = self._perform(item)
            except (OSError, RuntimeError, shutil.Error, ValueError) as error:
                errors.append(f"{name}: {operation_error(error)}")
            else:
                changes.append(change)

        self.finished.emit(self.action, changes, errors)

    def _perform(self, item: Path | FileChange) -> FileChange:
        if isinstance(item, Path) and item in self._expected_sources:
            expected = self._expected_sources[item]
            if isinstance(expected, OSError):
                raise expected
            require_unchanged(item, expected)
        if self.action == "copy" and isinstance(item, Path):
            return self._copy(item)
        if self.action == "move" and isinstance(item, Path):
            return self._move(item)
        if self.action == "delete" and isinstance(item, Path):
            checked_boundary(item.parent, item)
            send2trash(str(item))
            return FileChange(item, None)
        if self.action == "undo" and isinstance(item, FileChange):
            self._undo(item)
            return item
        raise ValueError(f"Unknown file operation: {self.action}")

    def _copy(self, source: Path) -> FileChange:
        destination = self._checked_destination(source, moving=False)
        target = unique_destination(destination, source)
        copy_exclusive(source, target)
        return record_change(source, target)

    def _move(self, source: Path) -> FileChange:
        destination = self._checked_destination(source, moving=True)
        target = unique_destination(destination, source)
        if target.exists() or target.is_symlink():
            raise FileExistsError(target)
        move_no_replace(source, target)
        return record_change(source, target)

    def _checked_destination(self, source: Path, *, moving: bool) -> Path:
        checked_boundary(source.parent, source)
        destination = self.destination
        if destination is None or not destination.is_dir():
            raise NotADirectoryError(destination)
        if not source.exists() and not source.is_symlink():
            raise FileNotFoundError(source)

        checked_boundary(destination, destination, allow_leaf_link=False)
        source_resolved = (
            Path(os.path.abspath(source))
            if source.is_symlink()
            else source.resolve(strict=True)
        )
        destination_resolved = destination.resolve(strict=True)
        if moving and destination_resolved == source.parent.resolve(strict=True):
            raise ValueError("The item is already in that folder")
        if (
            source.is_dir()
            and not source.is_symlink()
            and (
                destination_resolved == source_resolved
                or source_resolved in destination_resolved.parents
            )
        ):
            raise ValueError("A folder cannot be copied or moved inside itself")
        return destination

    def _undo(self, change: FileChange) -> None:
        record = self.record
        if change.target is not None and change.fingerprint is not None:
            if not change.target.exists() and not change.target.is_symlink():
                raise FileNotFoundError(change.target)
            if tree_fingerprint(change.target) != change.fingerprint:
                raise FileExistsError(
                    f"Undo target changed externally: {change.target}"
                )
        if record is None:
            raise ValueError("There is no operation to undo")

        if record.action in {"copy", "create_file", "create_folder"}:
            target = change.target
            if target is not None and (target.exists() or target.is_symlink()):
                checked_boundary(target.parent, target)
                send2trash(str(target))
            return

        if record.action in {"move", "rename"}:
            source = change.source
            target = change.target
            if source is None or target is None:
                raise ValueError("The undo record is incomplete")
            if source.exists() or source.is_symlink():
                raise FileExistsError(source)
            if not target.exists() and not target.is_symlink():
                raise FileNotFoundError(target)
            checked_boundary(target.parent, target)
            checked_boundary(source.parent, source, allow_leaf_link=False)
            move_no_replace(target, source)
            return

        raise ValueError(f"The {record.action} operation cannot be undone")

    @staticmethod
    def _item_name(item: Path | FileChange) -> str:
        if isinstance(item, Path):
            return item.name or str(item)
        path = item.target or item.source
        return path.name if path is not None else "item"


def unique_destination(destination: Path, source: Path) -> Path:
    target = destination / source.name
    if not target.exists() and not target.is_symlink():
        return target

    suffix = "" if source.is_dir() and not source.is_symlink() else source.suffix
    stem = source.name if not suffix else source.name[: -len(suffix)]
    number = 2
    while True:
        target = destination / f"{stem} ({number}){suffix}"
        if not target.exists() and not target.is_symlink():
            return target
        number += 1


def record_change(source: Path | None, target: Path | None) -> FileChange:
    return FileChange(
        source, target, tree_fingerprint(target) if target is not None else None
    )
