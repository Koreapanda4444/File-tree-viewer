from __future__ import annotations

import ctypes
import json
import os
import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from uuid import uuid4

from plan_diff import simulate_plan
from planning import ConflictPolicy, FilePlan, PlanAction, PlanOperation
from snapshot import FileSnapshot

ProgressCallback = Callable[[str, int, int, str], None]
INTERNAL_DIRECTORY = ".file-tree-viewer"


class ApplicationStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    ROLLED_BACK = "rolled_back"
    ROLLBACK_FAILED = "rollback_failed"


class MutationKind(str, Enum):
    STAGE_MOVE = "stage_move"
    FINAL_MOVE = "final_move"
    BACKUP_DELETE = "backup_delete"
    BACKUP_TARGET = "backup_target"
    CREATE_FILE = "create_file"
    CREATE_FOLDER = "create_folder"


class ApplicationCancelled(Exception):
    pass


@dataclass(frozen=True, slots=True)
class AppliedMutation:
    kind: MutationKind
    operation_id: str
    source: Path | None = None
    target: Path | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "kind": self.kind.value,
            "operation_id": self.operation_id,
            "source": str(self.source) if self.source is not None else None,
            "target": str(self.target) if self.target is not None else None,
        }


@dataclass(slots=True)
class PlanApplicationRecord:
    transaction_id: str
    root: Path
    transaction_directory: Path
    journal_path: Path
    mutation_log_path: Path
    started_at: str
    status: ApplicationStatus = ApplicationStatus.RUNNING
    finished_at: str | None = None
    completed_operation_ids: list[str] = field(default_factory=list)
    skipped_operation_ids: list[str] = field(default_factory=list)
    mutations: list[AppliedMutation] = field(default_factory=list)
    error: str = ""
    rollback_errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "version": 1,
            "transaction_id": self.transaction_id,
            "root": str(self.root),
            "transaction_directory": str(self.transaction_directory),
            "mutation_log": str(self.mutation_log_path),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "status": self.status.value,
            "completed_operation_ids": self.completed_operation_ids,
            "skipped_operation_ids": self.skipped_operation_ids,
            "mutation_count": len(self.mutations),
            "error": self.error,
            "rollback_errors": self.rollback_errors,
        }

    def save(self) -> None:
        temporary = self.journal_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(self.as_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, self.journal_path)


def apply_plan(
    snapshot: FileSnapshot,
    plan: FilePlan,
    *,
    cancelled: threading.Event | None = None,
    progress: ProgressCallback | None = None,
) -> PlanApplicationRecord:
    simulation = simulate_plan(snapshot, plan)
    if not simulation.can_apply:
        raise ValueError("Resolve all Plan problems before applying it")

    transaction_id = uuid4().hex
    transaction_directory = (
        snapshot.root / INTERNAL_DIRECTORY / "transactions" / transaction_id
    )
    transaction_directory.mkdir(parents=True, exist_ok=False)
    hide_internal_directory(snapshot.root / INTERNAL_DIRECTORY)
    (transaction_directory / "staging").mkdir()
    (transaction_directory / "backup").mkdir()
    record = PlanApplicationRecord(
        transaction_id=transaction_id,
        root=snapshot.root,
        transaction_directory=transaction_directory,
        journal_path=transaction_directory / "journal.json",
        mutation_log_path=transaction_directory / "mutations.jsonl",
        started_at=now_text(),
        skipped_operation_ids=[
            operation.operation_id
            for operation in plan.operations
            if operation.conflict_policy is ConflictPolicy.SKIP
        ],
    )
    record.mutation_log_path.touch(exist_ok=False)
    record.save()

    try:
        perform_plan(record, plan, cancelled=cancelled, progress=progress)
    except (ApplicationCancelled, OSError, RuntimeError, shutil.Error) as error:
        record.error = str(error)
        record.rollback_errors = rollback_mutations(record)
        record.status = (
            ApplicationStatus.ROLLBACK_FAILED
            if record.rollback_errors
            else ApplicationStatus.ROLLED_BACK
        )
    else:
        record.status = ApplicationStatus.COMPLETED
    record.finished_at = now_text()
    record.save()
    return record


