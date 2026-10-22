from __future__ import annotations

import ctypes
import json
import os
import shutil
import stat
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from uuid import uuid4

from fs_ops import (
    copy_exclusive,
    create_exclusive,
    file_signature,
    move_no_replace,
    path_exists,
    transaction_lock,
    tree_fingerprint,
)
from fs_paths import (
    checked_boundary,
    normalized_path,
    path_is_within,
    validate_windows_name,
)
from journal import append_event, atomic_json, read_events
from plan_diff import SimulationCancelled, simulate_plan, source_live_problem
from planning import ConflictPolicy, FilePlan, PlanAction, PlanOperation
from snapshot import FileSnapshot

ProgressCallback = Callable[[str, int, int, str], None]
INTERNAL_DIRECTORY = ".file-tree-viewer"


class ApplicationStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    ROLLED_BACK = "rolled_back"
    ROLLBACK_FAILED = "rollback_failed"
    UNDOING = "undoing"
    UNDONE = "undone"
    UNDO_FAILED = "undo_failed"


class MutationKind(str, Enum):
    PREPARED_FILE = "prepared_file"
    STAGE_COPY = "stage_copy"
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
    before_signature: dict[str, int] | None = None
    after_signature: dict[str, int] | None = None

    @property
    def key(self) -> str:
        return f"{self.operation_id}:{self.kind.value}"

    def as_dict(self) -> dict[str, object]:
        return {
            "before_signature": self.before_signature,
            "after_signature": self.after_signature,
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
    undone_at: str | None = None
    completed_operation_ids: list[str] = field(default_factory=list)
    skipped_operation_ids: list[str] = field(default_factory=list)
    mutations: list[AppliedMutation] = field(default_factory=list)
    undo_state: dict[str, str] = field(default_factory=dict)
    root_identity: dict[str, int] = field(default_factory=dict)
    pending_mutations: list[AppliedMutation] = field(default_factory=list)
    reversing_mutation_ids: set[str] = field(default_factory=set)
    reversed_mutation_ids: set[str] = field(default_factory=set)
    log_sequence: int = 0
    log_valid_bytes: int = 0
    log_truncated: bool = False
    error: str = ""
    rollback_errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "version": 3,
            "undo_state": self.undo_state,
            "root_identity": self.root_identity,
            "transaction_id": self.transaction_id,
            "root": str(self.root),
            "transaction_directory": str(self.transaction_directory),
            "mutation_log": str(self.mutation_log_path),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "undone_at": self.undone_at,
            "status": self.status.value,
            "completed_operation_ids": self.completed_operation_ids,
            "skipped_operation_ids": self.skipped_operation_ids,
            "mutation_count": len(self.mutations),
            "error": self.error,
            "rollback_errors": self.rollback_errors,
        }

    def save(self) -> None:
        checked_boundary(self.root, self.journal_path, allow_leaf_link=False)
        atomic_json(self.journal_path, self.as_dict())


def apply_plan(
    snapshot: FileSnapshot,
    plan: FilePlan,
    *,
    cancelled: threading.Event | None = None,
    progress: ProgressCallback | None = None,
) -> PlanApplicationRecord:
    from recovery import inspect_recovery

    check_cancelled(cancelled)
    recovery = inspect_recovery(snapshot.root)
    if recovery.needs_attention:
        raise RuntimeError(recovery.summary())
    frozen_plan = FilePlan(plan.root)
    frozen_plan.extend(plan.operations)
    if not any(
        op.conflict_policy is not ConflictPolicy.SKIP for op in frozen_plan.operations
    ):
        raise ValueError("The Plan has no active operations")
    with transaction_lock(snapshot.root):
        return _apply_locked(
            snapshot, frozen_plan, cancelled=cancelled, progress=progress
        )


