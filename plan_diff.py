from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath

from planning import FilePlan, PlanAction, PlanOperation
from snapshot import FileSnapshot, SnapshotEntry


class ChangeKind(str, Enum):
    MOVED = "moved"
    RENAMED = "renamed"
    CREATED = "created"
    DELETED = "deleted"


class IssueKind(str, Enum):
    CONFLICT = "conflict"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class PlanChange:
    operation_id: str
    kind: ChangeKind
    source: PurePosixPath | None
    target: PurePosixPath | None


@dataclass(frozen=True, slots=True)
class SimulationIssue:
    operation_id: str
    kind: IssueKind
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
        return Counter(change.kind for change in self.changes)

    @property
    def issue_counts(self) -> Counter[IssueKind]:
        return Counter(issue.kind for issue in self.issues)

    def issues_for(self, operation_id: str) -> tuple[SimulationIssue, ...]:
        return tuple(
            issue for issue in self.issues if issue.operation_id == operation_id
        )


def simulate_plan(snapshot: FileSnapshot, plan: FilePlan) -> PlanSimulation:
    if plan.root is None or plan.root != snapshot.root:
        raise ValueError("The Plan and Snapshot roots do not match")

    operations = plan.operations
    changes = tuple(change_from_operation(operation) for operation in operations)
    issues: list[SimulationIssue] = []
    issue_keys: set[tuple[str, IssueKind, str, PurePosixPath | None]] = set()
    entry_cache: dict[PurePosixPath, SnapshotEntry | None] = {}

    def entry(path: PurePosixPath) -> SnapshotEntry | None:
        if path not in entry_cache:
            entry_cache[path] = snapshot.entry(path)
        return entry_cache[path]

    def report(
        operation: PlanOperation,
        kind: IssueKind,
        message: str,
        path: PurePosixPath | None = None,
    ) -> None:
        key = (operation.operation_id, kind, message, path)
        if key in issue_keys:
            return
        issue_keys.add(key)
        issues.append(SimulationIssue(operation.operation_id, kind, message, path))

    source_operations = tuple(
        operation for operation in operations if operation.source is not None
    )
    source_map: dict[PurePosixPath, list[PlanOperation]] = defaultdict(list)
    target_operations: dict[PurePosixPath, list[PlanOperation]] = defaultdict(list)
    created_folders: set[PurePosixPath] = set()
    vacated_paths: set[PurePosixPath] = set()

    for operation in operations:
        if operation.source is not None:
            source_map[operation.source].append(operation)
            if operation.action in {
                PlanAction.MOVE,
                PlanAction.RENAME,
                PlanAction.DELETE,
            }:
                vacated_paths.add(operation.source)
        if operation.target is not None:
            target_operations[operation.target].append(operation)
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
                "Source no longer exists in the Snapshot",
                source,
            )
        elif source_entry.error:
            report(
                operation,
                IssueKind.INVALID,
                f"Source was inaccessible: {source_entry.error}",
                source,
            )

        if len(source_map[source]) > 1:
            report(
                operation,
                IssueKind.CONFLICT,
                "Source is changed by more than one operation",
                source,
            )
        for parent in source.parents:
            for ancestor_operation in source_map.get(parent, ()):
                report(
                    operation,
                    IssueKind.CONFLICT,
                    f"An ancestor is also changed: {parent}",
                    source,
                )
                report(
                    ancestor_operation,
                    IssueKind.CONFLICT,
                    f"A descendant is also changed: {source}",
                    parent,
                )

    for operation in operations:
        target = operation.target
        if target is None:
            continue

        if len(target_operations[target]) > 1:
            report(
                operation,
                IssueKind.CONFLICT,
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
                "A folder cannot be moved inside itself",
                target,
            )

        target_entry = entry(target)
        if target_entry is not None and not path_is_vacated(target, vacated_paths):
            report(
                operation,
                IssueKind.CONFLICT,
                "Target already exists in the Snapshot",
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
                "Destination folder does not exist",
                parent,
            )
        elif not parent_entry.is_directory or parent_entry.is_symlink:
            report(
                operation,
                IssueKind.INVALID,
                "Destination parent is not a usable folder",
                parent,
            )
        elif parent_entry.error:
            report(
                operation,
                IssueKind.INVALID,
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
    )


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
