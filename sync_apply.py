from __future__ import annotations

import hashlib
import itertools
import json
import os
import sqlite3
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from file_content import open_readonly_file
from file_hashing import FileHashCache
from folder_compare import compare_folders, root_identity
from fs_ops import copy_exclusive, path_exists, transaction_lock, tree_fingerprint
from fs_paths import (
    checked_boundary,
    normalize_root,
    normalized_path,
    path_is_within,
    validate_windows_name,
)
from journal import atomic_json
from plan_apply import (
    ApplicationStatus,
    AppliedMutation,
    MutationKind,
    capture_undo_state,
    create_application_record,
    execute_mutation,
    load_application_record,
    now_text,
    reverse_recorded_mutation,
    validate_application_record,
    validate_undo_state,
)
from snapshot import SnapshotCancelled
from snapshot_store import check_cancelled
from sync_baseline import baseline_path, save_sync_baseline
from sync_plan import SyncAction, SyncOperation

TERMINAL_STATES = {"completed", "rolled_back", "undone"}


@dataclass(frozen=True, slots=True)
class SyncApplication:
    transaction_id: str
    roots: tuple[Path, ...]
    status: str
    journals: tuple[Path, ...]
    completed: int
    error: str = ""
    recovery_errors: tuple[str, ...] = ()


def notify(progress, phase, done, total, name=""):
    if progress:
        progress(phase, done, total, name)


def inventory_rows(comparison, side):
    data, digest = (
        ("before_data", "left_digest") if side == 0 else ("after_data", "right_digest")
    )
    for path, raw, hash_value in comparison.database.execute(
        f"SELECT COALESCE(source,target),{data},{digest} FROM changes WHERE {data} IS NOT NULL ORDER BY COALESCE(source,target)"
    ):
        yield path, {**json.loads(raw), "digest": hash_value}


def revalidate_plan(plan, cache, *, cancelled=None, progress=None):
    if tuple(root_identity(root) for root in plan.roots) != plan.identities:
        raise FileExistsError("A synchronization root was replaced")
    fresh = compare_folders(
        *plan.roots,
        verify_content=True,
        hash_cache=cache,
        cancelled=cancelled,
        progress=lambda message, count: notify(progress, message, count, 0),
    )
    try:
        if fresh.error_count or fresh.counts.get("error", 0):
            raise FileExistsError("A synchronization root is no longer fully readable")
        for side in (0, 1):
            previous = (
                (path, json.loads(raw))
                for path, raw in plan.database.execute(
                    "SELECT path,data FROM inventory WHERE side=? ORDER BY path",
                    (side,),
                )
            )
            if any(
                old != new
                for old, new in itertools.zip_longest(
                    previous, inventory_rows(fresh, side)
                )
            ):
                raise FileExistsError(
                    f"Folder contents changed since review: {plan.roots[side]}; compare and stage again"
                )
    finally:
        fresh.close()


def hash_payload(path, *, cancelled=None):
    digest = hashlib.sha256()
    with open_readonly_file(path) as (stream, _details, _signature, _linked):
        while data := stream.read(1024 * 1024):
            check_cancelled(cancelled)
            digest.update(data)
    return digest.hexdigest()


def pending_path(roots):
    return baseline_path(roots).with_suffix(".pending")