def _apply_locked(snapshot, plan, *, cancelled, progress) -> PlanApplicationRecord:
    try:
        simulation = simulate_plan(snapshot, plan, cancelled=cancelled)
    except SimulationCancelled as error:
        raise ApplicationCancelled("Application cancelled before execution") from error
    check_cancelled(cancelled)
    if not simulation.can_apply:
        raise ValueError("Resolve all Plan problems before applying it")

    record = create_application_record(snapshot.root, plan)

    try:
        perform_plan(
            record, plan, snapshot=snapshot, cancelled=cancelled, progress=progress
        )
        record.undo_state = capture_undo_state(record)
    except (
        ApplicationCancelled,
        OSError,
        RuntimeError,
        shutil.Error,
        ValueError,
        TypeError,
        KeyError,
    ) as error:
        record.error = str(error)
        record.rollback_errors = rollback_mutations(record)
        if record.pending_mutations:
            record.rollback_errors.append(
                "An unconfirmed filesystem change requires recovery review"
            )
        record.status = (
            ApplicationStatus.ROLLBACK_FAILED
            if record.rollback_errors
            else ApplicationStatus.ROLLED_BACK
        )
    else:
        record.status = ApplicationStatus.COMPLETED
    record.finished_at = now_text()
    record.save()
    if progress is not None:
        progress(
            "Application complete"
            if record.status is ApplicationStatus.COMPLETED
            else "Application stopped",
            len(record.completed_operation_ids),
            len(plan.operations),
            record.root.name,
        )
    return record


def undo_application(
    record: PlanApplicationRecord,
    *,
    progress: ProgressCallback | None = None,
) -> PlanApplicationRecord:
    if (record.transaction_directory / "sync.json").exists():
        raise ValueError(
            "Use Undo Synchronization to restore every participant root together"
        )
    with transaction_lock(record.root):
        from recovery import inspect_recovery

        scan = inspect_recovery(record.root)
        if scan.candidates:
            raise ValueError("Resolve interrupted transactions before Undo")
        newest = max(scan.completed, key=lambda item: item.started_at, default=None)
        if newest is None or newest.transaction_id != record.transaction_id:
            raise ValueError("Undo the most recent completed transaction first")
        current = load_application_record(record.journal_path)
        if current.status is not record.status:
            raise ValueError("Transaction state changed; reload recovery records")
        return _undo_locked(record, progress=progress)


def _undo_locked(record, *, progress) -> PlanApplicationRecord:
    if record.status is not ApplicationStatus.COMPLETED:
        raise ValueError("Only a completed Plan can be undone")
    validate_application_record(record)
    validate_undo_state(record)

    record.status = ApplicationStatus.UNDOING
    record.rollback_errors.clear()
    record.save()
    total = len(record.mutations)
    errors: list[str] = []
    for completed, mutation in enumerate(reversed(record.mutations)):
        path = mutation.target or mutation.source or record.root
        if progress is not None:
            progress("Undoing Plan", completed, total, path.name)
        try:
            reverse_recorded_mutation(record, mutation)
        except (OSError, RuntimeError, shutil.Error, ValueError) as error:
            errors.append(f"{mutation.operation_id}: {error}")
            break

    record.rollback_errors = errors
    record.status = (
        ApplicationStatus.UNDO_FAILED if errors else ApplicationStatus.UNDONE
    )
    record.undone_at = now_text()
    record.save()
    if progress is not None:
        progress("Undo complete", total, total, record.root.name)
    return record


def latest_completed_application(
    root: Path | str,
) -> PlanApplicationRecord | None:
    from recovery import inspect_recovery

    scan = inspect_recovery(root)
    if scan.needs_attention:
        return None
    return max(scan.completed, key=lambda record: record.started_at, default=None)


