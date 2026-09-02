from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath, PureWindowsPath
from uuid import uuid4


class PlanAction(str, Enum):
    MOVE = "move"
    RENAME = "rename"
    DELETE = "delete"
    CREATE_FILE = "create_file"
    CREATE_FOLDER = "create_folder"


@dataclass(frozen=True, slots=True)
class PlanOperation:
    action: PlanAction
    source: PurePosixPath | str | None = None
    target: PurePosixPath | str | None = None
    operation_id: str = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self) -> None:
        action = PlanAction(self.action)
        source = normalize_plan_path(self.source) if self.source is not None else None
        target = normalize_plan_path(self.target) if self.target is not None else None
        operation_id = self.operation_id.strip()

        if not operation_id:
            raise ValueError("An operation ID is required")
        validate_operation(action, source, target)

        object.__setattr__(self, "action", action)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "target", target)
        object.__setattr__(self, "operation_id", operation_id)


class FilePlan:
    def __init__(self, root: Path | str | None = None) -> None:
        self.root = normalize_root(root)
        self._operations: list[PlanOperation] = []
        self._operation_ids: set[str] = set()
        self.revision = 0

    @property
    def operations(self) -> tuple[PlanOperation, ...]:
        return tuple(self._operations)

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
        self.revision += 1
        return operation

    def remove(self, operation_id: str) -> PlanOperation:
        for position, operation in enumerate(self._operations):
            if operation.operation_id == operation_id:
                removed = self._operations.pop(position)
                self._operation_ids.remove(operation_id)
                self.revision += 1
                return removed
        raise KeyError(operation_id)

    def clear(self) -> None:
        if not self._operations:
            return
        self._operations.clear()
        self._operation_ids.clear()
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


def normalize_root(root: Path | str | None) -> Path | None:
    if root is None:
        return None
    if isinstance(root, str) and not root.strip():
        raise ValueError("The plan root cannot be empty")
    return Path(root).expanduser().absolute()


def normalize_plan_path(path: PurePosixPath | str) -> PurePosixPath:
    raw_path = str(path).replace("\\", "/")
    if not raw_path or raw_path.startswith("/") or PureWindowsPath(raw_path).drive:
        raise ValueError("Plan paths must be relative")

    parts = raw_path.split("/")
    if any(not part or part in {".", ".."} for part in parts):
        raise ValueError("Plan paths cannot contain empty, current, or parent parts")
    if any(
        "\0" in part or any(ord(character) < 32 for character in part) for part in parts
    ):
        raise ValueError("Plan paths cannot contain control characters")
    return PurePosixPath(*parts)


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
