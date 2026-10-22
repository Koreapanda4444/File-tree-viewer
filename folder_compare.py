from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from enum import Enum

from file_hashing import FileHashCache
from fs_ops import file_signature
from fs_paths import checked_boundary, normalize_root
from snapshot import build_snapshot
from snapshot_compare import compare_snapshots
from snapshot_store import check_cancelled


class FolderStatus(str, Enum):
    SAME = "same"
    LEFT_ONLY = "left_only"
    RIGHT_ONLY = "right_only"
    MODIFIED = "modified"
    TYPE_CONFLICT = "type_conflict"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class FolderEntry:
    path: str
    status: FolderStatus
    left: dict | None
    right: dict | None
    details: str
    left_digest: str | None = None
    right_digest: str | None = None


class FolderComparison:
    def __init__(self, changes, left, right, identities):
        self.changes, self.left, self.right = changes, left, right
        self.left_root, self.right_root = left.root, right.root
        self.identities = identities
        self.database = changes._database
        self.hash_verified = False
        self.error_count = left.error_count + right.error_count
        self.closed = False
        self.recount()

    def recount(self):
        self.counts = Counter(
            {
                FolderStatus(status): count
                for status, count in self.database.execute(
                    "SELECT folder_status,COUNT(*) FROM changes GROUP BY folder_status"
                )
            }
        )

    def count(self, status=None):
        return (
            self.counts[FolderStatus(status)]
            if status is not None
            else sum(self.counts.values())
        )

    def page(self, *, status=None, offset=0, limit=256):
        if self.closed or offset < 0 or not 0 < limit <= 4096:
            raise ValueError("Invalid folder comparison page")
        where, parameters = (
            ("", ())
            if status is None
            else (" WHERE folder_status=?", (FolderStatus(status).value,))
        )
        return tuple(
            self.decode(row)
            for row in self.database.execute(
                "SELECT COALESCE(source,target),folder_status,before_data,after_data,details,left_digest,right_digest FROM changes"
                + where
                + " ORDER BY id LIMIT ? OFFSET ?",
                (*parameters, limit, offset),
            )
        )

    @staticmethod
    def decode(row):
        path, status, left, right, details, left_digest, right_digest = row
        return FolderEntry(
            path,
            FolderStatus(status),
            json.loads(left) if left else None,
            json.loads(right) if right else None,
            details,
            left_digest,
            right_digest,
        )

    def iter_entries(self):
        cursor = self.database.execute(
            "SELECT COALESCE(source,target),folder_status,before_data,after_data,details,left_digest,right_digest FROM changes ORDER BY id"
        )
        try:
            for row in cursor:
                yield self.decode(row)
        finally:
            cursor.close()

    def close(self):
        if not self.closed:
            self.closed = True
            self.changes.close()
            self.left.close()
            self.right.close()


def root_identity(root):
    details = file_signature(root)
    return {key: details[key] for key in ("st_dev", "st_ino", "st_mode")}


def compare_folders(
    left_root,
    right_root,
    *,
    cancelled=None,
    progress=None,
    verify_content=False,
    hash_cache=None,
):
    roots = tuple(normalize_root(root) for root in (left_root, right_root))
    for root in roots:
        if root is None:
            raise ValueError("Choose two folder roots")
        checked_boundary(root, root, allow_leaf_link=False)
    identities = tuple(root_identity(root) for root in roots)
    snapshots = []
    changes = None
    try:
        for side, root in zip(("left", "right"), roots, strict=True):
            check_cancelled(cancelled)
            snapshots.append(
                build_snapshot(
                    root,
                    cancelled=cancelled,
                    progress=lambda count, errors, s=side: (
                        progress(f"Scanning {s}: {errors:,} errors", count)
                        if progress
                        else None
                    ),
                )
            )
        changes = compare_snapshots(
            *snapshots,
            cancelled=cancelled,
            progress=progress,
            include_unchanged=True,
            ignore_directory_times=True,
        )
        database = changes._database
        database.execute("ALTER TABLE changes ADD COLUMN folder_status TEXT")
        database.execute("ALTER TABLE changes ADD COLUMN left_digest TEXT")
        database.execute("ALTER TABLE changes ADD COLUMN right_digest TEXT")
        database.execute(
            "UPDATE changes SET folder_status=CASE WHEN kind='created' THEN 'right_only' WHEN kind='deleted' THEN 'left_only' WHEN kind='unchanged' THEN 'same' ELSE 'modified' END"
        )
        database.execute(
            "UPDATE changes SET folder_status='type_conflict' WHERE before_data IS NOT NULL AND after_data IS NOT NULL AND (json_extract(before_data,'$.is_directory') != json_extract(after_data,'$.is_directory') OR json_extract(before_data,'$.is_symlink') != json_extract(after_data,'$.is_symlink'))"
        )
        database.execute(
            "UPDATE changes SET folder_status='error',details='Links and inaccessible items require review' WHERE json_extract(before_data,'$.error') IS NOT NULL OR json_extract(after_data,'$.error') IS NOT NULL OR json_extract(before_data,'$.is_symlink')=1 OR json_extract(after_data,'$.is_symlink')=1"
        )
        database.execute("CREATE INDEX folder_status_page ON changes(folder_status,id)")
        database.commit()
        check_cancelled(cancelled)
        if identities != tuple(root_identity(root) for root in roots):
            raise FileExistsError("A comparison root was replaced while scanning")
        result = FolderComparison(changes, *snapshots, identities)
        if verify_content:
            verify_folder_contents(
                result, hash_cache=hash_cache, cancelled=cancelled, progress=progress
            )
        return result
    except BaseException:
        if changes is not None:
            changes.close()
        for snapshot in snapshots:
            snapshot.close()
        raise


def verify_folder_contents(
    comparison, *, hash_cache=None, cancelled=None, progress=None
):
    cache = hash_cache or FileHashCache()
    cursor = comparison.database.execute(
        "SELECT id,before_data,after_data,folder_status FROM changes ORDER BY id"
    )
    processed = 0
    try:
        for row_id, before, after, status in cursor:
            check_cancelled(cancelled)
            if status in {FolderStatus.ERROR.value, FolderStatus.TYPE_CONFLICT.value}:
                continue
            entries = [
                json.loads(value) if value else None for value in (before, after)
            ]
            hashes = []
            try:
                for root, entry in zip(
                    (comparison.left_root, comparison.right_root), entries, strict=True
                ):
                    hashes.append(
                        cache.digest(root, entry, cancelled=cancelled)
                        if entry and not entry["is_directory"]
                        else None
                    )
            except (OSError, ValueError) as error:
                comparison.database.execute(
                    "UPDATE changes SET folder_status='error',details=? WHERE id=?",
                    (str(error), row_id),
                )
            else:
                details = (
                    "SHA-256 verified"
                    if any(hashes)
                    else "Matching directory structure"
                )
                if all(hashes):
                    status = (
                        FolderStatus.SAME.value
                        if hashes[0] == hashes[1]
                        else FolderStatus.MODIFIED.value
                    )
                    details = (
                        "SHA-256 equal" if hashes[0] == hashes[1] else "SHA-256 differs"
                    )
                comparison.database.execute(
                    "UPDATE changes SET folder_status=?,details=?,left_digest=?,right_digest=? WHERE id=?",
                    (status, details, *hashes, row_id),
                )
            processed += 1
            if progress and processed % 64 == 0:
                progress("Verifying file contents with SHA-256", processed)
        check_cancelled(cancelled)
        comparison.database.commit()
        comparison.hash_verified = True
        comparison.recount()
    finally:
        cursor.close()
