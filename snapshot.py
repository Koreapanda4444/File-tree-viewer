from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from fs_paths import is_reparse_point
from planning import normalize_plan_path, normalize_root

SNAPSHOT_BATCH_SIZE = 1_024
PROGRESS_INTERVAL = 2_048
QUERY_PAGE_SIZE = 512
LOOKUP_BATCH_SIZE = 500


class SnapshotCancelled(Exception):
    pass


@dataclass(frozen=True, slots=True)
class SnapshotEntry:
    path: PurePosixPath
    parent: PurePosixPath | None
    name: str
    is_directory: bool
    is_symlink: bool
    size: int
    modified_ns: int
    identity: str | None
    error: str | None


@dataclass(frozen=True, slots=True)
class SnapshotError:
    path: PurePosixPath | None
    message: str


class FileSnapshot:
    def __init__(
        self,
        root: Path,
        database_path: Path,
        temporary_directory: tempfile.TemporaryDirectory[str],
        entry_count: int,
        error_count: int,
    ) -> None:
        self.root = root
        self.database_path = database_path
        self.entry_count = entry_count
        self.error_count = error_count
        self._temporary_directory = temporary_directory
        self._connections: dict[int, sqlite3.Connection] = {}
        self._connection_lock = threading.RLock()
        self._closed = False

    def child_count(self, parent: PurePosixPath | str | None = None) -> int:
        parent_key = snapshot_path_key(parent)
        row = (
            self._connection()
            .execute(
                "SELECT COUNT(*) FROM entries WHERE parent = ?",
                (parent_key,),
            )
            .fetchone()
        )
        return int(row[0]) if row is not None else 0

    def children(
        self,
        parent: PurePosixPath | str | None = None,
        *,
        offset: int = 0,
        limit: int = QUERY_PAGE_SIZE,
    ) -> tuple[SnapshotEntry, ...]:
        if offset < 0 or limit <= 0:
            raise ValueError("Snapshot page values must be positive")
        parent_key = snapshot_path_key(parent)
        rows = (
            self._connection()
            .execute(
                entry_select()
                + " WHERE entries.parent = ?"
                + " ORDER BY entries.is_directory DESC, "
                + "entries.name COLLATE NOCASE, entries.name LIMIT ? OFFSET ?",
                (parent_key, limit, offset),
            )
            .fetchall()
        )
        return tuple(snapshot_entry_from_row(row) for row in rows)

    def entry(self, path: PurePosixPath | str) -> SnapshotEntry | None:
        path_key = normalize_plan_path(path).as_posix()
        row = (
            self._connection()
            .execute(
                entry_select() + " WHERE entries.path = ?",
                (path_key,),
            )
            .fetchone()
        )
        return snapshot_entry_from_row(row) if row is not None else None

    def entries_by_path(
        self,
        paths,
    ) -> dict[PurePosixPath, SnapshotEntry | None]:
        results: dict[PurePosixPath, SnapshotEntry | None] = {}
        database = self._connection()
        batch = []

        def load_batch():
            if not batch:
                return
            placeholders = ",".join("?" for _ in batch)
            for row in database.execute(
                entry_select() + f" WHERE entries.path IN ({placeholders})",
                tuple(path.as_posix() for path in batch),
            ):
                entry = snapshot_entry_from_row(row)
                results[entry.path] = entry
            batch.clear()

        for path in paths:
            if path is None:
                continue
            normalized = normalize_plan_path(path)
            if normalized in results:
                continue
            results[normalized] = None
            batch.append(normalized)
            if len(batch) >= LOOKUP_BATCH_SIZE:
                load_batch()
        load_batch()
        return results

    def iter_entries(
        self, batch_size: int = SNAPSHOT_BATCH_SIZE
    ) -> Iterator[SnapshotEntry]:
        if batch_size <= 0:
            raise ValueError("Snapshot batch size must be positive")
        cursor = self._connection().execute(entry_select() + " ORDER BY entries.path")
        while rows := cursor.fetchmany(batch_size):
            for row in rows:
                yield snapshot_entry_from_row(row)

    def errors(
        self,
        *,
        offset: int = 0,
        limit: int = QUERY_PAGE_SIZE,
    ) -> tuple[SnapshotError, ...]:
        if offset < 0 or limit <= 0:
            raise ValueError("Snapshot page values must be positive")
        rows = (
            self._connection()
            .execute(
                "SELECT path, message FROM errors ORDER BY path LIMIT ? OFFSET ?",
                (limit, offset),
            )
            .fetchall()
        )
        return tuple(
            SnapshotError(PurePosixPath(path) if path else None, message)
            for path, message in rows
        )

    def children_after(
        self,
        parent=None,
        *,
        after: SnapshotEntry | None = None,
        limit: int = QUERY_PAGE_SIZE,
    ) -> tuple[SnapshotEntry, ...]:
        if limit <= 0:
            raise ValueError("Snapshot page size must be positive")
        if after is None:
            return self.children(parent, limit=limit)
        parent_key = snapshot_path_key(parent)
        if snapshot_path_key(after.parent) != parent_key:
            raise ValueError("Snapshot cursor belongs to a different folder")
        database = self._connection()
        rows = database.execute(
            entry_select()
            + " WHERE entries.parent = ? AND entries.is_directory = ? AND (entries.name COLLATE NOCASE, entries.name) > (?, ?) ORDER BY entries.name COLLATE NOCASE, entries.name LIMIT ?",
            (parent_key, int(after.is_directory), after.name, after.name, limit),
        ).fetchall()
        if after.is_directory and len(rows) < limit:
            rows.extend(
                database.execute(
                    entry_select()
                    + " WHERE entries.parent = ? AND entries.is_directory = 0 ORDER BY entries.name COLLATE NOCASE, entries.name LIMIT ?",
                    (parent_key, limit - len(rows)),
                ).fetchall()
            )
        return tuple(snapshot_entry_from_row(row) for row in rows)

    def close(self) -> None:
        with self._connection_lock:
            if self._closed:
                return
            self._closed = True
            for database in self._connections.values():
                database.close()
            self._connections.clear()
            self._temporary_directory.cleanup()

    def release_thread_connection(self) -> None:
        with self._connection_lock:
            database = self._connections.pop(threading.get_ident(), None)
            if database is not None:
                database.close()

    def _connection(self) -> sqlite3.Connection:
        with self._connection_lock:
            if self._closed:
                raise RuntimeError("The snapshot is closed")
            identity = threading.get_ident()
            database = self._connections.get(identity)
            if database is None:
                database = open_snapshot_database(self.database_path)
                self._connections[identity] = database
            return database