def load_application_record(journal_path: Path | str) -> PlanApplicationRecord:
    path = Path(journal_path).expanduser().absolute()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") not in {1, 2, 3}:
        raise ValueError("Unsupported recovery record version")
    transaction_id = required_text(data, "transaction_id")
    transaction_directory = Path(
        required_text(data, "transaction_directory")
    ).absolute()
    if normalized_path(transaction_directory) != normalized_path(path.parent):
        raise ValueError("Recovery record directory mismatch")
    root = Path(required_text(data, "root")).absolute()
    mutation_log_path = transaction_directory / "mutations.jsonl"
    record = PlanApplicationRecord(
        transaction_id=transaction_id,
        root=root,
        transaction_directory=transaction_directory,
        journal_path=path,
        mutation_log_path=mutation_log_path,
        started_at=required_text(data, "started_at"),
        status=ApplicationStatus(required_text(data, "status")),
        finished_at=optional_text(data.get("finished_at")),
        undone_at=optional_text(data.get("undone_at")),
        completed_operation_ids=text_list(data, "completed_operation_ids"),
        skipped_operation_ids=text_list(data, "skipped_operation_ids"),
        error=optional_text(data.get("error")) or "",
        rollback_errors=text_list(data, "rollback_errors"),
    )
    undo_state = data.get("undo_state", {})
    root_identity = data.get("root_identity", {})
    if not isinstance(undo_state, dict) or not all(
        isinstance(k, str) and isinstance(v, str) and len(v) == 64
        for k, v in undo_state.items()
    ):
        raise ValueError("Invalid Undo fingerprints")
    if not isinstance(root_identity, dict) or (
        root_identity
        and (
            set(root_identity) != {"st_dev", "st_ino", "st_mode"}
            or not all(type(value) is int for value in root_identity.values())
        )
    ):
        raise ValueError("Invalid root identity")
    record.undo_state = undo_state
    record.root_identity = root_identity
    load_record_events(record)
    validate_application_record(record)
    count = data.get("mutation_count")
    if type(count) is not int or count < 0:
        raise ValueError("Invalid recovery mutation count")
    if (
        record.status
        in {
            ApplicationStatus.COMPLETED,
            ApplicationStatus.UNDONE,
            ApplicationStatus.ROLLED_BACK,
        }
        and not record.log_truncated
        and not record.pending_mutations
        and count != len(record.mutations)
    ):
        raise ValueError("Recovery mutation count mismatch")
    return record


def mutation_from_dict(data: dict) -> AppliedMutation:
    if not isinstance(data, dict):
        raise TypeError("Invalid recovery mutation")

    def signature(key):
        value = data.get(key)
        if value is not None and (
            not isinstance(value, dict)
            or not all(isinstance(k, str) and type(v) is int for k, v in value.items())
        ):
            raise ValueError("Invalid recovery signature")
        return value

    return AppliedMutation(
        MutationKind(required_text(data, "kind")),
        required_text(data, "operation_id"),
        optional_path(data.get("source")),
        optional_path(data.get("target")),
        signature("before_signature"),
        signature("after_signature"),
    )


def load_record_events(record: PlanApplicationRecord) -> None:
    events, record.log_valid_bytes, record.log_truncated = read_events(
        record.mutation_log_path
    )
    record.log_sequence = len(events)
    pending = {}
    applied = {}
    for data in events:
        if "event" not in data:
            mutation = mutation_from_dict(data)
            if mutation.key in applied:
                raise ValueError("Duplicate legacy mutation")
            applied[mutation.key] = mutation
            continue
        record.log_sequence = data["sequence"]
        mutation = mutation_from_dict(data["mutation"])
        event = data["event"]
        if event == "intent":
            if mutation.key in pending or mutation.key in applied:
                raise ValueError("Duplicate recovery intent")
            pending[mutation.key] = mutation
        elif event == "applied":
            intent = pending.pop(mutation.key, None)
            if intent is None or (
                intent.kind,
                intent.source,
                intent.target,
                intent.before_signature,
            ) != (
                mutation.kind,
                mutation.source,
                mutation.target,
                mutation.before_signature,
            ):
                raise ValueError("Recovery completion has no matching intent")
            applied[mutation.key] = mutation
        elif event == "aborted":
            if pending.pop(mutation.key, None) is None:
                raise ValueError("Recovery abort has no matching intent")
        elif event == "reverse_intent":
            if (
                mutation.key not in applied
                or mutation.key in record.reversing_mutation_ids
                or mutation.key in record.reversed_mutation_ids
            ):
                raise ValueError("Invalid reverse intent")
            record.reversing_mutation_ids.add(mutation.key)
        elif event == "reverse_aborted":
            if mutation.key not in record.reversing_mutation_ids:
                raise ValueError("Invalid aborted reversal")
            record.reversing_mutation_ids.remove(mutation.key)
        elif event == "reversed":
            if (
                mutation.key not in applied
                or mutation.key in record.reversed_mutation_ids
            ):
                raise ValueError("Invalid recovery reversal")
            record.reversed_mutation_ids.add(mutation.key)
            record.reversing_mutation_ids.discard(mutation.key)
        else:
            raise ValueError("Unknown recovery event")
    record.mutations = list(applied.values())
    record.pending_mutations = list(pending.values())


