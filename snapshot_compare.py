from __future__ import annotations

import json
import sqlite3
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path

from fs_paths import normalized_path
from snapshot import FileSnapshot
from snapshot_store import check_cancelled


class SnapshotChangeKind(str, Enum):
    UNCHANGED = "unchanged"
    CREATED = "created"
    DELETED = "deleted"
    MODIFIED = "modified"
    MOVED = "moved_candidate"
    RENAMED = "renamed_candidate"


@dataclass(frozen=True, slots=True)
class SnapshotChange:
    kind: SnapshotChangeKind
    source: str | None
    target: str | None
    before: dict | None
    after: dict | None
    details: str


class SnapshotComparison:
    def __init__(
        self,
        temporary_directory,
        database: sqlite3.Connection,
        before: FileSnapshot,
        after: FileSnapshot,
    ) -> None:
        self._temporary_directory = temporary_directory
        self._database = database
        self.before_name = before.name
        self.after_name = after.name
        self.before_root = before.root
        self.after_root = after.root
        self.counts = Counter(
            {
                SnapshotChangeKind(kind): count
                for kind, count in database.execute(
                    "SELECT kind, COUNT(*) FROM changes GROUP BY kind"
                )
            }
        )
        self._closed = False

    def count(self, kinds=None) -> int:
        if kinds is None:
            return sum(self.counts.values())
        return sum(self.counts[SnapshotChangeKind(kind)] for kind in set(kinds))

    def page(
        self, *, kinds=None, offset: int = 0, limit: int = 512
    ) -> tuple[SnapshotChange, ...]:
        if self._closed:
            raise RuntimeError("The Snapshot comparison is closed")
        if offset < 0 or limit <= 0:
            raise ValueError("Invalid comparison page")
        parameters = []
        where = ""
        if kinds is not None:
            parameters = list(
                dict.fromkeys(SnapshotChangeKind(kind).value for kind in kinds)
            )
            if not parameters:
                return ()
            where = " WHERE kind IN (" + ",".join("?" for _ in parameters) + ")"
        rows = self._database.execute(
            "SELECT kind,source,target,before_data,after_data,details FROM changes"
            + where
            + " ORDER BY id LIMIT ? OFFSET ?",
            (*parameters, limit, offset),
        )
        return tuple(
            SnapshotChange(
                SnapshotChangeKind(kind),
                source,
                target,
                json.loads(before) if before else None,
                json.loads(after) if after else None,
                details,
            )
            for kind, source, target, before, after, details in rows
        )

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._database.close()
            self._temporary_directory.cleanup()


