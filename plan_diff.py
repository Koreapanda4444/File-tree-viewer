from __future__ import annotations

import os
import stat
import threading
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import PurePosixPath

from fs_paths import checked_boundary, is_link_stat, path_collision_key
from planning import ConflictPolicy, FilePlan, PlanAction, PlanOperation
from snapshot import FileSnapshot, SnapshotEntry

SIMULATION_PROGRESS_INTERVAL = 512
SimulationProgress = Callable[[str, int, int], None]


class SimulationCancelled(Exception):
    pass


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
    _change_counts: Counter[ChangeKind] = field(init=False, repr=False, compare=False)
    _issue_counts: Counter[IssueKind] = field(init=False, repr=False, compare=False)
    _skipped_count: int = field(init=False, repr=False, compare=False)
    _issues_by_operation: dict[str, tuple[SimulationIssue, ...]] = field(
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        grouped: dict[str, list[SimulationIssue]] = defaultdict(list)
        for issue in self.issues:
            grouped[issue.operation_id].append(issue)
        object.__setattr__(
            self,
            "_change_counts",
            Counter(change.kind for change in self.changes if not change.skipped),
        )
        object.__setattr__(self, "_issue_counts", Counter(x.kind for x in self.issues))
        object.__setattr__(
            self,
            "_skipped_count",
            sum(change.skipped for change in self.changes),
        )
        object.__setattr__(
            self,
            "_issues_by_operation",
            {key: tuple(value) for key, value in grouped.items()},
        )

    @property
    def can_apply(self) -> bool:
        return not self.issues

    @property
    def change_counts(self) -> Counter[ChangeKind]:
        return self._change_counts.copy()

    @property
    def skipped_count(self) -> int:
        return self._skipped_count

    @property
    def issue_counts(self) -> Counter[IssueKind]:
        return self._issue_counts.copy()

    def issues_for(self, operation_id: str) -> tuple[SimulationIssue, ...]:
        return self._issues_by_operation.get(operation_id, ())


def simulate_plan(
    snapshot: FileSnapshot,
    plan: FilePlan,
    *,
    validate_live: bool = True,
    cancelled: threading.Event | None = None,
    progress: SimulationProgress | None = None,
) -> PlanSimulation:
    if plan.root is None or plan.root != snapshot.root:
        raise ValueError("The Plan and Snapshot roots do not match")

    operations = plan.operations
    total = len(operations)
    check_simulation_cancelled(cancelled)
    emit_simulation_progress(progress, "Preparing Plan simulation", 0, total)
    changes = tuple(change_from_operation(operation) for operation in operations)
    issues: list[SimulationIssue] = []
    issue_keys: set[tuple[str, IssueKind, IssueCode, str, PurePosixPath | None]] = set()

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
        operation for operation in active_operations if operation.source is not None
    )
    source_map: dict[PurePosixPath, list[PlanOperation]] = defaultdict(list)
    target_operations: dict[str, list[PlanOperation]] = defaultdict(list)
    created_folders: set[PurePosixPath] = set()
    vacated_paths: set[PurePosixPath] = set()
    lookup_paths: set[PurePosixPath] = set()

    for position, operation in enumerate(active_operations, start=1):
        simulation_checkpoint(
            cancelled,
            progress,
            "Indexing planned paths",
            position,
            total,
        )
        if operation.source is not None:
            source_map[operation.source].append(operation)
            lookup_paths.add(operation.source)
            if operation.action in {
                PlanAction.MOVE,
                PlanAction.RENAME,
                PlanAction.DELETE,
            }:
                vacated_paths.add(operation.source)
        if operation.target is not None:
            lookup_paths.add(operation.target)
            if operation.target.parent != PurePosixPath("."):
                lookup_paths.add(operation.target.parent)
            target_operations[path_collision_key(operation.target)].append(operation)
            if operation.action is PlanAction.CREATE_FOLDER:
                created_folders.add(operation.target)

    check_simulation_cancelled(cancelled)
    emit_simulation_progress(
        progress,
        "Loading Snapshot metadata",
        0,
        len(lookup_paths),
    )
    entry_cache = snapshot.entries_by_path(
        lookup_paths,
        normalized=True,
        checkpoint=lambda: check_simulation_cancelled(cancelled),
    )
    file_targets = {
        path_collision_key(operation.target)
        for operation in active_operations
        if operation.target is not None
        and (
            operation.action is PlanAction.CREATE_FILE
            or (
                operation.source is not None
                and (source_entry := entry_cache.get(operation.source)) is not None
                and (not source_entry.is_directory or source_entry.is_symlink)
            )
        )
    }
    reported_ancestors: set[PurePosixPath] = set()
    root_problem = None
    if validate_live:
        try:
            checked_boundary(snapshot.root, snapshot.root, allow_leaf_link=False)
        except (OSError, ValueError) as error:
            root_problem = str(error)
    live_source_cache: dict[PurePosixPath, str | None] = {}
    live_target_cache: dict[PurePosixPath, bool] = {}
    ancestor_cache: dict[PurePosixPath, PurePosixPath | None] = {}
    parent_cache: dict[PurePosixPath, str | None] = {}
    vacated_keys = {path_collision_key(path): path for path in vacated_paths}

    def vacated_ancestor(path: PurePosixPath) -> PurePosixPath | None:
        if path not in ancestor_cache:
            ancestor_cache[path] = next(
                (
                    vacated_keys[key]
                    for part in (path, *path.parents)
                    if (key := path_collision_key(part)) in vacated_keys
                ),
                None,
            )
        return ancestor_cache[path]

    def parent_problem(path: PurePosixPath) -> str | None:
        for part in reversed((path, *path.parents)):
            if part == PurePosixPath("."):
                continue
            if part not in parent_cache:
                try:
                    details = plan.absolute_path(part).lstat()
                    parent_cache[part] = (
                        None
                        if stat.S_ISDIR(details.st_mode) and not is_link_stat(details)
                        else f"Destination ancestor is not a usable folder: {part}"
                    )
                except OSError as error:
                    parent_cache[part] = (
                        f"Destination ancestor cannot be checked: {part}: {error}"
                    )
            if parent_cache[part] is not None:
                return parent_cache[part]
        return None

    def entry(path: PurePosixPath) -> SnapshotEntry | None:
        return entry_cache.get(path)

    for position, operation in enumerate(source_operations, start=1):
        simulation_checkpoint(
            cancelled,
            progress,
            "Checking planned sources",
            position,
            len(source_operations),
        )
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
            if source not in live_source_cache:
                live_source_cache[source] = source_live_problem(snapshot, source_entry)
            live_problem = live_source_cache[source]
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
            ancestor_operations = source_map.get(parent, ())
            if not ancestor_operations:
                continue
            report(
                operation,
                IssueKind.CONFLICT,
                IssueCode.SOURCE_OVERLAP,
                f"An ancestor is also changed: {parent}",
                source,
            )
            if parent not in reported_ancestors:
                reported_ancestors.add(parent)
                for ancestor_operation in ancestor_operations:
                    report(
                        ancestor_operation,
                        IssueKind.CONFLICT,
                        IssueCode.SOURCE_OVERLAP,
                        f"One or more descendants are also changed, including: {source}",
                        parent,
                    )

    for position, operation in enumerate(active_operations, start=1):
        simulation_checkpoint(
            cancelled,
            progress,
            "Checking planned destinations",
            position,
            len(active_operations),
        )
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
        if validate_live and target not in live_target_cache:
            live_target_cache[target] = live_path_exists(plan, target)
        target_exists = (
            live_target_cache[target] if validate_live else target_entry is not None
        )
        if (
            target_exists
            and vacated_ancestor(target) is None
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
        blocker = next(
            (
                ancestor
                for ancestor in target.parents
                if path_collision_key(ancestor) in file_targets
            ),
            None,
        )
        if blocker is not None:
            report(
                operation,
                IssueKind.INVALID,
                IssueCode.PARENT_INVALID,
                f"A planned file is used as a destination folder: {blocker}",
                blocker,
            )
            continue
        if root_problem is not None:
            report(
                operation, IssueKind.CONFLICT, IssueCode.PARENT_CHANGED, root_problem
            )
            continue
        if parent == PurePosixPath("."):
            continue
        blocking_source = vacated_ancestor(parent)
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
        if validate_live and parent_entry is not None:
            problem = parent_problem(parent)
            if problem is not None:
                report(
                    operation,
                    IssueKind.CONFLICT,
                    IssueCode.PARENT_CHANGED,
                    problem,
                    parent,
                )
                continue
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

    check_simulation_cancelled(cancelled)
    emit_simulation_progress(progress, "Simulation complete", total, total)
    return PlanSimulation(changes, tuple(issues))


def simulation_checkpoint(
    cancelled: threading.Event | None,
    progress: SimulationProgress | None,
    message: str,
    completed: int,
    total: int,
) -> None:
    if completed % SIMULATION_PROGRESS_INTERVAL != 0 and completed != total:
        return
    check_simulation_cancelled(cancelled)
    emit_simulation_progress(progress, message, completed, total)


def check_simulation_cancelled(cancelled: threading.Event | None) -> None:
    if cancelled is not None and cancelled.is_set():
        raise SimulationCancelled


def emit_simulation_progress(
    progress: SimulationProgress | None,
    message: str,
    completed: int,
    total: int,
) -> None:
    if progress is not None:
        progress(message, completed, total)


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
        checked_boundary(snapshot.root, path)
        details = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return "Source was removed after the Snapshot was created"
    except (OSError, ValueError) as error:
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
