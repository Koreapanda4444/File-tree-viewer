from __future__ import annotations

import os
import stat
from collections import Counter, defaultdict
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath

from planning import ConflictPolicy, FilePlan, PlanAction, PlanOperation
from snapshot import FileSnapshot, SnapshotEntry


class ChangeKind(str, Enum):
    MOVED = "moved"
    RENAMED = "renamed"
    CREATED = "created"
    DELETED = "deleted"


class IssueKind(str, Enum):
    CONFLICT = "conflict"
    INVALID = "invalid"


class IssueCode(str, Enum):
    SOURCE_MISSING = "source_missing"
    SOURCE_INACCESSIBLE = "source_inaccessible"
    SOURCE_CHANGED = "source_changed"
    SOURCE_DUPLICATE = "source_duplicate"
    SOURCE_OVERLAP = "source_overlap"
    TARGET_DUPLICATE = "target_duplicate"
    TARGET_EXISTS = "target_exists"
    SELF_MOVE = "self_move"
    PARENT_CHANGED = "parent_changed"
    PARENT_MISSING = "parent_missing"
    PARENT_INVALID = "parent_invalid"
    PARENT_INACCESSIBLE = "parent_inaccessible"


@dataclass(frozen=True, slots=True)
class PlanChange:
    operation_id: str
    kind: ChangeKind
    source: PurePosixPath | None
    target: PurePosixPath | None
    conflict_policy: ConflictPolicy

    @property
    def skipped(self) -> bool:
        return self.conflict_policy is ConflictPolicy.SKIP


@dataclass(frozen=True, slots=True)
class SimulationIssue:
    operation_id: str
    kind: IssueKind
    code: IssueCode
    message: str
    path: PurePosixPath | None = None


@dataclass(frozen=True, slots=True)
class PlanSimulation:
    changes: tuple[PlanChange, ...]
    issues: tuple[SimulationIssue, ...]

    @property
    def can_apply(self) -> bool:
        return not self.issues

    @property
    def change_counts(self) -> Counter[ChangeKind]:
        return Counter(change.kind for change in self.changes if not change.skipped)

    @property
    def skipped_count(self) -> int:
        return sum(change.skipped for change in self.changes)

    @property
    def issue_counts(self) -> Counter[IssueKind]:
        return Counter(issue.kind for issue in self.issues)

    def issues_for(self, operation_id: str) -> tuple[SimulationIssue, ...]:
        return tuple(
            issue for issue in self.issues if issue.operation_id == operation_id
        )


def simulate_plan(
    snapshot: FileSnapshot,
    plan: FilePlan,
    *,
    validate_live: bool = True,
) -> PlanSimulation:
    if plan.root is None or plan.root != snapshot.root:
        raise ValueError("The Plan and Snapshot roots do not match")

    operations = plan.operations
    changes = tuple(change_from_operation(operation) for operation in operations)
    issues: list[SimulationIssue] = []
    issue_keys: set[
        tuple[str, IssueKind, IssueCode, str, PurePosixPath | None]
    ] = set()
    entry_cache: dict[PurePosixPath, SnapshotEntry | None] = {}

    def entry(path: PurePosixPath) -> SnapshotEntry | None:
        if path not in entry_cache:
            entry_cache[path] = snapshot.entry(path)
        return entry_cache[path]

    def report(
        operation: PlanOperation,
        kind: IssueKind,
        code: IssueCode,
        message: str,
        path: PurePosixPath | None = None,
    ) -> None:
        key = (operation.operation_id, kind, code, message, path)
        if key in issue_keys:
            return
        issue_keys.add(key)
        issues.append(
            SimulationIssue(operation.operation_id, kind, code, message, path)
        )

    active_operations = tuple(
        operation
        for operation in operations
        if operation.conflict_policy is not ConflictPolicy.SKIP
    )
    source_operations = tuple(
        operation
        for operation in active_operations
        if operation.source is not None
    )
    source_map: dict[PurePosixPath, list[PlanOperation]] = defaultdict(list)
    target_operations: dict[str, list[PlanOperation]] = defaultdict(list)
    created_folders: set[PurePosixPath] = set()
    vacated_paths: set[PurePosixPath] = set()

    for operation in active_operations:
        if operation.source is not None:
            source_map[operation.source].append(operation)
            if operation.action in {
                PlanAction.MOVE,
                PlanAction.RENAME,
                PlanAction.DELETE,
            }:
                vacated_paths.add(operation.source)
        if operation.target is not None:
            target_operations[path_collision_key(operation.target)].append(operation)
            if operation.action is PlanAction.CREATE_FOLDER:
                created_folders.add(operation.target)

    for operation in source_operations:
        source = operation.source
        if source is None:
            continue
        source_entry = entry(source)
        if source_entry is None:
            report(
                operation,
                IssueKind.INVALID,
                IssueCode.SOURCE_MISSING,
                "Source no longer exists in the Snapshot",
                source,
            )
        elif source_entry.error:
            report(
                operation,
                IssueKind.INVALID,
                IssueCode.SOURCE_INACCESSIBLE,
                f"Source was inaccessible: {source_entry.error}",
                source,
            )
        elif validate_live:
            live_problem = source_live_problem(snapshot, source_entry)
            if live_problem is not None:
                report(
                    operation,
                    IssueKind.CONFLICT,
                    IssueCode.SOURCE_CHANGED,
                    live_problem,
                    source,
                )

        if len(source_map[source]) > 1:
            report(
                operation,
                IssueKind.CONFLICT,
                IssueCode.SOURCE_DUPLICATE,
                "Source is changed by more than one operation",
                source,
            )
        for parent in source.parents:
            for ancestor_operation in source_map.get(parent, ()):
                report(
                    operation,
                    IssueKind.CONFLICT,
                    IssueCode.SOURCE_OVERLAP,
                    f"An ancestor is also changed: {parent}",
                    source,
                )
                report(
                    ancestor_operation,
                    IssueKind.CONFLICT,
                    IssueCode.SOURCE_OVERLAP,
                    f"A descendant is also changed: {source}",
                    parent,
                )

    for operation in active_operations:
        target = operation.target
        if target is None:
            continue

        if len(target_operations[path_collision_key(target)]) > 1:
            report(
                operation,
                IssueKind.CONFLICT,
                IssueCode.TARGET_DUPLICATE,
                "More than one operation produces this path",
                target,
            )

        source = operation.source
        source_entry = entry(source) if source is not None else None
        if (
            operation.action is PlanAction.MOVE
            and source is not None
            and source_entry is not None
            and source_entry.is_directory
            and target.is_relative_to(source)
        ):
            report(
                operation,
                IssueKind.INVALID,
                IssueCode.SELF_MOVE,
                "A folder cannot be moved inside itself",
                target,
            )

        target_entry = entry(target)
        target_exists = (
            live_path_exists(plan, target)
            if validate_live
            else target_entry is not None
        )
        if (
            target_exists
            and not path_is_vacated(target, vacated_paths)
            and operation.conflict_policy is not ConflictPolicy.OVERWRITE
        ):
            report(
                operation,
                IssueKind.CONFLICT,
                IssueCode.TARGET_EXISTS,
                "Target already exists in the current filesystem",
                target,
            )

        parent = target.parent
        if parent == PurePosixPath("."):
            continue
        blocking_source = changed_ancestor(parent, vacated_paths)
        if blocking_source is not None:
            report(
                operation,
                IssueKind.CONFLICT,
                IssueCode.PARENT_CHANGED,
                "Destination folder is changed by another operation: "
                f"{blocking_source}",
                parent,
            )
            continue
        if folder_is_planned(parent, created_folders):
            continue
        parent_entry = entry(parent)
        if parent_entry is None:
            report(
                operation,
                IssueKind.INVALID,
                IssueCode.PARENT_MISSING,
                "Destination folder does not exist",
                parent,
            )
        elif not parent_entry.is_directory or parent_entry.is_symlink:
            report(
                operation,
                IssueKind.INVALID,
                IssueCode.PARENT_INVALID,
                "Destination parent is not a usable folder",
                parent,
            )
        elif parent_entry.error:
            report(
                operation,
                IssueKind.INVALID,
                IssueCode.PARENT_INACCESSIBLE,
                f"Destination folder was inaccessible: {parent_entry.error}",
                parent,
            )

    return PlanSimulation(changes, tuple(issues))


