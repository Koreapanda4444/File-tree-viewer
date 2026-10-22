from __future__ import annotations

import os
import shutil
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

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


class FileConflictPolicy(str, Enum):
    RENAME = "rename"
    SKIP = "skip"
    STOP = "stop"


class FileOperationSkipped(Exception):
    pass


@dataclass(frozen=True, slots=True)
class OperationItem:
    status: str
    source: Path | None
    target: Path | None
    message: str = ""


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


class FileOperationTask:
    def __init__(
        self,
        action: str,
        *,
        sources: list[Path] | None = None,
        destination: Path | None = None,
        record: OperationRecord | None = None,
        conflict_policy: FileConflictPolicy | str = FileConflictPolicy.RENAME,
        cancelled: threading.Event | None = None,
    ) -> None:
        self._cancelled = cancelled if cancelled is not None else threading.Event()
        self.conflict_policy = FileConflictPolicy(conflict_policy)
        self.outcomes: list[OperationItem] = []
        self.current_target = None
        self.action = action
        self.sources = normalize_sources(sources or [])
        self.destination = destination
        self.record = record
        self._expected_sources: dict[Path, dict[str, int] | OSError] = {}
        for source in self.sources:
            if self._cancelled.is_set():
                break
            try:
                self._expected_sources[source] = file_signature(source)
            except OSError as error:
                self._expected_sources[source] = error

    def cancel(self) -> None:
        self._cancelled.set()

    def run(
        self, *, progress=None, item_finished=None
    ) -> tuple[list[FileChange], list[str]]:
        changes: list[FileChange] = []
        errors: list[str] = []
        self.outcomes.clear()
        items = (
            (list(reversed(self.record.changes)) if self.record else [])
            if self.action == "undo"
            else self.sources
        )

        def report(status, item, message=""):
            source, target = self._item_paths(item)
            result = OperationItem(
                status,
                source,
                self.current_target if status in {"success", "failed"} else target,
                message,
            )
            self.outcomes.append(result)
            if item_finished is not None:
                item_finished(result)

        total = len(items)
        for position, item in enumerate(items, start=1):
            if self._cancelled.is_set():
                errors.append(
                    "Operation cancelled; completed copies and moves can be undone"
                )
                for pending in items[position - 1 :]:
                    report("cancelled", pending, "Not started; cancellation requested")
                break
            name = self._item_name(item)
            self.current_target = self._item_paths(item)[1]
            if progress is not None:
                progress(self.action, position, total, name)
            try:
                change = self._perform(item)
            except FileOperationSkipped as error:
                report("skipped", item, str(error))
            except (OSError, RuntimeError, shutil.Error, ValueError) as error:
                message = operation_error(error)
                errors.append(f"{name}: {message}")
                report("failed", item, message)
                if (
                    isinstance(error, FileExistsError)
                    and self.conflict_policy is FileConflictPolicy.STOP
                ):
                    for pending in items[position:]:
                        report(
                            "skipped", pending, "Not started; stopped after a conflict"
                        )
                    break
            else:
                changes.append(change)
                report("success", item)
        return changes, errors

    def _item_paths(self, item):
        if isinstance(item, Path):
            return (
                item,
                self.destination / item.name if self.destination is not None else None,
            )
        moving = self.record is not None and self.record.action in {"move", "rename"}
        return item.target or item.source, item.source if moving else None

    def _transfer_target(self, destination, source):
        target = destination / source.name
        if target.exists() or target.is_symlink():
            if self.conflict_policy is FileConflictPolicy.SKIP:
                raise FileOperationSkipped(f"Target already exists: {target}")
            if self.conflict_policy is FileConflictPolicy.STOP:
                raise FileExistsError(f"Target already exists: {target}")
            target = unique_destination(destination, source)
        self.current_target = target
        return target

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
        target = self._transfer_target(destination, source)
        copy_exclusive(source, target)
        return record_change(source, target)

    def _move(self, source: Path) -> FileChange:
        destination = self._checked_destination(source, moving=True)
        target = self._transfer_target(destination, source)
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