def compare_snapshots(
    before: FileSnapshot,
    after: FileSnapshot,
    *,
    cancelled=None,
    progress=None,
    include_unchanged=False,
    ignore_directory_times=False,
) -> SnapshotComparison:
    temporary = tempfile.TemporaryDirectory(prefix="file-tree-viewer-compare-")
    database = sqlite3.connect(
        Path(temporary.name) / "comparison.sqlite3", check_same_thread=False
    )
    database.execute("PRAGMA journal_mode=OFF")
    database.execute("PRAGMA synchronous=OFF")
    database.execute("PRAGMA temp_store=FILE")
    database.execute("PRAGMA cache_size=-2048")
    database.execute(
        "CREATE TABLE changes(id INTEGER PRIMARY KEY,kind TEXT NOT NULL,source TEXT,target TEXT,before_data TEXT,after_data TEXT,details TEXT NOT NULL,identity TEXT,size INTEGER,modified_ns INTEGER,is_directory INTEGER,is_symlink INTEGER)"
    )
    same_root = normalized_path(before.root) == normalized_path(after.root)
    left = iter(before.iter_entries())
    right = iter(after.iter_entries())
    old = next(left, None)
    new = next(right, None)
    batch = []
    processed = 0

    def add(kind, source, target, details):
        entry = target or source

        def encode(value):
            return (
                json.dumps(asdict(value), default=str, ensure_ascii=True)
                if value
                else None
            )

        batch.append(
            (
                kind.value,
                str(source.path) if source else None,
                str(target.path) if target else None,
                encode(source),
                encode(target),
                details,
                entry.identity,
                entry.size,
                entry.modified_ns,
                int(entry.is_directory),
                int(entry.is_symlink),
            )
        )
        if len(batch) >= 512:
            flush()

    def flush():
        if batch:
            database.executemany(
                "INSERT INTO changes(kind,source,target,before_data,after_data,details,identity,size,modified_ns,is_directory,is_symlink) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                batch,
            )
            batch.clear()

    try:
        while old is not None or new is not None:
            if processed % 512 == 0:
                check_cancelled(cancelled)
                if progress:
                    progress("Comparing Snapshot metadata", processed)
            if old is not None and new is not None and old.path == new.path:
                fields = [
                    key
                    for key in (
                        "is_directory",
                        "is_symlink",
                        "size",
                        "modified_ns",
                        "error",
                    )
                    if getattr(old, key) != getattr(new, key)
                    and not (
                        ignore_directory_times
                        and old.is_directory
                        and new.is_directory
                        and key == "modified_ns"
                    )
                ]
                if (
                    same_root
                    and old.identity
                    and new.identity
                    and old.identity != new.identity
                ):
                    fields.append("identity")
                if fields:
                    add(SnapshotChangeKind.MODIFIED, old, new, ", ".join(fields))
                elif include_unchanged:
                    add(SnapshotChangeKind.UNCHANGED, old, new, "Matching metadata")
                old, new = next(left, None), next(right, None)
            elif new is None or (
                old is not None and old.path.as_posix() < new.path.as_posix()
            ):
                add(SnapshotChangeKind.DELETED, old, None, "Path removed")
                old = next(left, None)
            else:
                add(SnapshotChangeKind.CREATED, None, new, "Path added")
                new = next(right, None)
            processed += 1
        flush()
        if same_root:
            database.execute(
                "CREATE INDEX candidate_identity ON changes(identity,is_directory,is_symlink,size,modified_ns,kind)"
            )
            database.execute(
                "CREATE TEMP TABLE identity_pairs AS SELECT MIN(CASE WHEN kind='deleted' THEN id END) AS old_id, MIN(CASE WHEN kind='created' THEN id END) AS new_id FROM changes WHERE identity IS NOT NULL AND kind IN ('deleted','created') GROUP BY identity,is_directory,is_symlink,size,modified_ns HAVING SUM(kind='deleted')=1 AND SUM(kind='created')=1"
            )
            for old_id, new_id in database.execute(
                "SELECT old_id,new_id FROM identity_pairs"
            ):
                check_cancelled(cancelled)
                source, target, after_data = database.execute(
                    "SELECT old.source,new.target,new.after_data FROM changes old, changes new WHERE old.id=? AND new.id=?",
                    (old_id, new_id),
                ).fetchone()
                kind = (
                    SnapshotChangeKind.RENAMED
                    if Path(source).parent == Path(target).parent
                    else SnapshotChangeKind.MOVED
                )
                database.execute(
                    "UPDATE changes SET kind=?,target=?,after_data=?,details=? WHERE id=?",
                    (
                        kind.value,
                        target,
                        after_data,
                        "Unique matching filesystem identity and metadata; candidate only",
                        old_id,
                    ),
                )
                database.execute("DELETE FROM changes WHERE id=?", (new_id,))
        check_cancelled(cancelled)
        database.execute("CREATE INDEX changes_kind ON changes(kind,id)")
        database.commit()
        return SnapshotComparison(temporary, database, before, after)
    except BaseException:
        database.close()
        temporary.cleanup()
        raise
    finally:
        left.close()
        right.close()
        before.release_thread_connection()
        if after is not before:
            after.release_thread_connection()