def change_from_operation(operation: PlanOperation) -> PlanChange:
    if operation.action is PlanAction.MOVE:
        kind = ChangeKind.MOVED
    elif operation.action is PlanAction.RENAME:
        kind = ChangeKind.RENAMED
    elif operation.action is PlanAction.DELETE:
        kind = ChangeKind.DELETED
    else:
        kind = ChangeKind.CREATED
    return PlanChange(
        operation.operation_id,
        kind,
        operation.source,
        operation.target,
        operation.conflict_policy,
    )


def source_live_problem(
    snapshot: FileSnapshot,
    entry: SnapshotEntry,
) -> str | None:
    path = snapshot.root.joinpath(*entry.path.parts)
    try:
        details = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return "Source was removed after the Snapshot was created"
    except OSError as error:
        return f"Source cannot be checked against the Snapshot: {error}"

    is_directory = stat.S_ISDIR(details.st_mode)
    reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    file_attributes = getattr(details, "st_file_attributes", 0)
    is_symlink = stat.S_ISLNK(details.st_mode) or bool(
        reparse_attribute and file_attributes & reparse_attribute
    )
    modified_ns = getattr(
        details,
        "st_mtime_ns",
        int(details.st_mtime * 1_000_000_000),
    )
    identity = f"{details.st_dev}:{details.st_ino}" if details.st_ino else None
    changed = (
        is_directory != entry.is_directory
        or is_symlink != entry.is_symlink
        or (not is_directory and details.st_size != entry.size)
        or modified_ns != entry.modified_ns
        or (entry.identity is not None and identity != entry.identity)
    )
    return "Source changed after the Snapshot was created" if changed else None


def live_path_exists(plan: FilePlan, path: PurePosixPath) -> bool:
    try:
        return os.path.lexists(plan.absolute_path(path))
    except OSError:
        return True


def path_collision_key(path: PurePosixPath) -> str:
    value = path.as_posix()
    return value.casefold() if os.name == "nt" else value


def path_is_vacated(
    path: PurePosixPath,
    vacated_paths: set[PurePosixPath],
) -> bool:
    return path in vacated_paths or any(
        parent in vacated_paths for parent in path.parents
    )


def changed_ancestor(
    path: PurePosixPath,
    vacated_paths: set[PurePosixPath],
) -> PurePosixPath | None:
    if path in vacated_paths:
        return path
    return next((parent for parent in path.parents if parent in vacated_paths), None)


def folder_is_planned(
    path: PurePosixPath,
    created_folders: set[PurePosixPath],
) -> bool:
    return path in created_folders
