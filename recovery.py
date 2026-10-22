from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass, replace
from pathlib import Path

from fs_ops import file_signature, path_exists, transaction_lock
from fs_paths import checked_boundary, is_link_stat, normalize_root, normalized_path
from plan_apply import (
    ApplicationStatus,
    MutationKind,
    PlanApplicationRecord,
    copied_backup_path,
    load_application_record,
    log_event,
    now_text,
    required_path,
    reverse_recorded_mutation,
    validate_application_record,
)

UNFINISHED_STATUSES = {
    ApplicationStatus.RUNNING,
    ApplicationStatus.ROLLBACK_FAILED,
    ApplicationStatus.UNDOING,
    ApplicationStatus.UNDO_FAILED,
}


@dataclass(frozen=True, slots=True)
class RecoveryCandidate:
    journal_path: Path
    record: PlanApplicationRecord | None
    problem: str


@dataclass(frozen=True, slots=True)
class RecoveryScan:
    root: Path
    candidates: tuple[RecoveryCandidate, ...]
    completed: tuple[PlanApplicationRecord, ...]
    lock_path: Path | None = None

    @property
    def needs_attention(self) -> bool:
        return bool(self.candidates or self.lock_path)

    def summary(self) -> str:
        lines = [f"Recovery check for {self.root}"]
        for candidate in self.candidates[:20]:
            lines.append(f"{candidate.journal_path}: {candidate.problem}")
        if len(self.candidates) > 20:
            lines.append(f"{len(self.candidates) - 20:,} more recovery records")
        if self.lock_path is not None:
            lines.append(f"Transaction lock: {self.lock_path}")
        lines.append(
            "New Plan applications are blocked until these records are resolved."
        )
        return "\n".join(lines)


def inspect_recovery(root: Path | str) -> RecoveryScan:
    root_path = normalize_root(root)
    if root_path is None:
        raise ValueError("A recovery root is required")
    internal = root_path / ".file-tree-viewer"
    candidates = []
    completed = []
    lock_path = internal / "transaction.lock"
    try:
        checked_boundary(root_path, internal, allow_leaf_link=False)
        if not internal.exists():
            return RecoveryScan(root_path, (), ())
        if not lock_path.exists() and not lock_path.is_symlink():
            lock_path = None
        transactions = internal / "transactions"
        checked_boundary(root_path, transactions, allow_leaf_link=False)
        directories = sorted(transactions.iterdir()) if transactions.exists() else []
        for directory in directories:
            journal = directory / "journal.json"
            try:
                checked_boundary(root_path, journal, allow_leaf_link=False)
                if not directory.is_dir():
                    raise ValueError("Unexpected item in the transaction directory")
                record = load_application_record(journal)
                if normalized_path(record.root) != normalized_path(root_path):
                    raise ValueError("Recovery record root mismatch")
            except (
                OSError,
                ValueError,
                TypeError,
                KeyError,
                UnicodeError,
                json.JSONDecodeError,
            ) as error:
                candidates.append(
                    RecoveryCandidate(
                        journal, None, f"Invalid recovery record: {error}"
                    )
                )
                continue
            from sync_apply import synchronization_problem

            group_problem = synchronization_problem(record)
            if group_problem:
                candidates.append(RecoveryCandidate(journal, record, group_problem))
                continue
            if (
                record.status in UNFINISHED_STATUSES
                or record.pending_mutations
                or record.log_truncated
                or record.reversing_mutation_ids
            ):
                details = record.status.value
                if record.pending_mutations:
                    details += (
                        f"; {len(record.pending_mutations)} unconfirmed change(s)"
                    )
                if record.log_truncated:
                    details += "; incomplete log tail"
                candidates.append(RecoveryCandidate(journal, record, details))
            elif record.status is ApplicationStatus.COMPLETED:
                completed.append(record)
    except (OSError, ValueError) as error:
        candidates.append(
            RecoveryCandidate(
                internal, None, f"Recovery storage is inaccessible: {error}"
            )
        )
    return RecoveryScan(root_path, tuple(candidates), tuple(completed), lock_path)


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    root: Path
    recovered: tuple[str, ...]
    errors: tuple[str, ...]


def signature_matches(path: Path, expected: dict[str, int] | None) -> bool:
    if expected is None or not path_exists(path):
        return False
    current = file_signature(path)
    return all(
        current.get(key) == value
        for key, value in expected.items()
        if key != "st_ctime_ns"
    )


def process_is_running(pid: int) -> bool:
    if pid <= 0:
        raise ValueError("Invalid lock process ID")
    if os.name == "nt":
        import ctypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
        handle = kernel.OpenProcess(0x1000, False, pid)
        if handle:
            kernel.CloseHandle(handle)
            return True
        return ctypes.get_last_error() != 87
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def clear_abandoned_lock(root: Path) -> None:
    path = root / ".file-tree-viewer" / "transaction.lock"
    if not path_exists(path):
        return
    checked_boundary(root, path, allow_leaf_link=False)
    expected = file_signature(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or type(payload.get("pid")) is not int
        or not isinstance(payload.get("token"), str)
    ):
        raise ValueError("Damaged transaction lock requires manual review")
    if process_is_running(payload["pid"]):
        raise RuntimeError(
            "The transaction owner is still running; close it before recovery"
        )
    if file_signature(path) != expected:
        raise RuntimeError("The transaction lock changed during recovery")
    path.unlink()