def set_pending(roots, transaction_id):
    path = pending_path(roots)
    checked_boundary(roots[0], path, allow_leaf_link=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path_exists(path):
        raise ValueError("An earlier synchronization baseline requires recovery")
    atomic_json(path, {"transaction_id": transaction_id})


def clear_pending(roots, transaction_id, *, invalidate=False):
    path = pending_path(roots)
    checked_boundary(roots[0], path, allow_leaf_link=False)
    if path_exists(path):
        if json.loads(path.read_text()) != {"transaction_id": transaction_id}:
            raise ValueError("Synchronization baseline marker changed")
        if invalidate:
            baseline = baseline_path(roots)
            checked_boundary(roots[0], baseline, allow_leaf_link=False)
            baseline.unlink(missing_ok=True)
        path.unlink()


def save_group(records, descriptor, state):
    data = {**descriptor, "state": state}
    for record in records:
        path = record.transaction_directory / "sync.json"
        checked_boundary(record.root, path, allow_leaf_link=False)
        atomic_json(path, data)


def load_group(marker):
    marker = Path(marker).absolute()
    if len(marker.parents) < 4:
        raise ValueError("Invalid synchronization marker path")
    checked_boundary(marker.parents[3], marker, allow_leaf_link=False)
    if marker.stat().st_size > 32768:
        raise ValueError("Oversized synchronization descriptor")
    descriptor = json.loads(marker.read_text(encoding="utf-8"))
    if (
        not isinstance(descriptor, dict)
        or descriptor.get("version") != 1
        or set(descriptor) != {"version", "id", "roots", "identities", "state"}
    ):
        raise ValueError("Invalid synchronization descriptor")
    transaction_id = validate_windows_name(descriptor["id"])
    if len(transaction_id) != 32 or any(
        char not in "0123456789abcdef" for char in transaction_id
    ):
        raise ValueError("Invalid synchronization transaction ID")
    roots = tuple(normalize_root(root) for root in descriptor["roots"])
    if (
        len(roots) != 2
        or any(root is None for root in roots)
        or path_is_within(roots[0], roots[1])
        or path_is_within(roots[1], roots[0])
    ):
        raise ValueError("Invalid synchronization roots")
    if str(marker) not in {
        str(root / ".file-tree-viewer" / "transactions" / transaction_id / "sync.json")
        for root in roots
    }:
        raise ValueError("Synchronization descriptor is outside its participant roots")
    if descriptor["state"] not in TERMINAL_STATES | {
        "running",
        "undoing",
        "recovery_failed",
    }:
        raise ValueError("Invalid synchronization state")
    if [root_identity(root) for root in roots] != descriptor["identities"]:
        raise ValueError("A synchronization participant root changed")
    records = []
    states = []
    for root in roots:
        directory = root / ".file-tree-viewer" / "transactions" / transaction_id
        checked_boundary(root, directory, allow_leaf_link=False)
        record = load_application_record(directory / "journal.json")
        if record.root != root or record.transaction_id != transaction_id:
            raise ValueError("Synchronization participant journal mismatch")
        other = directory / "sync.json"
        checked_boundary(root, other, allow_leaf_link=False)
        if other.exists():
            if other.stat().st_size > 32768:
                raise ValueError("Oversized participant descriptor")
            value = json.loads(other.read_text(encoding="utf-8"))
            if {key: val for key, val in value.items() if key != "state"} != {
                key: val for key, val in descriptor.items() if key != "state"
            }:
                raise ValueError("Synchronization participant descriptors disagree")
            if value.get("state") not in TERMINAL_STATES | {
                "running",
                "undoing",
                "recovery_failed",
            }:
                raise ValueError("Invalid participant synchronization state")
            states.append(value["state"])
        elif record.mutations or record.pending_mutations:
            raise ValueError(
                "Missing descriptor for a changed synchronization participant"
            )
        else:
            states.append("running")
        records.append(record)
    return descriptor, tuple(records), tuple(states)


def synchronization_problem(record):
    marker = record.transaction_directory / "sync.json"
    if not path_exists(marker):
        return ""
    try:
        descriptor, records, states = load_group(marker)
        expected = {
            "completed": ApplicationStatus.COMPLETED,
            "rolled_back": ApplicationStatus.ROLLED_BACK,
            "undone": ApplicationStatus.UNDONE,
        }
        state = descriptor["state"]
        if (
            state not in TERMINAL_STATES
            or any(value != state for value in states)
            or any(
                item.status is not expected[state]
                or item.pending_mutations
                or item.reversing_mutation_ids
                or item.log_truncated
                for item in records
            )
        ):
            return "Interrupted multi-root synchronization; recover every participant together"
        if path_exists(pending_path(tuple(item.root for item in records))):
            return "Synchronization baseline commit is unfinished; coordinated recovery is required"
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        RuntimeError,
        sqlite3.Error,
    ) as error:
        return f"Invalid synchronization recovery group: {error}"
    return ""


