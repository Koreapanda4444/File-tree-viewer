from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath

from fs_paths import checked_boundary, normalize_plan_path, normalized_path
from journal import sync_directory
from snapshot import FileSnapshot, SnapshotCancelled, count_errors

SNAPSHOT_FORMAT = "file-tree-viewer-snapshot"


def check_cancelled(cancelled) -> None:
    if cancelled is not None and cancelled.is_set():
        raise SnapshotCancelled


def copy_database(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    *,
    cancelled=None,
    progress=None,
) -> None:
    def report(status, remaining, total):
        check_cancelled(cancelled)
        if progress:
            progress("Copying Snapshot pages", total - remaining)

    check_cancelled(cancelled)
    source.backup(target, pages=256, progress=report)
    check_cancelled(cancelled)


def save_snapshot(
    snapshot: FileSnapshot,
    path: Path | str,
    *,
    name: str,
    cancelled=None,
    progress=None,
) -> Path:
    if not isinstance(name, str) or not name.strip() or len(name) > 128:
        raise ValueError("Enter a Snapshot name of 1 to 128 characters")
    destination = Path(path).expanduser().absolute()
    if normalized_path(destination) == normalized_path(snapshot.database_path):
        raise ValueError("The active Snapshot database cannot be replaced")
    checked_boundary(destination.parent, destination, allow_leaf_link=False)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".snapshot-", suffix=".tmp", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    database = sqlite3.connect(temporary)
    try:
        copy_database(
            snapshot._connection(), database, cancelled=cancelled, progress=progress
        )
        database.execute(
            "CREATE TABLE IF NOT EXISTS snapshot_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID"
        )
        metadata = {
            "format": SNAPSHOT_FORMAT,
            "version": 1,
            "name": name.strip(),
            "root": str(snapshot.root),
            "captured_at": snapshot.captured_at,
            "entry_count": snapshot.entry_count,
            "error_count": snapshot.error_count,
        }
        database.executemany(
            "INSERT OR REPLACE INTO snapshot_metadata VALUES (?, ?)",
            (
                (key, json.dumps(value, ensure_ascii=True))
                for key, value in metadata.items()
            ),
        )
        database.commit()
        database.close()
        check_cancelled(cancelled)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        checked_boundary(destination.parent, destination, allow_leaf_link=False)
        os.replace(temporary, destination)
        sync_directory(destination.parent)
        return destination
    finally:
        database.close()
        temporary.unlink(missing_ok=True)


def validate_database(database: sqlite3.Connection, *, cancelled=None) -> dict:
    database.execute("PRAGMA trusted_schema=OFF")
    if database.execute("PRAGMA quick_check").fetchone() != ("ok",):
        raise ValueError("The Snapshot database is damaged")
    tables = dict(
        database.execute(
            "SELECT name, type FROM sqlite_master WHERE name IN ('entries','errors','snapshot_metadata')"
        )
    )
    if any(
        tables.get(name) != "table"
        for name in ("entries", "errors", "snapshot_metadata")
    ):
        raise ValueError("Required Snapshot tables are missing")
    expected = {
        "path",
        "parent",
        "name",
        "is_directory",
        "is_symlink",
        "size",
        "modified_ns",
        "identity",
    }
    if {row[1] for row in database.execute("PRAGMA table_info(entries)")} != expected:
        raise ValueError("Unsupported Snapshot entry schema")
    metadata = {
        key: json.loads(value)
        for key, value in database.execute("SELECT key, value FROM snapshot_metadata")
    }
    if metadata.get("format") != SNAPSHOT_FORMAT or metadata.get("version") != 1:
        raise ValueError("Unsupported Snapshot format")
    for key in ("name", "root", "captured_at"):
        if not isinstance(metadata.get(key), str) or not metadata[key].strip():
            raise ValueError(f"Invalid Snapshot metadata: {key}")
    if (
        not Path(metadata["root"]).is_absolute()
        and not PureWindowsPath(metadata["root"]).is_absolute()
    ):
        raise ValueError("The Snapshot root must be absolute")
    datetime.fromisoformat(metadata["captured_at"])
    for key in ("entry_count", "error_count"):
        if type(metadata.get(key)) is not int or metadata[key] < 0:
            raise ValueError(f"Invalid Snapshot count: {key}")
    count = 0
    for (
        path,
        parent,
        name,
        directory,
        link,
        size,
        modified,
        identity,
    ) in database.execute(
        "SELECT path,parent,name,is_directory,is_symlink,size,modified_ns,identity FROM entries"
    ):
        if count % 512 == 0:
            check_cancelled(cancelled)
        if not all(isinstance(value, str) for value in (path, parent, name)):
            raise TypeError("Invalid Snapshot path fields")
        normalized = normalize_plan_path(path)
        if (
            normalized.as_posix() != path
            or normalized.name != name
            or (
                normalized.parent.as_posix()
                if normalized.parent != PurePosixPath(".")
                else ""
            )
            != parent
        ):
            raise ValueError("Inconsistent Snapshot path fields")
        if (
            directory not in (0, 1)
            or link not in (0, 1)
            or type(size) is not int
            or size < 0
            or type(modified) is not int
            or (identity is not None and not isinstance(identity, str))
        ):
            raise ValueError("Invalid Snapshot file metadata")
        count += 1
    for path, message in database.execute("SELECT path,message FROM errors"):
        if not isinstance(path, str) or not isinstance(message, str):
            raise TypeError("Invalid Snapshot error record")
        if path:
            normalize_plan_path(path)
    if (
        count != metadata["entry_count"]
        or count_errors(database) != metadata["error_count"]
    ):
        raise ValueError("Snapshot counts do not match the database")
    missing = database.execute(
        "SELECT child.path FROM entries child LEFT JOIN entries parent ON parent.path=child.parent WHERE child.parent<>'' AND (parent.path IS NULL OR parent.is_directory<>1 OR parent.is_symlink<>0) LIMIT 1"
    ).fetchone()
    if missing:
        raise ValueError(f"Snapshot parent is missing or invalid: {missing[0]}")
    return metadata


def load_snapshot(path: Path | str, *, cancelled=None, progress=None) -> FileSnapshot:
    source_path = Path(path).expanduser().absolute()
    source = sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)
    temporary_directory = tempfile.TemporaryDirectory(
        prefix="file-tree-viewer-snapshot-"
    )
    database_path = Path(temporary_directory.name) / "snapshot.sqlite3"
    database = sqlite3.connect(database_path)
    try:
        copy_database(source, database, cancelled=cancelled, progress=progress)
        metadata = validate_database(database, cancelled=cancelled)
        database.close()
        snapshot = FileSnapshot(
            Path(metadata["root"]),
            database_path,
            temporary_directory,
            metadata["entry_count"],
            metadata["error_count"],
        )
        snapshot.name = metadata["name"]
        snapshot.captured_at = metadata["captured_at"]
        snapshot.source_path = source_path
        return snapshot
    except BaseException:
        database.close()
        temporary_directory.cleanup()
        raise
    finally:
        source.close()
        database.close()
