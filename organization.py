from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, timezone
from fnmatch import fnmatchcase
from pathlib import PurePosixPath

from planning import FilePlan, PlanAction, PlanOperation, normalize_plan_path
from real.operations import validate_name
from snapshot import FileSnapshot, SnapshotEntry


def checked_name(name: str) -> str:
    if validate_name(name) != name:
        raise ValueError("Names cannot begin or end with whitespace")
    return name


@dataclass(frozen=True)
class RenameOptions:
    prefix: str = ""
    suffix: str = ""
    find: str = ""
    replacement: str = ""
    case: str = "Keep"
    keep_extension: bool = True
    number: bool = False
    start: int = 1
    digits: int = 3

    def name(self, entry: SnapshotEntry, index: int) -> str:
        if self.start < 0 or not 1 <= self.digits <= 12:
            raise ValueError("Invalid numbering settings")
        extension = (
            entry.path.suffix if self.keep_extension and not entry.is_directory else ""
        )
        stem = entry.name[: -len(extension)] if extension else entry.name
        if self.find:
            stem = stem.replace(self.find, self.replacement)
        if self.case == "lowercase":
            stem = stem.lower()
        elif self.case == "UPPERCASE":
            stem = stem.upper()
        elif self.case != "Keep":
            raise ValueError("Unknown case conversion")
        number = f"{self.start + index:0{self.digits}d}" if self.number else ""
        return checked_name(f"{self.prefix}{stem}{self.suffix}{number}{extension}")


@dataclass(frozen=True)
class OrganizationRule:
    destination: str
    pattern: str = "*"
    extensions: str = ""
    minimum: int = 0
    maximum: int | None = None
    after: date | None = None
    before: date | None = None
    grouping: str = "None"

    def __post_init__(self) -> None:
        path = normalize_plan_path(self.destination)
        for part in path.parts:
            checked_name(part)
        if self.minimum < 0 or (
            self.maximum is not None and self.maximum < self.minimum
        ):
            raise ValueError("Maximum size must be at least the minimum size")
        if self.after and self.before and self.after > self.before:
            raise ValueError("The end date must be on or after the start date")
        if self.grouping not in {"None", "Extension", "Year", "Year / Month"}:
            raise ValueError("Unknown grouping")
        object.__setattr__(self, "destination", path.as_posix())

    def target(self, entry: SnapshotEntry) -> PurePosixPath | None:
        if entry.is_directory or entry.is_symlink or entry.error:
            return None
        if not fnmatchcase(entry.name.casefold(), (self.pattern or "*").casefold()):
            return None
        extensions = {
            part.strip().lower().removeprefix("*").removeprefix(".")
            for part in self.extensions.replace(",", ";").split(";")
            if part.strip()
        }
        extension = entry.path.suffix[1:].lower()
        if extensions and extension not in extensions:
            return None
        if entry.size < self.minimum or (
            self.maximum is not None and entry.size > self.maximum
        ):
            return None
        modified = None
        if self.after or self.before or self.grouping in {"Year", "Year / Month"}:
            if entry.modified_ns <= 0:
                return None
            try:
                modified = (
                    datetime.fromtimestamp(
                        entry.modified_ns / 1_000_000_000, tz=timezone.utc
                    )
                    .astimezone()
                    .date()
                )
            except (OSError, OverflowError, ValueError):
                return None
            if self.after and modified < self.after:
                return None
            if self.before and modified > self.before:
                return None
        folder = PurePosixPath(self.destination)
        if self.grouping == "Extension":
            folder /= extension or "No extension"
        elif self.grouping == "Year":
            folder /= str(modified.year)
        elif self.grouping == "Year / Month":
            folder /= f"{modified.year}/{modified.month:02d}"
        for part in folder.parts:
            checked_name(part)
        return folder / checked_name(entry.name)


def scope_entries(
    snapshot: FileSnapshot, selected: tuple[SnapshotEntry, ...] | None
) -> Iterator[SnapshotEntry | None]:
    if selected is None:
        yield from snapshot.iter_entries()
        return
    files = {entry.path for entry in selected}
    directories = {entry.path for entry in selected if entry.is_directory}
    for entry in snapshot.iter_entries():
        if entry.path in files or any(
            parent in directories for parent in entry.path.parents
        ):
            yield entry
        else:
            yield None


def batch_operations(
    snapshot: FileSnapshot,
    plan: FilePlan,
    entries: Iterable[SnapshotEntry | None],
    options: RenameOptions | OrganizationRule,
) -> Iterator[PlanOperation | None]:
    existing = plan.operations
    blocked = {
        operation.source for operation in existing if operation.source is not None
    }
    blocked_ancestors = {parent for path in blocked for parent in path.parents}
    folders = {
        operation.target
        for operation in existing
        if operation.action is PlanAction.CREATE_FOLDER
    }
    targets = {
        operation.target for operation in existing if operation.target is not None
    } - folders
    index = 0
    for entry in entries:
        if entry is None or entry.error or entry.is_symlink:
            yield None
            continue
        source = entry.path
        if (
            source in blocked
            or source in blocked_ancestors
            or any(parent in blocked for parent in source.parents)
        ):
            yield None
            continue
        if isinstance(options, RenameOptions):
            target = source.with_name(options.name(entry, index))
            index += 1
            action = PlanAction.RENAME
        else:
            target = options.target(entry)
            action = PlanAction.MOVE
        if target is None or target == source:
            yield None
            continue
        if action is PlanAction.MOVE:
            for parent in reversed(target.parents):
                if parent == PurePosixPath("."):
                    continue
                if parent in blocked or any(
                    ancestor in blocked for ancestor in parent.parents
                ):
                    raise ValueError(
                        f"Destination is already being changed in the Plan: {parent}"
                    )
                if parent in targets:
                    raise ValueError(
                        f"Destination is reserved by another operation: {parent}"
                    )
                if parent in folders:
                    continue
                current = snapshot.entry(parent)
                if current is not None:
                    if not current.is_directory or current.is_symlink or current.error:
                        raise ValueError(
                            f"Destination is not an accessible folder: {parent}"
                        )
                else:
                    folders.add(parent)
                    yield PlanOperation(PlanAction.CREATE_FOLDER, target=parent)
        yield PlanOperation(action, source=source, target=target)