def apply_sync_plan(plan, *, cancelled=None, progress=None):
    from recovery import inspect_recovery

    if not plan.can_apply:
        raise ValueError("Resolve every synchronization issue before applying")
    if path_exists(pending_path(plan.roots)):
        raise ValueError("An earlier synchronization baseline requires recovery")
    if any(inspect_recovery(root).needs_attention for root in plan.roots):
        raise ValueError(
            "Resolve unfinished transactions on both roots before synchronization"
        )
    cache = FileHashCache()
    records = []
    descriptor = None
    completed = 0
    group_id = uuid4().hex
    with ExitStack() as stack:
        for root in sorted(plan.roots, key=normalized_path):
            stack.enter_context(transaction_lock(root))
        revalidate_plan(plan, cache, cancelled=cancelled, progress=progress)
        plan.database.execute(
            "CREATE TEMP TABLE IF NOT EXISTS execution_checks(id INTEGER PRIMARY KEY,fingerprint TEXT)"
        )
        plan.database.execute("DELETE FROM execution_checks")
        for index, operation in enumerate(plan.operations(), 1):
            check_cancelled(cancelled)
            if operation.target and operation.target["is_directory"]:
                path = plan.roots[operation.destination_side] / operation.path
                plan.database.execute(
                    "INSERT INTO execution_checks VALUES (?,?)",
                    (index, tree_fingerprint(path)),
                )
        try:
            for root in plan.roots:
                record = create_application_record(root, transaction_id=group_id)
                (record.transaction_directory / "assets").mkdir()
                records.append(record)
            descriptor = {
                "version": 1,
                "id": group_id,
                "roots": [str(root) for root in plan.roots],
                "identities": list(plan.identities),
            }
            save_group(records, descriptor, "running")
            set_pending(plan.roots, group_id)
            for index, operation in enumerate(plan.operations(), 1):
                check_cancelled(cancelled)
                if operation.action is not SyncAction.COPY:
                    continue
                source = plan.roots[operation.source_side] / operation.path
                checked_boundary(
                    plan.roots[operation.source_side], source, allow_leaf_link=False
                )
                notify(
                    progress,
                    "Preparing verified copy",
                    completed,
                    plan.count,
                    operation.path,
                )
                if (
                    cache.digest(
                        plan.roots[operation.source_side],
                        operation.source,
                        cancelled=cancelled,
                    )
                    != operation.source["digest"]
                ):
                    raise FileExistsError(f"Copy source changed: {source}")
                payload = (
                    records[operation.destination_side].transaction_directory
                    / "assets"
                    / f"{index:032x}"
                )
                copy_exclusive(source, payload)
                if (
                    hash_payload(payload, cancelled=cancelled)
                    != operation.source["digest"]
                ):
                    raise FileExistsError(f"Copied contents changed: {source}")
            revalidate_plan(plan, cache, cancelled=cancelled, progress=progress)
            rows = plan.database.execute(
                "SELECT id,data FROM operations ORDER BY CASE json_extract(data,'$.action') WHEN 'mkdir' THEN 0 ELSE 1 END,length(json_extract(data,'$.path')),id"
            )
            for index, raw in rows:
                check_cancelled(cancelled)
                operation = SyncOperation(**json.loads(raw))
                record = records[operation.destination_side]
                operation_id = f"{index:032x}"
                target = record.root / operation.path
                checked_boundary(record.root, target, allow_leaf_link=False)
                notify(
                    progress,
                    "Applying synchronization",
                    completed,
                    plan.count,
                    operation.path,
                )
                if operation.target is not None:
                    if operation.target["is_directory"]:
                        expected = plan.database.execute(
                            "SELECT fingerprint FROM execution_checks WHERE id=?",
                            (index,),
                        ).fetchone()[0]
                        if tree_fingerprint(target) != expected:
                            raise FileExistsError(
                                f"Destination folder changed: {target}"
                            )
                    elif (
                        cache.digest(record.root, operation.target, cancelled=cancelled)
                        != operation.target["digest"]
                    ):
                        raise FileExistsError(f"Destination changed: {target}")
                    kind = (
                        MutationKind.BACKUP_DELETE
                        if operation.action is SyncAction.DELETE
                        else MutationKind.BACKUP_TARGET
                    )
                    backup = (
                        record.transaction_directory
                        / "backup"
                        / (
                            operation_id
                            if kind is MutationKind.BACKUP_DELETE
                            else "overwrite-" + operation_id
                        )
                    )
                    execute_mutation(
                        record, AppliedMutation(kind, operation_id, target, backup)
                    )
                    if operation.target["is_directory"]:
                        if tree_fingerprint(backup) != expected:
                            raise FileExistsError(
                                "Destination folder changed while backing it up"
                            )
                    elif (
                        hash_payload(backup, cancelled=cancelled)
                        != operation.target["digest"]
                    ):
                        raise FileExistsError("Destination changed while backing it up")
                elif path_exists(target):
                    raise FileExistsError(
                        f"Destination was created after review: {target}"
                    )
                if operation.action is not SyncAction.DELETE:
                    stage = record.transaction_directory / "staging" / operation_id
                    if operation.action is SyncAction.MKDIR:
                        execute_mutation(
                            record,
                            AppliedMutation(
                                MutationKind.CREATE_FOLDER, operation_id, target=stage
                            ),
                        )
                    else:
                        execute_mutation(
                            record,
                            AppliedMutation(
                                MutationKind.PREPARED_FILE, operation_id, target=stage
                            ),
                            payload=record.transaction_directory
                            / "assets"
                            / operation_id,
                        )
                    execute_mutation(
                        record,
                        AppliedMutation(
                            MutationKind.FINAL_MOVE, operation_id, stage, target
                        ),
                    )
                record.completed_operation_ids.append(operation_id)
                record.save()
                completed += 1
                notify(
                    progress, "Synchronized item", completed, plan.count, operation.path
                )
            for operation in plan.operations():
                if operation.action is SyncAction.COPY:
                    if (
                        cache.digest(
                            plan.roots[operation.source_side],
                            operation.source,
                            cancelled=cancelled,
                        )
                        != operation.source["digest"]
                    ):
                        raise FileExistsError(
                            "A source changed before synchronization committed"
                        )
                    if (
                        hash_payload(
                            plan.roots[operation.destination_side] / operation.path,
                            cancelled=cancelled,
                        )
                        != operation.source["digest"]
                    ):
                        raise FileExistsError(
                            "A copied destination changed before commit"
                        )
            check_cancelled(cancelled)
            for record in records:
                record.undo_state = capture_undo_state(record)
                record.status = ApplicationStatus.COMPLETED
                record.finished_at = now_text()
                record.save()
            save_group(records, descriptor, "completed")
            fresh = compare_folders(*plan.roots, verify_content=True, hash_cache=cache)
            try:
                save_sync_baseline(fresh)
            finally:
                fresh.close()
            clear_pending(plan.roots, group_id)
            return SyncApplication(
                group_id,
                plan.roots,
                "completed",
                tuple(item.journal_path for item in records),
                completed,
            )
        except (
            OSError,
            ValueError,
            RuntimeError,
            TypeError,
            KeyError,
            sqlite3.Error,
        ) as error:
            return restore_group(
                records, descriptor, completed, str(error), progress=progress
            )
        except SnapshotCancelled:
            return restore_group(
                records,
                descriptor,
                completed,
                "Synchronization cancelled",
                progress=progress,
            )