def build_snapshot(
    root: Path | str,
    *,
    cancelled: threading.Event | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> FileSnapshot:
    root_path = normalize_root(root)
    if root_path is None or not root_path.is_dir():
        raise NotADirectoryError(root)

    temporary_directory = tempfile.TemporaryDirectory(
        prefix="file-tree-viewer-snapshot-"
    )
    database_path = Path(temporary_directory.name) / "snapshot.sqlite3"
    database = sqlite3.connect(database_path)
    iterators: list[tuple[os.ScandirIterator[str], tuple[object, ...], str]] = []
    active_directories: set[tuple[object, ...]] = set()
    entry_rows: list[tuple[object, ...]] = []
    error_rows: list[tuple[str, str]] = []
    scanned = 0

    def flush() -> None:
        if entry_rows:
            database.executemany(
                "INSERT INTO entries("
                "path, parent, name, is_directory, is_symlink, size, modified_ns, identity"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                entry_rows,
            )
            entry_rows.clear()
        if error_rows:
            database.executemany(
                "INSERT OR REPLACE INTO errors(path, message) VALUES (?, ?)",
                error_rows,
            )
            error_rows.clear()
        database.commit()

    def record_error(path: str, error: OSError) -> None:
        error_rows.append((path, str(error)))

    def open_directory(
        directory: Path, relative_path: str, *, required: bool = False
    ) -> None:
        try:
            details = directory.stat(follow_symlinks=False)
            identity: tuple[object, ...]
            if details.st_ino:
                identity = ("inode", details.st_dev, details.st_ino)
            else:
                identity = ("path", os.path.normcase(os.path.abspath(directory)))
            if identity in active_directories:
                return
            iterator = os.scandir(directory)
        except OSError as error:
            if required:
                raise
            record_error(relative_path, error)
            return
        active_directories.add(identity)
        iterators.append((iterator, identity, relative_path))

    try:
        initialize_database(database)
        open_directory(root_path, "", required=True)

        while iterators:
            if cancelled is not None and cancelled.is_set():
                raise SnapshotCancelled

            iterator, identity, parent_path = iterators[-1]
            try:
                entry = next(iterator)
            except StopIteration:
                iterator.close()
                iterators.pop()
                active_directories.discard(identity)
                continue
            except OSError as error:
                record_error(parent_path, error)
                iterator.close()
                iterators.pop()
                active_directories.discard(identity)
                continue

            relative_path = f"{parent_path}/{entry.name}" if parent_path else entry.name
            if not parent_path and entry.name.casefold() == ".file-tree-viewer":
                continue
            scanned += 1
            is_directory = False
            is_symlink = False
            size = 0
            modified_ns = 0
            file_identity = None

            try:
                is_directory = entry.is_dir(follow_symlinks=False)
                is_symlink = is_reparse_point(entry)
                details = entry.stat(follow_symlinks=False)
                size = 0 if is_directory else details.st_size
                modified_ns = getattr(
                    details,
                    "st_mtime_ns",
                    int(details.st_mtime * 1_000_000_000),
                )
                if details.st_ino:
                    file_identity = f"{details.st_dev}:{details.st_ino}"
            except OSError as error:
                record_error(relative_path, error)

            entry_rows.append(
                (
                    relative_path,
                    parent_path,
                    entry.name,
                    int(is_directory),
                    int(is_symlink),
                    size,
                    modified_ns,
                    file_identity,
                )
            )

            if is_directory and not is_symlink:
                open_directory(Path(entry.path), relative_path)

            if len(entry_rows) >= SNAPSHOT_BATCH_SIZE:
                flush()
            if progress is not None and scanned % PROGRESS_INTERVAL == 0:
                flush()
                progress(scanned, count_errors(database))

        flush()
        error_count = count_errors(database)
        finalize_database(database)
        if progress is not None:
            progress(scanned, error_count)
        database.close()
        return FileSnapshot(
            root_path,
            database_path,
            temporary_directory,
            scanned,
            error_count,
        )
    except BaseException:
        database.close()
        temporary_directory.cleanup()
        raise
    finally:
        while iterators:
            iterator, identity, _ = iterators.pop()
            iterator.close()
            active_directories.discard(identity)


def initialize_database(database: sqlite3.Connection) -> None:
    database.execute("PRAGMA journal_mode=OFF")
    database.execute("PRAGMA synchronous=OFF")
    database.execute("PRAGMA locking_mode=EXCLUSIVE")
    database.execute("PRAGMA cache_size=-8192")
    database.execute(
        "CREATE TABLE entries ("
        "path TEXT PRIMARY KEY, "
        "parent TEXT NOT NULL, "
        "name TEXT NOT NULL, "
        "is_directory INTEGER NOT NULL, "
        "is_symlink INTEGER NOT NULL, "
        "size INTEGER NOT NULL, "
        "modified_ns INTEGER NOT NULL, "
        "identity TEXT) WITHOUT ROWID"
    )
    database.execute(
        "CREATE TABLE errors ("
        "path TEXT PRIMARY KEY, "
        "message TEXT NOT NULL) WITHOUT ROWID"
    )
    database.commit()


def count_errors(database: sqlite3.Connection) -> int:
    row = database.execute("SELECT COUNT(*) FROM errors").fetchone()
    return int(row[0]) if row is not None else 0


def snapshot_path_key(path: PurePosixPath | str | None) -> str:
    if path is None or path == "":
        return ""
    return normalize_plan_path(path).as_posix()


def entry_select() -> str:
    return (
        "SELECT entries.path, entries.parent, entries.name, "
        "entries.is_directory, entries.is_symlink, entries.size, "
        "entries.modified_ns, entries.identity, errors.message "
        "FROM entries LEFT JOIN errors ON errors.path = entries.path"
    )


def snapshot_entry_from_row(row: tuple[object, ...]) -> SnapshotEntry:
    path, parent, name, is_directory, is_symlink, size, modified_ns, identity, error = (
        row
    )
    return SnapshotEntry(
        path=PurePosixPath(str(path)),
        parent=PurePosixPath(str(parent)) if parent else None,
        name=str(name),
        is_directory=bool(is_directory),
        is_symlink=bool(is_symlink),
        size=int(size),
        modified_ns=int(modified_ns),
        identity=str(identity) if identity is not None else None,
        error=str(error) if error is not None else None,
    )


def finalize_database(database: sqlite3.Connection) -> None:
    database.execute(
        "CREATE INDEX entries_parent ON entries("
        "parent, is_directory DESC, name COLLATE NOCASE, name)"
    )
    database.execute(
        "CREATE INDEX entries_largest ON entries(is_directory, size DESC, path)"
    )
    database.execute(
        "CREATE INDEX entries_duplicates ON entries("
        "is_directory, is_symlink, size, path)"
    )
    database.execute("ANALYZE")
    database.commit()


def open_snapshot_database(path: Path) -> sqlite3.Connection:
    database = sqlite3.connect(
        path.resolve().as_uri() + "?mode=ro&immutable=1",
        uri=True,
        check_same_thread=False,
    )
    database.execute("PRAGMA query_only=ON")
    database.execute("PRAGMA cache_size=-2048")
    database.execute("PRAGMA temp_store=FILE")
    return database
