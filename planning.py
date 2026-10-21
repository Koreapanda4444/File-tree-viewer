from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path, PurePosixPath
from uuid import uuid4

from fs_paths import normalize_plan_path, normalize_root, validate_windows_name


class PlanAction(str, Enum):
    MOVE = "move"
    RENAME = "rename"
    DELETE = "delete"
    CREATE_FILE = "create_file"
    CREATE_FOLDER = "create_folder"


class ConflictPolicy(str, Enum):
    ERROR = "error"
    OVERWRITE = "overwrite"
    AUTO_RENAME = "auto_rename"
    SKIP = "skip"


@dataclass(frozen=True, slots=True)
class PlanOperation:
    action: PlanAction
    source: PurePosixPath | str | None = None
    target: PurePosixPath | str | None = None
    operation_id: str = field(default_factory=lambda: uuid4().hex)
    conflict_policy: ConflictPolicy = ConflictPolicy.ERROR

    def __post_init__(self) -> None:
        action = PlanAction(self.action)
        source = normalize_plan_path(self.source) if self.source is not None else None
        target = normalize_plan_path(self.target) if self.target is not None else None
        operation_id = self.operation_id.strip()
        conflict_policy = ConflictPolicy(self.conflict_policy)

        if validate_windows_name(operation_id) != operation_id:
            raise ValueError("Invalid operation ID")
        if not operation_id:
            raise ValueError("An operation ID is required")
        validate_operation(action, source, target)

        object.__setattr__(self, "action", action)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "target", target)
        object.__setattr__(self, "operation_id", operation_id)
        object.__setattr__(self, "conflict_policy", conflict_policy)


