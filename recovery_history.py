from __future__ import annotations

import os
from pathlib import Path

from fs_paths import checked_boundary, normalized_path
from plan_apply import (
    ApplicationStatus,
    load_application_record,
    validate_application_record,
    validate_undo_state,
)
from snapshot_store import check_cancelled


def list_recovery_history(root, *, cancelled=None, limit=1000):
    root = Path(root).absolute()
    directory = root / ".file-tree-viewer" / "transactions"
    checked_boundary(root, directory, allow_leaf_link=False)
    if not directory.exists():
        return []
    rows = []
    with os.scandir(directory) as iterator:
        for item in iterator:
            check_cancelled(cancelled)
            if len(rows) >= limit:
                break
            journal = Path(item.path) / "journal.json"
            try:
                checked_boundary(root, journal, allow_leaf_link=False)
                record = load_application_record(journal)
                if normalized_path(record.root) != normalized_path(root):
                    raise ValueError("Recovery root does not match")
                from sync_apply import synchronization_problem

                issue = synchronization_problem(record)
                rows.append(
                    {
                        "journal": str(journal),
                        "id": record.transaction_id,
                        "status": record.status.value,
                        "time": record.started_at,
                        "details": issue
                        or record.error
                        or "; ".join(record.rollback_errors),
                        "sync": (record.transaction_directory / "sync.json").exists(),
                    }
                )
            except (OSError, ValueError, TypeError, KeyError) as error:
                rows.append(
                    {
                        "journal": str(journal),
                        "id": item.name,
                        "status": "invalid",
                        "time": "",
                        "details": str(error),
                        "sync": False,
                    }
                )
    return sorted(rows, key=lambda row: (row["time"], row["id"]), reverse=True)


def check_history_undo(root, journal):
    root, journal = Path(root).absolute(), Path(journal).absolute()
    checked_boundary(root, journal, allow_leaf_link=False)
    if journal.parent.parent != root / ".file-tree-viewer" / "transactions":
        raise ValueError("Invalid recovery journal location")
    record = load_application_record(journal)
    if normalized_path(record.root) != normalized_path(root):
        raise ValueError("Recovery root does not match")
    records = (record,)
    marker = record.transaction_directory / "sync.json"
    if marker.exists():
        from sync_apply import load_group, synchronization_problem

        problem = synchronization_problem(record)
        if problem:
            raise ValueError(problem)
        _, records, _ = load_group(marker)
    for participant in records:
        if participant.status is not ApplicationStatus.COMPLETED:
            raise ValueError("Only completed transactions can be undone")
        validate_application_record(participant)
        validate_undo_state(participant)
    return record
