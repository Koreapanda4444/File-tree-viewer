from __future__ import annotations

import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot
from send2trash import send2trash


@dataclass(frozen=True, slots=True)
class FileChange:
    source: Path | None
    target: Path | None


@dataclass(slots=True)
class OperationRecord:
    action: str
    changes: list[FileChange]


def create_item(parent: Path, name: str, *, directory: bool) -> OperationRecord:
    clean_name = validate_name(name)
    if not parent.is_dir():
        raise NotADirectoryError(parent)

    target = parent / clean_name
    if target.exists() or target.is_symlink():
        raise FileExistsError(target)

    if directory:
        target.mkdir()
        action = "create_folder"
    else:
        target.touch(exist_ok=False)
        action = "create_file"
    return OperationRecord(action, [FileChange(None, target)])


def rename_item(source: Path, new_name: str) -> OperationRecord:
    clean_name = validate_name(new_name)
    target = source.with_name(clean_name)
    if target == source:
        raise ValueError("The name is unchanged")
    if target.exists() or target.is_symlink():
        raise FileExistsError(target)

    source.rename(target)
    return OperationRecord("rename", [FileChange(source, target)])


def validate_name(name: str) -> str:
    clean_name = name.strip()
    if not clean_name or clean_name in {".", ".."}:
        raise ValueError("Enter a valid name")
    if any(ord(character) < 32 for character in clean_name):
        raise ValueError("A name cannot contain control characters")
    if "\0" in clean_name or "/" in clean_name or "\\" in clean_name:
        raise ValueError("A name cannot contain path separators")
    if os.name == "nt":
        if any(character in clean_name for character in '<>:"|?*'):
            raise ValueError("The name contains an invalid Windows character")
        if clean_name.endswith((".", " ")):
            raise ValueError("A Windows name cannot end with a dot or space")
        stem = clean_name.split(".", 1)[0].casefold()
        reserved = {"con", "prn", "aux", "nul"}
        reserved.update(f"com{number}" for number in range(1, 10))
        reserved.update(f"lpt{number}" for number in range(1, 10))
        if stem in reserved:
            raise ValueError("The name is reserved by Windows")
    return clean_name


def normalize_sources(paths: list[Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        absolute = Path(os.path.abspath(path))
        key = os.path.normcase(str(absolute))
        if key not in seen:
            unique.append(absolute)
            seen.add(key)

    selected_keys = {os.path.normcase(str(path)) for path in unique}
    result = []
    for path in unique:
        if any(
            os.path.normcase(str(parent)) in selected_keys for parent in path.parents
        ):
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
                errors.append(f"{name}: {error}")
            else:
                changes.append(change)

        self.finished.emit(self.action, changes, errors)

    def _perform(self, item: Path | FileChange) -> FileChange:
        if self.action == "copy" and isinstance(item, Path):
            return self._copy(item)
        if self.action == "move" and isinstance(item, Path):
            return self._move(item)
        if self.action == "delete" and isinstance(item, Path):
            send2trash(str(item))
            return FileChange(item, None)
        if self.action == "undo" and isinstance(item, FileChange):
            self._undo(item)
            return item
        raise ValueError(f"Unknown file operation: {self.action}")

    def _copy(self, source: Path) -> FileChange:
        destination = self._checked_destination(source, moving=False)
        target = unique_destination(destination, source)
        if source.is_dir() and not source.is_symlink():
            target.mkdir()
            try:
                shutil.copytree(source, target, symlinks=True, dirs_exist_ok=True)
            except (OSError, shutil.Error):
                shutil.rmtree(target, ignore_errors=True)
                raise
        elif source.is_symlink():
            os.symlink(
                os.readlink(source),
                target,
                target_is_directory=source.is_dir(),
            )
        else:
            if not stat.S_ISREG(source.stat(follow_symlinks=False).st_mode):
                raise ValueError("This file type cannot be copied")
            try:
                with source.open("rb") as source_file, target.open("xb") as target_file:
                    shutil.copyfileobj(source_file, target_file, length=1_024 * 1_024)
                shutil.copystat(source, target, follow_symlinks=False)
            except (OSError, shutil.Error):
                target.unlink(missing_ok=True)
                raise
        return FileChange(source, target)

    def _move(self, source: Path) -> FileChange:
        destination = self._checked_destination(source, moving=True)
        target = unique_destination(destination, source)
        if target.exists() or target.is_symlink():
            raise FileExistsError(target)
        shutil.move(str(source), str(target))
        return FileChange(source, target)

    def _checked_destination(self, source: Path, *, moving: bool) -> Path:
        destination = self.destination
        if destination is None or not destination.is_dir():
            raise NotADirectoryError(destination)
        if not source.exists() and not source.is_symlink():
            raise FileNotFoundError(source)

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
        if record is None:
            raise ValueError("There is no operation to undo")

        if record.action in {"copy", "create_file", "create_folder"}:
            target = change.target
            if target is not None and (target.exists() or target.is_symlink()):
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
            source.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(target), str(source))
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
