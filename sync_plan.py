from __future__ import annotations

import json
import sqlite3
import tempfile
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path

from folder_compare import FolderStatus
from fs_paths import checked_boundary, normalize_plan_path, path_is_within
from snapshot_store import check_cancelled


class SyncAction(str, Enum):
    COPY = "copy"
    MKDIR = "mkdir"
    DELETE = "delete"


@dataclass(frozen=True, slots=True)
class SyncOperation:
    action: SyncAction
    path: str
    destination_side: int
    source_side: int | None = None
    source: dict | None = None
    target: dict | None = None

    def __post_init__(self):
        object.__setattr__(self, "action", SyncAction(self.action))
        normalize_plan_path(self.path)
        if self.destination_side not in (0, 1) or self.source_side not in (None, 0, 1):
            raise ValueError("Invalid synchronization endpoint")
        if self.action is SyncAction.COPY and (
            self.source is None
            or self.source_side is None
            or self.source_side == self.destination_side
        ):
            raise ValueError(
                "A synchronized copy requires distinct source and destination roots"
            )


class SyncPlan:
    def __init__(self, comparison):
        self.roots = (comparison.left_root, comparison.right_root)
        self.identities = comparison.identities
        self.temporary = tempfile.TemporaryDirectory(
            prefix="file-tree-viewer-sync-plan-"
        )
        self.database_path = Path(self.temporary.name) / "plan.sqlite3"
        self.database = sqlite3.connect(self.database_path, check_same_thread=False)
        self.database.execute("PRAGMA journal_mode=OFF")
        self.database.execute("PRAGMA cache_size=-2048")
        self.database.execute(
            "CREATE TABLE operations(id INTEGER PRIMARY KEY,data TEXT NOT NULL)"
        )
        self.database.execute(
            "CREATE TABLE inventory(side INTEGER,path TEXT,folded TEXT,data TEXT,PRIMARY KEY(side,path)) WITHOUT ROWID"
        )
        self.database.execute("CREATE INDEX inventory_folded ON inventory(folded,side)")
        self.operation_count = 0
        self.issues = []
        self.issue_count = 0
        self.direction = ""
        self.closed = False

    @property
    def can_apply(self):
        return not self.issue_count

    @property
    def count(self):
        return self.operation_count

    def issue(self, message):
        self.issue_count += 1
        if len(self.issues) < 200:
            self.issues.append(message)

    def add(self, operation):
        self.database.execute(
            "INSERT INTO operations(data) VALUES (?)", (json.dumps(asdict(operation)),)
        )
        self.operation_count += 1

    def operations(self, *, offset=0, limit=None):
        sql = "SELECT data FROM operations ORDER BY id"
        parameters = ()
        if limit is not None:
            if offset < 0 or not 0 < limit <= 4096:
                raise ValueError("Invalid synchronization page")
            sql += " LIMIT ? OFFSET ?"
            parameters = (limit, offset)
        cursor = self.database.execute(sql, parameters)
        try:
            for row in cursor:
                yield SyncOperation(**json.loads(row[0]))
        finally:
            cursor.close()

    def close(self):
        if not self.closed:
            self.closed = True
            self.database.close()
            self.temporary.cleanup()


def prepare_sync_plan(comparison, *, cancelled=None):
    plan = SyncPlan(comparison)
    try:
        if not comparison.hash_verified:
            plan.issue(
                "Enable SHA-256 verification and compare again before synchronization"
            )
        if comparison.error_count:
            plan.issue(f"The scan contains {comparison.error_count:,} access errors")
        if path_is_within(plan.roots[0], plan.roots[1]) or path_is_within(
            plan.roots[1], plan.roots[0]
        ):
            plan.issue(
                "Synchronization roots must be distinct and must not contain one another"
            )
        for root in plan.roots:
            checked_boundary(root, root, allow_leaf_link=False)
        for entry in comparison.iter_entries():
            check_cancelled(cancelled)
            if entry.status in {FolderStatus.ERROR, FolderStatus.TYPE_CONFLICT}:
                plan.issue(
                    f"{entry.path}: {entry.status.value}; resolve this item manually"
                )
            normalize_plan_path(entry.path)
            for side, value, digest in (
                (0, entry.left, entry.left_digest),
                (1, entry.right, entry.right_digest),
            ):
                if value:
                    data = {**value, "digest": digest}
                    plan.database.execute(
                        "INSERT INTO inventory VALUES (?,?,?,?)",
                        (
                            side,
                            entry.path,
                            entry.path.casefold(),
                            json.dumps(data, sort_keys=True),
                        ),
                    )
        collisions = plan.database.execute(
            "SELECT DISTINCT a.path,b.path FROM inventory a JOIN inventory b ON a.folded=b.folded WHERE a.path<b.path"
        )
        for first, second in collisions:
            plan.issue(f"Ambiguous case-insensitive paths: {first} / {second}")
        return plan
    except BaseException:
        plan.close()
        raise


def entry_data(plan, side, path):
    row = plan.database.execute(
        "SELECT data FROM inventory WHERE side=? AND path=?", (side, path)
    ).fetchone()
    return json.loads(row[0]) if row else None


def plan_one_way_sync(
    comparison, *, source_side=0, delete_extra=False, cancelled=None, progress=None
):
    if source_side not in (0, 1):
        raise ValueError("Choose the left or right source")
    plan = prepare_sync_plan(comparison, cancelled=cancelled)
    plan.direction = f"{'Left → Right' if source_side == 0 else 'Right → Left'}" + (
        "; delete destination-only items"
        if delete_extra
        else "; preserve destination-only items"
    )
    destination = 1 - source_side
    source_only = (
        FolderStatus.LEFT_ONLY if source_side == 0 else FolderStatus.RIGHT_ONLY
    )
    target_only = (
        FolderStatus.RIGHT_ONLY if source_side == 0 else FolderStatus.LEFT_ONLY
    )
    deleted_folders = set()
    try:
        for index, entry in enumerate(comparison.iter_entries()):
            check_cancelled(cancelled)
            if progress and index % 512 == 0:
                progress("Preparing one-way synchronization", index)
            source = entry_data(plan, source_side, entry.path)
            target = entry_data(plan, destination, entry.path)
            if (
                entry.status in {source_only, FolderStatus.MODIFIED}
                and source is not None
            ):
                if source["is_directory"]:
                    if target is None:
                        plan.add(
                            SyncOperation(SyncAction.MKDIR, entry.path, destination)
                        )
                else:
                    plan.add(
                        SyncOperation(
                            SyncAction.COPY,
                            entry.path,
                            destination,
                            source_side,
                            source,
                            target,
                        )
                    )
            elif delete_extra and entry.status is target_only:
                relative = normalize_plan_path(entry.path)
                if not any(
                    str(parent) in deleted_folders for parent in relative.parents
                ):
                    plan.add(
                        SyncOperation(
                            SyncAction.DELETE, entry.path, destination, target=target
                        )
                    )
                    if target["is_directory"]:
                        deleted_folders.add(entry.path)
        plan.database.commit()
        return plan
    except BaseException:
        plan.close()
        raise