def restore_group(
    records, descriptor, completed, error="", *, progress=None, undo=False
):
    from recovery import reconcile_pending

    errors = []
    for record in reversed(records):
        try:
            validate_application_record(record)
            if record.log_truncated:
                with record.mutation_log_path.open("r+b") as stream:
                    stream.truncate(record.log_valid_bytes)
                    stream.flush()
                    os.fsync(stream.fileno())
                record.log_truncated = False
            reconcile_pending(record)
            record.status = (
                ApplicationStatus.UNDOING if undo else ApplicationStatus.RUNNING
            )
            record.save()
            for index, mutation in enumerate(reversed(record.mutations)):
                notify(
                    progress,
                    "Restoring synchronization",
                    index,
                    len(record.mutations),
                    str(mutation.target),
                )
                reverse_recorded_mutation(record, mutation)
            record.status = (
                ApplicationStatus.UNDONE if undo else ApplicationStatus.ROLLED_BACK
            )
            record.error = error
            record.finished_at = now_text()
            record.rollback_errors.clear()
            record.save()
        except (OSError, ValueError, RuntimeError, TypeError, KeyError) as failure:
            errors.append(f"{record.root}: {failure}")
            record.status = (
                ApplicationStatus.UNDO_FAILED
                if undo
                else ApplicationStatus.ROLLBACK_FAILED
            )
            record.rollback_errors = [str(failure)]
            try:
                record.save()
            except OSError as save_error:
                errors.append(str(save_error))
            break
    state = "recovery_failed" if errors else "undone" if undo else "rolled_back"
    if descriptor is not None:
        try:
            if not errors:
                clear_pending(
                    tuple(item.root for item in records),
                    descriptor["id"],
                    invalidate=True,
                )
            save_group(records, descriptor, state)
        except (OSError, ValueError) as failure:
            errors.append(str(failure))
            state = "recovery_failed"
    return SyncApplication(
        descriptor["id"] if descriptor else "",
        tuple(item.root for item in records),
        state,
        tuple(item.journal_path for item in records),
        completed,
        error,
        tuple(errors),
    )