def perform_plan(
    record: PlanApplicationRecord,
    plan: FilePlan,
    *,
    cancelled: threading.Event | None,
    progress: ProgressCallback | None,
) -> None:
    active = tuple(
        operation
        for operation in plan.operations
        if operation.conflict_policy is not ConflictPolicy.SKIP
    )
    total = len(active)
    by_action = {
        action: [operation for operation in active if operation.action is action]
        for action in PlanAction
    }
    staged: dict[str, Path] = {}

    movable = by_action[PlanAction.MOVE] + by_action[PlanAction.RENAME]
    for operation in movable:
        check_cancelled(cancelled)
        source = absolute_operation_path(record.root, operation.source)
        target = record.transaction_directory / "staging" / operation.operation_id
        emit_progress(progress, "Preparing moves", record, total, source)
        move_without_overwrite(source, target)
        staged[operation.operation_id] = target
        add_mutation(
            record,
            AppliedMutation(
                MutationKind.STAGE_MOVE,
                operation.operation_id,
                source,
                target,
            ),
        )

    for operation in by_action[PlanAction.DELETE]:
        check_cancelled(cancelled)
        source = absolute_operation_path(record.root, operation.source)
        target = record.transaction_directory / "backup" / operation.operation_id
        emit_progress(progress, "Backing up deletions", record, total, source)
        move_without_overwrite(source, target)
        add_mutation(
            record,
            AppliedMutation(
                MutationKind.BACKUP_DELETE,
                operation.operation_id,
                source,
                target,
            ),
        )
        check_cancelled(cancelled)
        complete_operation(record, operation)

    for operation in active:
        if operation.conflict_policy is not ConflictPolicy.OVERWRITE:
            continue
        check_cancelled(cancelled)
        target = absolute_operation_path(record.root, operation.target)
        if not path_exists(target):
            continue
        backup = (
            record.transaction_directory
            / "backup"
            / f"overwrite-{operation.operation_id}"
        )
        emit_progress(progress, "Backing up overwritten items", record, total, target)
        move_without_overwrite(target, backup)
        add_mutation(
            record,
            AppliedMutation(
                MutationKind.BACKUP_TARGET,
                operation.operation_id,
                target,
                backup,
            ),
        )

    folders = sorted(
        by_action[PlanAction.CREATE_FOLDER],
        key=lambda operation: len(operation.target.parts) if operation.target else 0,
    )
    for operation in folders:
        check_cancelled(cancelled)
        target = absolute_operation_path(record.root, operation.target)
        emit_progress(progress, "Creating folders", record, total, target)
        target.mkdir(exist_ok=False)
        add_mutation(
            record,
            AppliedMutation(
                MutationKind.CREATE_FOLDER,
                operation.operation_id,
                target=target,
            ),
        )
        check_cancelled(cancelled)
        complete_operation(record, operation)

    for operation in movable:
        check_cancelled(cancelled)
        source = staged[operation.operation_id]
        target = absolute_operation_path(record.root, operation.target)
        emit_progress(progress, "Moving items", record, total, target)
        move_without_overwrite(source, target)
        add_mutation(
            record,
            AppliedMutation(
                MutationKind.FINAL_MOVE,
                operation.operation_id,
                source,
                target,
            ),
        )
        check_cancelled(cancelled)
        complete_operation(record, operation)

    for operation in by_action[PlanAction.CREATE_FILE]:
        check_cancelled(cancelled)
        target = absolute_operation_path(record.root, operation.target)
        emit_progress(progress, "Creating files", record, total, target)
        target.touch(exist_ok=False)
        add_mutation(
            record,
            AppliedMutation(
                MutationKind.CREATE_FILE,
                operation.operation_id,
                target=target,
            ),
        )
        check_cancelled(cancelled)
        complete_operation(record, operation)


def rollback_mutations(record: PlanApplicationRecord) -> list[str]:
    errors: list[str] = []
    for mutation in reversed(record.mutations):
        try:
            reverse_mutation(mutation)
        except (OSError, RuntimeError, shutil.Error) as error:
            errors.append(f"{mutation.operation_id}: {error}")
    return errors


def reverse_mutation(mutation: AppliedMutation) -> None:
    if mutation.kind in {MutationKind.CREATE_FILE, MutationKind.CREATE_FOLDER}:
        target = required_path(mutation.target)
        if mutation.kind is MutationKind.CREATE_FILE:
            target.unlink(missing_ok=True)
        elif target.exists():
            target.rmdir()
        return

    source = required_path(mutation.source)
    target = required_path(mutation.target)
    if mutation.kind in {MutationKind.STAGE_MOVE, MutationKind.BACKUP_DELETE}:
        move_without_overwrite(target, source)
        return
    if mutation.kind is MutationKind.BACKUP_TARGET:
        move_without_overwrite(target, source)
        return
    if mutation.kind is MutationKind.FINAL_MOVE:
        move_without_overwrite(target, source)
        return
    raise ValueError(f"Unknown mutation: {mutation.kind}")


def add_mutation(record: PlanApplicationRecord, mutation: AppliedMutation) -> None:
    record.mutations.append(mutation)
    with record.mutation_log_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(mutation.as_dict(), ensure_ascii=False) + "\n")
        stream.flush()


def complete_operation(
    record: PlanApplicationRecord,
    operation: PlanOperation,
) -> None:
    record.completed_operation_ids.append(operation.operation_id)


def absolute_operation_path(root: Path, relative_path) -> Path:
    if relative_path is None:
        raise ValueError("The operation path is missing")
    return root.joinpath(*relative_path.parts)


def move_without_overwrite(source: Path, target: Path) -> None:
    if not path_exists(source):
        raise FileNotFoundError(source)
    if path_exists(target):
        raise FileExistsError(target)
    if not target.parent.is_dir():
        raise NotADirectoryError(target.parent)
    shutil.move(str(source), str(target))


def path_exists(path: Path) -> bool:
    return os.path.lexists(path)


def hide_internal_directory(path: Path) -> None:
    if os.name != "nt":
        return
    try:
        hidden_attribute = 0x02
        attributes = ctypes.windll.kernel32.GetFileAttributesW(str(path))
        if attributes != -1:
            ctypes.windll.kernel32.SetFileAttributesW(
                str(path),
                attributes | hidden_attribute,
            )
    except (AttributeError, OSError):
        return


def required_path(path: Path | None) -> Path:
    if path is None:
        raise ValueError("The recovery record is incomplete")
    return path


def check_cancelled(cancelled: threading.Event | None) -> None:
    if cancelled is not None and cancelled.is_set():
        raise ApplicationCancelled("Application cancelled")


def emit_progress(
    callback: ProgressCallback | None,
    phase: str,
    record: PlanApplicationRecord,
    total: int,
    path: Path,
) -> None:
    if callback is not None:
        callback(phase, len(record.completed_operation_ids), total, path.name)


def now_text() -> str:
    return datetime.now(timezone.utc).isoformat()