class FilePlan:
    def __init__(self, root: Path | str | None = None) -> None:
        self.root = normalize_root(root)
        self._operations: list[PlanOperation] = []
        self._operation_ids: set[str] = set()
        self._operations_cache: tuple[PlanOperation, ...] | None = ()
        self.revision = 0

    @property
    def operations(self) -> tuple[PlanOperation, ...]:
        if self._operations_cache is None:
            self._operations_cache = tuple(self._operations)
        return self._operations_cache

    def __len__(self) -> int:
        return len(self._operations)

    def set_root(self, root: Path | str | None) -> None:
        normalized = normalize_root(root)
        if normalized == self.root:
            return
        self.root = normalized
        if self._operations:
            self.clear()
        else:
            self.revision += 1

    def add_move(
        self,
        source: PurePosixPath | str,
        target: PurePosixPath | str,
    ) -> PlanOperation:
        return self._append(PlanAction.MOVE, source=source, target=target)

    def add_rename(
        self,
        source: PurePosixPath | str,
        target: PurePosixPath | str,
    ) -> PlanOperation:
        return self._append(PlanAction.RENAME, source=source, target=target)

    def add_delete(self, source: PurePosixPath | str) -> PlanOperation:
        return self._append(PlanAction.DELETE, source=source)

    def add_create_file(self, target: PurePosixPath | str) -> PlanOperation:
        return self._append(PlanAction.CREATE_FILE, target=target)

    def add_create_folder(self, target: PurePosixPath | str) -> PlanOperation:
        return self._append(PlanAction.CREATE_FOLDER, target=target)

    def append(self, operation: PlanOperation) -> PlanOperation:
        if operation.operation_id in self._operation_ids:
            raise ValueError(f"Duplicate operation ID: {operation.operation_id}")
        self._operations.append(operation)
        self._operation_ids.add(operation.operation_id)
        self._operations_cache = None
        self.revision += 1
        return operation

    def extend(self, operations) -> int:
        additions = tuple(operations)
        incoming_ids = [operation.operation_id for operation in additions]
        if len(incoming_ids) != len(set(incoming_ids)):
            raise ValueError("Duplicate operation IDs in added operations")
        duplicate = next(
            (
                operation_id
                for operation_id in incoming_ids
                if operation_id in self._operation_ids
            ),
            None,
        )
        if duplicate is not None:
            raise ValueError(f"Duplicate operation ID: {duplicate}")
        if not additions:
            return 0
        self._operations.extend(additions)
        self._operation_ids.update(incoming_ids)
        self._operations_cache = None
        self.revision += 1
        return len(additions)

    def update_operation(self, operation_id: str, **changes) -> PlanOperation:
        if "operation_id" in changes:
            raise ValueError("An operation ID cannot be changed")
        for position, operation in enumerate(self._operations):
            if operation.operation_id == operation_id:
                updated = replace(operation, **changes)
                if updated != operation:
                    self._operations[position] = updated
                    self._operations_cache = None
                    self.revision += 1
                return updated
        raise KeyError(operation_id)

    def set_conflict_policy(self, operation_ids, policy: ConflictPolicy | str) -> int:
        selected = set(operation_ids)
        if not selected <= self._operation_ids:
            raise KeyError("Unknown operation selected")
        policy = ConflictPolicy(policy)
        updated = [
            replace(op, conflict_policy=policy) if op.operation_id in selected else op
            for op in self._operations
        ]
        count = sum(before != after for before, after in zip(self._operations, updated))
        if count:
            self._operations = updated
            self._operations_cache = None
            self.revision += 1
        return count

    def reorder(self, operation_ids) -> None:
        order = tuple(operation_ids)
        if len(order) != len(self._operations) or set(order) != self._operation_ids:
            raise ValueError("The new order must contain every operation exactly once")
        if order == tuple(op.operation_id for op in self._operations):
            return
        by_id = {op.operation_id: op for op in self._operations}
        self._operations = [by_id[key] for key in order]
        self._operations_cache = None
        self.revision += 1

    def remove(self, operation_id: str) -> PlanOperation:
        for position, operation in enumerate(self._operations):
            if operation.operation_id == operation_id:
                removed = self._operations.pop(position)
                self._operation_ids.remove(operation_id)
                self._operations_cache = None
                self.revision += 1
                return removed
        raise KeyError(operation_id)

    def remove_many(self, operation_ids) -> int:
        requested = set(operation_ids)
        if not requested:
            return 0
        existing = requested & self._operation_ids
        if not existing:
            return 0
        self._operations = [
            operation
            for operation in self._operations
            if operation.operation_id not in existing
        ]
        self._operation_ids.difference_update(existing)
        self._operations_cache = None
        self.revision += 1
        return len(existing)

    def clear(self) -> None:
        if not self._operations:
            return
        self._operations.clear()
        self._operation_ids.clear()
        self._operations_cache = ()
        self.revision += 1

    def absolute_path(self, relative_path: PurePosixPath | str) -> Path:
        if self.root is None:
            raise ValueError("The plan root is not set")
        relative = normalize_plan_path(relative_path)
        return self.root.joinpath(*relative.parts)

    def _append(
        self,
        action: PlanAction,
        *,
        source: PurePosixPath | str | None = None,
        target: PurePosixPath | str | None = None,
    ) -> PlanOperation:
        return self.append(PlanOperation(action, source, target))


def validate_operation(
    action: PlanAction,
    source: PurePosixPath | None,
    target: PurePosixPath | None,
) -> None:
    if action in {PlanAction.MOVE, PlanAction.RENAME}:
        if source is None or target is None:
            raise ValueError(f"{action.value} requires source and target paths")
        if source == target:
            raise ValueError("Source and target paths must be different")
        if action is PlanAction.RENAME and source.parent != target.parent:
            raise ValueError("Rename operations must keep the same parent folder")
        return

    if action is PlanAction.DELETE:
        if source is None or target is not None:
            raise ValueError("Delete operations require only a source path")
        return

    if source is not None or target is None:
        raise ValueError(f"{action.value} requires only a target path")


def basic_dependencies(plan: FilePlan) -> dict[str, tuple[str, ...]]:
    sources = {}
    folders = {}
    for operation in plan.operations:
        if operation.conflict_policy is ConflictPolicy.SKIP:
            continue
        if operation.source is not None:
            sources.setdefault(operation.source, []).append(operation.operation_id)
        if operation.action is PlanAction.CREATE_FOLDER:
            folders.setdefault(operation.target, []).append(operation.operation_id)
    result = {}
    for operation in plan.operations:
        dependencies = set()
        if (
            operation.target is not None
            and operation.conflict_policy is not ConflictPolicy.SKIP
        ):
            dependencies.update(sources.get(operation.target, ()))
            for parent in operation.target.parents:
                dependencies.update(folders.get(parent, ()))
        dependencies.discard(operation.operation_id)
        result[operation.operation_id] = tuple(sorted(dependencies))
    return result