def recover_sync_group(marker, *, undo=False, progress=None):
    from recovery import clear_abandoned_lock, inspect_recovery

    descriptor, records, states = load_group(marker)
    roots = tuple(item.root for item in records)
    for root in roots:
        clear_abandoned_lock(root)
    with ExitStack() as stack:
        for root in sorted(roots, key=normalized_path):
            stack.enter_context(transaction_lock(root))
        descriptor, records, states = load_group(marker)
        if undo:
            if any(state != "completed" for state in states) or any(
                record.status is not ApplicationStatus.COMPLETED for record in records
            ):
                raise ValueError("Only a completed synchronization can be undone")
            for record in records:
                completed = inspect_recovery(record.root).completed
                latest = max(completed, key=lambda item: item.started_at, default=None)
                if latest is None or latest.transaction_id != record.transaction_id:
                    raise ValueError("Undo newer transactions on every root first")
                validate_undo_state(record)
            set_pending(roots, descriptor["id"])
            save_group(records, descriptor, "undoing")
        elif all(
            state in {"rolled_back", "undone"} for state in states
        ) and not synchronization_problem(records[0]):
            return SyncApplication(
                descriptor["id"],
                roots,
                states[0],
                tuple(item.journal_path for item in records),
                0,
            )
        elif all(
            state == "completed" for state in states
        ) and not synchronization_problem(records[0]):
            return SyncApplication(
                descriptor["id"],
                roots,
                "completed",
                tuple(item.journal_path for item in records),
                sum(len(item.completed_operation_ids) for item in records),
            )
        undo = (
            undo
            or "undoing" in states
            or any(
                item.status
                in {ApplicationStatus.UNDOING, ApplicationStatus.UNDO_FAILED}
                for item in records
            )
        )
        return restore_group(
            records,
            descriptor,
            sum(len(item.completed_operation_ids) for item in records),
            progress=progress,
            undo=undo,
        )
