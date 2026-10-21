from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from fs_paths import checked_boundary, normalize_root, normalized_path
from plan_apply import ApplicationStatus, PlanApplicationRecord, load_application_record

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
            if (
                record.status in UNFINISHED_STATUSES
                or record.pending_mutations
                or record.log_truncated
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