def load_mutations(path: Path) -> list[AppliedMutation]:
    events, _, truncated = read_events(path)
    if truncated:
        raise ValueError("Incomplete mutation log")
    return [
        mutation_from_dict(data.get("mutation", data))
        for data in events
        if data.get("event") in {None, "applied"}
    ]


def perform_plan(
    record: PlanApplicationRecord,
    plan: FilePlan,
    *,
    cancelled: threading.Event | None,
    progress: ProgressCallback | None,
    snapshot: FileSnapshot,
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

    for operation in by_action[PlanAction.COPY]:
        check_cancelled(cancelled)
        source = absolute_operation_path(record.root, operation.source)
        target = record.transaction_directory / "staging" / operation.operation_id
        emit_progress(
            progress, "Copying into transaction storage", record, total, source
        )
        verify_live_source(snapshot, operation.source)
        expected = tree_fingerprint(source)
        execute_mutation(
            record,
            AppliedMutation(
                MutationKind.STAGE_COPY, operation.operation_id, source, target
            ),
        )
        if tree_fingerprint(source) != expected:
            raise FileExistsError(f"Copy source changed during preparation: {source}")
        staged[operation.operation_id] = target

    movable = by_action[PlanAction.MOVE] + by_action[PlanAction.RENAME]
    for operation in movable:
        check_cancelled(cancelled)
        source = absolute_operation_path(record.root, operation.source)
        target = record.transaction_directory / "staging" / operation.operation_id
        emit_progress(progress, "Preparing moves", record, total, source)
        verify_live_source(snapshot, operation.source)
        staged[operation.operation_id] = target
        execute_mutation(
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
        verify_live_source(snapshot, operation.source)
        execute_mutation(
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
        verify_live_source(snapshot, operation.target)
        execute_mutation(
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
        checked_boundary(record.root, target, allow_leaf_link=False)
        execute_mutation(
            record,
            AppliedMutation(
                MutationKind.CREATE_FOLDER,
                operation.operation_id,
                target=record.transaction_directory
                / "staging"
                / operation.operation_id,
            ),
        )
        execute_mutation(
            record,
            AppliedMutation(
                MutationKind.FINAL_MOVE,
                operation.operation_id,
                record.transaction_directory / "staging" / operation.operation_id,
                target,
            ),
        )
        check_cancelled(cancelled)
        complete_operation(record, operation)

    for operation in movable:
        check_cancelled(cancelled)
        source = staged[operation.operation_id]
        target = absolute_operation_path(record.root, operation.target)
        emit_progress(progress, "Moving items", record, total, target)
        execute_mutation(
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
        checked_boundary(record.root, target, allow_leaf_link=False)
        execute_mutation(
            record,
            AppliedMutation(
                MutationKind.CREATE_FILE,
                operation.operation_id,
                target=record.transaction_directory
                / "staging"
                / operation.operation_id,
            ),
            content=operation.content,
        )
        execute_mutation(
            record,
            AppliedMutation(
                MutationKind.FINAL_MOVE,
                operation.operation_id,
                record.transaction_directory / "staging" / operation.operation_id,
                target,
            ),
        )
        check_cancelled(cancelled)
        complete_operation(record, operation)

    for operation in by_action[PlanAction.COPY]:
        check_cancelled(cancelled)
        target = absolute_operation_path(record.root, operation.target)
        emit_progress(progress, "Publishing copied items", record, total, target)
        execute_mutation(
            record,
            AppliedMutation(
                MutationKind.FINAL_MOVE,
                operation.operation_id,
                staged[operation.operation_id],
                target,
            ),
        )
        check_cancelled(cancelled)
        complete_operation(record, operation)


def rollback_mutations(record: PlanApplicationRecord) -> list[str]:
    errors: list[str] = []
    for mutation in reversed(record.mutations):
        try:
            reverse_recorded_mutation(record, mutation)
        except (OSError, RuntimeError, shutil.Error, ValueError) as error:
            errors.append(f"{mutation.operation_id}: {error}")
            break
    return errors


def reverse_mutation(mutation: AppliedMutation) -> None:
    for path in (mutation.source, mutation.target):
        if path is not None:
            checked_boundary(path.parent, path)
    if mutation.kind in {MutationKind.STAGE_COPY, MutationKind.PREPARED_FILE}:
        target = required_path(mutation.target)
        move_without_overwrite(target, copied_backup_path(mutation))
        return
    if mutation.kind in {MutationKind.CREATE_FILE, MutationKind.CREATE_FOLDER}:
        target = required_path(mutation.target)
        if mutation.kind is MutationKind.CREATE_FILE:
            if path_exists(target):
                if target.stat(follow_symlinks=False).st_size:
                    move_without_overwrite(target, copied_backup_path(mutation))
                else:
                    target.unlink()
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


def log_event(
    record: PlanApplicationRecord, event: str, mutation: AppliedMutation
) -> None:
    append_event(
        record.mutation_log_path, record.log_sequence + 1, event, mutation.as_dict()
    )
    record.log_sequence += 1


def execute_mutation(
    record: PlanApplicationRecord,
    mutation: AppliedMutation,
    *,
    content: str | None = None,
    payload: Path | None = None,
) -> None:
    validate_root_identity(record)
    for path in (mutation.source, mutation.target):
        if path is not None:
            checked_boundary(record.root, path)
    if path_exists(required_path(mutation.target)):
        raise FileExistsError(mutation.target)
    if mutation.kind is MutationKind.PREPARED_FILE:
        expected_payload = (
            record.transaction_directory / "assets" / mutation.operation_id
        )
        if payload != expected_payload:
            raise ValueError("Prepared file must belong to this root transaction")
        checked_boundary(record.root, payload, allow_leaf_link=False)
        if not stat.S_ISREG(payload.lstat().st_mode):
            raise ValueError("Prepared payload is not a regular file")
    mutation = replace(
        mutation,
        before_signature=file_signature(payload)
        if mutation.kind is MutationKind.PREPARED_FILE
        else file_signature(mutation.source)
        if mutation.source
        else None,
    )
    log_event(record, "intent", mutation)
    record.pending_mutations.append(mutation)
    try:
        if mutation.kind is MutationKind.PREPARED_FILE:
            move_without_overwrite(
                required_path(payload), required_path(mutation.target)
            )
        elif mutation.kind is MutationKind.CREATE_FILE and content is not None:
            with required_path(mutation.target).open("xb") as stream:
                stream.write(content.encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
        elif mutation.kind in {MutationKind.CREATE_FILE, MutationKind.CREATE_FOLDER}:
            create_exclusive(
                required_path(mutation.target),
                directory=mutation.kind is MutationKind.CREATE_FOLDER,
            )
        elif mutation.kind is MutationKind.STAGE_COPY:
            copy_exclusive(
                required_path(mutation.source), required_path(mutation.target)
            )
        else:
            move_without_overwrite(
                required_path(mutation.source), required_path(mutation.target)
            )
    except Exception:
        unchanged = not path_exists(required_path(mutation.target)) and (
            mutation.source is None
            or file_signature(mutation.source) == mutation.before_signature
        )
        if unchanged:
            log_event(record, "aborted", mutation)
            record.pending_mutations.remove(mutation)
        raise
    applied = replace(
        mutation, after_signature=file_signature(required_path(mutation.target))
    )
    record.mutations.append(applied)
    log_event(record, "applied", applied)
    record.pending_mutations.remove(mutation)


def reverse_recorded_mutation(
    record: PlanApplicationRecord, mutation: AppliedMutation
) -> None:
    if mutation.key in record.reversed_mutation_ids:
        return
    validate_root_identity(record)
    target = required_path(mutation.target)
    if mutation.after_signature is not None and path_exists(target):
        expected = mutation.after_signature
        current = file_signature(target)
        keys = (
            ("st_dev", "st_ino", "st_mode")
            if stat.S_ISDIR(current["st_mode"])
            else tuple(key for key in expected if key != "st_ctime_ns")
        )
        if any(current.get(key) != expected.get(key) for key in keys):
            raise FileExistsError(f"Recovery source changed externally: {target}")
    log_event(record, "reverse_intent", mutation)
    record.reversing_mutation_ids.add(mutation.key)
    reverse_mutation(mutation)
    log_event(record, "reversed", mutation)
    record.reversed_mutation_ids.add(mutation.key)
    record.reversing_mutation_ids.discard(mutation.key)


def complete_operation(
    record: PlanApplicationRecord,
    operation: PlanOperation,
) -> None:
    if operation.operation_id in record.completed_operation_ids:
        raise ValueError("Operation was completed more than once")
    record.completed_operation_ids.append(operation.operation_id)
    record.save()


def absolute_operation_path(root: Path, relative_path) -> Path:
    if relative_path is None:
        raise ValueError("The operation path is missing")
    return checked_boundary(root, root.joinpath(*relative_path.parts))


def move_without_overwrite(source: Path, target: Path) -> None:
    move_no_replace(source, target, allow_cross_device=False)


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


def validate_application_record(record: PlanApplicationRecord) -> None:
    validate_root_identity(record)
    for path in record.undo_state:
        checked_boundary(record.root, Path(path))
    validate_windows_name(record.transaction_id)
    if record.transaction_id in {".", ".."}:
        raise ValueError("Invalid transaction ID")
    checked_boundary(record.root, record.transaction_directory, allow_leaf_link=False)
    expected_directory = (
        record.root / INTERNAL_DIRECTORY / "transactions" / record.transaction_id
    )
    if normalized_path(record.transaction_directory) != normalized_path(
        expected_directory
    ):
        raise ValueError("Recovery record is outside the expected transaction folder")
    if normalized_path(record.journal_path.parent) != normalized_path(
        record.transaction_directory
    ):
        raise ValueError("Recovery journal path mismatch")
    if normalized_path(record.mutation_log_path.parent) != normalized_path(
        record.transaction_directory
    ):
        raise ValueError("Recovery mutation log path mismatch")
    for mutation in (*record.mutations, *record.pending_mutations):
        validate_mutation_shape(record, mutation)
        for path in (mutation.source, mutation.target):
            if path is not None and not path_is_within(path, record.root):
                raise ValueError("Recovery mutation points outside the Plan root")
            if path is not None:
                checked_boundary(record.root, path)


def validate_undo_state(record: PlanApplicationRecord) -> None:
    for path, expected in record.undo_state.items():
        if tree_fingerprint(Path(path)) != expected:
            raise FileExistsError(f"Undo item changed externally: {path}")
    state: dict[str, Path | None] = {}
    owned_targets = {
        normalized_path(required_path(m.target))
        for m in record.mutations
        if m.kind
        in {
            MutationKind.FINAL_MOVE,
            MutationKind.CREATE_FILE,
            MutationKind.CREATE_FOLDER,
        }
    }

    def origin(path: Path) -> Path | None:
        key = normalized_path(path)
        if key not in state:
            state[key] = path if path_exists(path) else None
        return state[key]

    for mutation in reversed(record.mutations):
        if mutation.key in record.reversed_mutation_ids:
            continue
        target = required_path(mutation.target)
        actual = origin(target)
        if mutation.kind in {MutationKind.STAGE_COPY, MutationKind.PREPARED_FILE}:
            if actual is None:
                raise FileNotFoundError(f"Copied item is missing: {target}")
            if path_exists(copied_backup_path(mutation)):
                raise FileExistsError("Copied-item recovery storage is occupied")
            state[normalized_path(target)] = None
            continue
        if mutation.kind in {MutationKind.CREATE_FILE, MutationKind.CREATE_FOLDER}:
            if actual is not None:
                if mutation.kind is MutationKind.CREATE_FILE:
                    if actual.is_symlink() or not actual.is_file():
                        raise ValueError(f"Created file changed: {actual}")
                elif actual.is_symlink() or not actual.is_dir():
                    raise ValueError(f"Created folder changed type: {actual}")
                elif any(
                    normalized_path(child) not in owned_targets
                    for child in actual.iterdir()
                ):
                    raise ValueError(
                        f"Created folder contains an external item: {actual}"
                    )
            state[normalized_path(target)] = None
            continue
        source = required_path(mutation.source)
        if actual is None:
            raise FileNotFoundError(f"Undo source is missing: {target}")
        if origin(source) is not None:
            raise FileExistsError(f"Undo destination already exists: {source}")
        state[normalized_path(target)] = None
        state[normalized_path(source)] = actual


def required_text(data: dict[str, object], key: str) -> str:
    value = data[key]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Recovery field is missing: {key}")
    return value


def optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("Invalid recovery text field")
    return value


def text_list(data: dict[str, object], key: str) -> list[str]:
    value = data.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"Invalid recovery list: {key}")
    return list(value)


def optional_path(value: object) -> Path | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError("Invalid recovery path")
    return Path(value).absolute()


def verify_live_source(snapshot: FileSnapshot, path) -> None:
    if path is None:
        raise ValueError("Missing source path")
    entry = snapshot.entry(path)
    if entry is None:
        raise FileExistsError(f"Item was added after the Snapshot: {path}")
    problem = source_live_problem(snapshot, entry)
    if problem:
        raise FileExistsError(f"{path}: {problem}")


def validate_mutation_shape(
    record: PlanApplicationRecord, mutation: AppliedMutation
) -> None:
    from fs_paths import normalize_plan_path

    validate_windows_name(mutation.operation_id)
    for signature in (mutation.before_signature, mutation.after_signature):
        if signature is not None and set(signature) != {
            "st_dev",
            "st_ino",
            "st_mode",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        }:
            raise ValueError("Incomplete recovery identity")
    directory = record.transaction_directory
    stage = directory / "staging" / mutation.operation_id
    backup = directory / "backup" / mutation.operation_id
    if mutation.kind in {MutationKind.STAGE_MOVE, MutationKind.STAGE_COPY}:
        internal, expected, user_path = mutation.target, stage, mutation.source
    elif mutation.kind is MutationKind.FINAL_MOVE:
        internal, expected, user_path = mutation.source, stage, mutation.target
    elif mutation.kind is MutationKind.BACKUP_DELETE:
        internal, expected, user_path = mutation.target, backup, mutation.source
    elif mutation.kind is MutationKind.BACKUP_TARGET:
        internal, expected, user_path = (
            mutation.target,
            directory / "backup" / f"overwrite-{mutation.operation_id}",
            mutation.source,
        )
    else:
        if mutation.source is not None:
            raise ValueError("Created-item recovery cannot have a source")
        if mutation.target == stage:
            return
        internal, expected, user_path = None, None, mutation.target
    if internal != expected or user_path is None:
        raise ValueError("Recovery mutation paths do not match their purpose")
    normalize_plan_path(user_path.relative_to(record.root).as_posix())


def validate_root_identity(record: PlanApplicationRecord) -> None:
    if record.root_identity:
        current = file_signature(record.root)
        if any(
            current.get(key) != value for key, value in record.root_identity.items()
        ):
            raise FileExistsError(
                "The transaction root was replaced; recovery needs manual review"
            )


def capture_undo_state(record: PlanApplicationRecord) -> dict[str, str]:
    paths = {
        mutation.target
        for mutation in record.mutations
        if mutation.target is not None and path_exists(mutation.target)
    }
    roots = [
        path for path in paths if not any(parent in paths for parent in path.parents)
    ]
    return {str(path): tree_fingerprint(path) for path in roots}


def copied_backup_path(mutation: AppliedMutation) -> Path:
    return (
        required_path(mutation.target).parent.parent
        / "backup"
        / f"copy-{mutation.operation_id}"
    )


def create_application_record(root: Path, plan=None, *, transaction_id=None):
    transaction_id = transaction_id or uuid4().hex
    transaction_directory = root / INTERNAL_DIRECTORY / "transactions" / transaction_id
    checked_boundary(root, transaction_directory, allow_leaf_link=False)
    transaction_directory.mkdir(parents=True, exist_ok=False)
    hide_internal_directory(root / INTERNAL_DIRECTORY)
    (transaction_directory / "staging").mkdir()
    (transaction_directory / "backup").mkdir()
    record = PlanApplicationRecord(
        transaction_id=transaction_id,
        root=root,
        root_identity={
            key: file_signature(root)[key] for key in ("st_dev", "st_ino", "st_mode")
        },
        transaction_directory=transaction_directory,
        journal_path=transaction_directory / "journal.json",
        mutation_log_path=transaction_directory / "mutations.jsonl",
        started_at=now_text(),
        skipped_operation_ids=[
            operation.operation_id
            for operation in (plan.operations if plan is not None else ())
            if operation.conflict_policy is ConflictPolicy.SKIP
        ],
    )
    record.mutation_log_path.touch(exist_ok=False)
    record.save()

    return record