def reconcile_pending(record: PlanApplicationRecord) -> None:
    for mutation in tuple(record.pending_mutations):
        target = required_path(mutation.target)
        if not path_exists(target) and (
            mutation.source is None
            or signature_matches(mutation.source, mutation.before_signature)
        ):
            log_event(record, "aborted", mutation)
        elif (
            mutation.kind is MutationKind.PREPARED_FILE
            and signature_matches(target, mutation.before_signature)
            and not path_exists(
                record.transaction_directory / "assets" / mutation.operation_id
            )
        ) or (
            mutation.kind is not MutationKind.STAGE_COPY
            and mutation.source is not None
            and not path_exists(mutation.source)
            and signature_matches(target, mutation.before_signature)
        ):
            applied = replace(mutation, after_signature=file_signature(target))
            log_event(record, "applied", applied)
            record.mutations.append(applied)
        elif (
            mutation.source is None
            and target
            == record.transaction_directory / "staging" / mutation.operation_id
            and path_exists(target)
        ):
            details = target.lstat()
            safe = (
                mutation.kind is MutationKind.CREATE_FILE
                and stat.S_ISREG(details.st_mode)
                and details.st_size == 0
            ) or (
                mutation.kind is MutationKind.CREATE_FOLDER
                and stat.S_ISDIR(details.st_mode)
                and not any(target.iterdir())
            )
            if not safe or is_link_stat(details):
                raise FileExistsError(f"Unconfirmed created item was changed: {target}")
            applied = replace(mutation, after_signature=file_signature(target))
            log_event(record, "applied", applied)
            record.mutations.append(applied)
        else:
            raise FileExistsError(
                f"Unconfirmed change conflicts with current files: {target}"
            )
        record.pending_mutations.remove(mutation)

    for mutation in reversed(record.mutations):
        if mutation.key not in record.reversing_mutation_ids:
            continue
        target = required_path(mutation.target)
        if mutation.kind in {MutationKind.STAGE_COPY, MutationKind.PREPARED_FILE} or (
            mutation.kind is MutationKind.CREATE_FILE
            and mutation.after_signature
            and mutation.after_signature.get("st_size", 0) > 0
        ):
            backup = copied_backup_path(mutation)
            finished = not path_exists(target) and signature_matches(
                backup, mutation.after_signature
            )
            untouched = not path_exists(backup) and signature_matches(
                target, mutation.after_signature
            )
        elif mutation.source is None:
            finished = not path_exists(target)
            untouched = signature_matches(target, mutation.after_signature)
        else:
            finished = not path_exists(target) and signature_matches(
                mutation.source, mutation.after_signature
            )
            untouched = not path_exists(mutation.source) and signature_matches(
                target, mutation.after_signature
            )
        if not finished and not untouched:
            raise FileExistsError(
                f"Interrupted reversal conflicts with current files: {target}"
            )
        log_event(record, "reversed" if finished else "reverse_aborted", mutation)
        if finished:
            record.reversed_mutation_ids.add(mutation.key)
        record.reversing_mutation_ids.remove(mutation.key)


def recover_root(root: Path | str, *, progress=None) -> RecoveryResult:
    root_path = normalize_root(root)
    if root_path is None:
        raise ValueError("A recovery root is required")
    clear_abandoned_lock(root_path)
    scan = inspect_recovery(root_path)
    coordinated = [
        item
        for item in scan.candidates
        if item.record is not None
        and (item.record.transaction_directory / "sync.json").exists()
    ]
    if coordinated:
        from sync_apply import recover_sync_group

        recovered, errors = [], []
        for candidate in coordinated:
            try:
                result = recover_sync_group(
                    candidate.record.transaction_directory / "sync.json",
                    progress=progress,
                )
                if result.recovery_errors:
                    errors.extend(result.recovery_errors)
                    break
                recovered.append(result.transaction_id)
            except (OSError, ValueError, RuntimeError, TypeError, KeyError) as error:
                errors.append(str(error))
                break
        if errors:
            return RecoveryResult(root_path, tuple(recovered), tuple(errors))
        remaining = recover_root(root_path, progress=progress)
        return RecoveryResult(
            root_path, tuple(recovered) + remaining.recovered, remaining.errors
        )
    recovered = []
    errors = []
    with transaction_lock(root_path):
        scan = inspect_recovery(root_path)
        invalid = [item for item in scan.candidates if item.record is None]
        if invalid:
            return RecoveryResult(
                root_path, (), tuple(item.problem for item in invalid)
            )
        candidates = sorted(
            scan.candidates, key=lambda item: item.record.started_at, reverse=True
        )
        for candidate in candidates:
            record = load_application_record(candidate.journal_path)
            undoing = record.status in {
                ApplicationStatus.UNDOING,
                ApplicationStatus.UNDO_FAILED,
            }
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
                    ApplicationStatus.UNDOING if undoing else ApplicationStatus.RUNNING
                )
                record.save()
                total = len(record.mutations)
                for position, mutation in enumerate(reversed(record.mutations)):
                    if mutation.key in record.reversed_mutation_ids:
                        continue
                    if progress:
                        progress(
                            "Recovering interrupted Plan",
                            position,
                            total,
                            required_path(mutation.target).name,
                        )
                    validate_application_record(record)
                    reverse_recorded_mutation(record, mutation)
                record.status = (
                    ApplicationStatus.UNDONE
                    if undoing
                    else ApplicationStatus.ROLLED_BACK
                )
                record.rollback_errors.clear()
                record.finished_at = now_text()
                if undoing:
                    record.undone_at = now_text()
                record.save()
                recovered.append(record.transaction_id)
            except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
                record.status = (
                    ApplicationStatus.UNDO_FAILED
                    if undoing
                    else ApplicationStatus.ROLLBACK_FAILED
                )
                record.rollback_errors = [str(error)]
                record.save()
                errors.append(f"{record.transaction_id}: {error}")
                break
    return RecoveryResult(root_path, tuple(recovered), tuple(errors))
