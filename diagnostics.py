from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from snapshot import FileSnapshot

HASH_CHUNK_SIZE = 1024 * 1024
LONG_PATH_THRESHOLD = 260
ProgressCallback = Callable[[str], None]


class DiagnosticCancelled(Exception):
    pass


class FileChangedError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class DuplicateGroup:
    digest: str
    size: int
    paths: tuple[PurePosixPath, ...]

    @property
    def reclaimable_size(self) -> int:
        return self.size * (len(self.paths) - 1)


@dataclass(frozen=True, slots=True)
class PathProblem:
    path: PurePosixPath | None
    details: str


@dataclass(frozen=True, slots=True)
class NameCollision:
    paths: tuple[PurePosixPath, ...]


@dataclass(frozen=True, slots=True)
class DiagnosticReport:
    duplicate_groups: tuple[DuplicateGroup, ...]
    duplicate_file_count: int
    reclaimable_size: int
    hashed_file_count: int
    hashed_bytes: int
    empty_folders: tuple[PurePosixPath, ...]
    name_collisions: tuple[NameCollision, ...]
    long_paths: tuple[PathProblem, ...]
    inaccessible_items: tuple[PathProblem, ...]
    broken_links: tuple[PathProblem, ...]
    changed_items: tuple[PathProblem, ...]

    @property
    def problem_count(self) -> int:
        return (
            len(self.empty_folders)
            + len(self.name_collisions)
            + len(self.long_paths)
            + len(self.inaccessible_items)
            + len(self.broken_links)
            + len(self.changed_items)
        )


def scan_snapshot_problems(
    snapshot: FileSnapshot,
    *,
    cancelled: threading.Event | None = None,
    progress: ProgressCallback | None = None,
    long_path_threshold: int = LONG_PATH_THRESHOLD,
) -> DiagnosticReport:
    if long_path_threshold <= 0:
        raise ValueError("The long path threshold must be positive")

    database = sqlite3.connect(snapshot.database_path)
    database.execute("PRAGMA query_only=ON")
    database.execute("PRAGMA cache_size=-8192")

    def interrupt_if_cancelled() -> int:
        return int(cancelled is not None and cancelled.is_set())

    database.set_progress_handler(interrupt_if_cancelled, 10_000)
    try:
        emit(progress, "Finding empty folders…")
        check_cancelled(cancelled)
        empty_folders = tuple(
            PurePosixPath(str(row[0]))
            for row in database.execute(
                "SELECT folder.path FROM entries AS folder "
                "LEFT JOIN errors ON errors.path = folder.path "
                "WHERE folder.is_directory = 1 AND errors.path IS NULL "
                "AND NOT EXISTS ("
                "  SELECT 1 FROM entries AS child WHERE child.parent = folder.path"
                ") ORDER BY folder.path"
            )
        )

        emit(progress, "Finding name collisions…")
        check_cancelled(cancelled)
        name_collisions = find_name_collisions(database)

        emit(progress, "Checking path lengths…")
        check_cancelled(cancelled)
        long_paths = find_long_paths(snapshot, database, long_path_threshold)

        inaccessible = {
            str(path): PathProblem(PurePosixPath(path) if path else None, str(message))
            for path, message in database.execute(
                "SELECT path, message FROM errors ORDER BY path"
            )
        }

        emit(progress, "Checking symbolic links…")
        broken_links, changed_links = check_links(
            snapshot,
            database,
            inaccessible,
            cancelled,
        )

        emit(progress, "Finding duplicate candidates…")
        check_cancelled(cancelled)
        candidate_count = int(
            database.execute(
                "SELECT COUNT(*) FROM entries AS entry "
                "LEFT JOIN errors ON errors.path = entry.path "
                "WHERE entry.is_directory = 0 AND entry.is_symlink = 0 "
                "AND errors.path IS NULL AND entry.size IN ("
                "  SELECT size FROM entries "
                "  WHERE is_directory = 0 AND is_symlink = 0 "
                "  GROUP BY size HAVING COUNT(*) > 1"
                ")"
            ).fetchone()[0]
        )
        duplicate_groups, hashed_file_count, hashed_bytes, changed_files = (
            find_duplicate_files(
                snapshot,
                database,
                inaccessible,
                candidate_count,
                cancelled,
                progress,
            )
        )
    except sqlite3.OperationalError as error:
        if cancelled is not None and cancelled.is_set():
            raise DiagnosticCancelled from error
        raise
    finally:
        database.close()

    changed_items = tuple(
        sorted((*changed_links, *changed_files), key=problem_sort_key)
    )
    duplicate_groups = tuple(
        sorted(
            duplicate_groups,
            key=lambda group: (-group.reclaimable_size, -group.size, group.digest),
        )
    )
    return DiagnosticReport(
        duplicate_groups=duplicate_groups,
        duplicate_file_count=sum(len(group.paths) for group in duplicate_groups),
        reclaimable_size=sum(group.reclaimable_size for group in duplicate_groups),
        hashed_file_count=hashed_file_count,
        hashed_bytes=hashed_bytes,
        empty_folders=empty_folders,
        name_collisions=name_collisions,
        long_paths=long_paths,
        inaccessible_items=tuple(sorted(inaccessible.values(), key=problem_sort_key)),
        broken_links=tuple(sorted(broken_links, key=problem_sort_key)),
        changed_items=changed_items,
    )


def find_name_collisions(database: sqlite3.Connection) -> tuple[NameCollision, ...]:
    collisions: list[NameCollision] = []
    current_key: tuple[str, str] | None = None
    current_paths: list[PurePosixPath] = []

    def finish_group() -> None:
        if len(current_paths) > 1:
            collisions.append(NameCollision(tuple(current_paths)))

    rows = database.execute(
        "SELECT parent, name, path FROM entries "
        "ORDER BY parent, name COLLATE NOCASE, name, path"
    )
    for parent, name, path in rows:
        key = (str(parent), str(name).casefold())
        if key != current_key:
            finish_group()
            current_key = key
            current_paths = []
        current_paths.append(PurePosixPath(str(path)))
    finish_group()
    return tuple(collisions)


def find_long_paths(
    snapshot: FileSnapshot,
    database: sqlite3.Connection,
    threshold: int,
) -> tuple[PathProblem, ...]:
    problems: list[PathProblem] = []
    for (path_text,) in database.execute("SELECT path FROM entries ORDER BY path"):
        path = PurePosixPath(str(path_text))
        length = len(os.fspath(snapshot.root.joinpath(*path.parts)))
        if length >= threshold:
            problems.append(PathProblem(path, f"{length:,} characters"))
    return tuple(problems)


def check_links(
    snapshot: FileSnapshot,
    database: sqlite3.Connection,
    inaccessible: dict[str, PathProblem],
    cancelled: threading.Event | None,
) -> tuple[list[PathProblem], list[PathProblem]]:
    broken: list[PathProblem] = []
    changed: list[PathProblem] = []
    for (path_text,) in database.execute(
        "SELECT path FROM entries WHERE is_symlink = 1 ORDER BY path"
    ):
        check_cancelled(cancelled)
        path = PurePosixPath(str(path_text))
        absolute = snapshot.root.joinpath(*path.parts)
        try:
            absolute.lstat()
        except FileNotFoundError:
            changed.append(PathProblem(path, "Missing since the Snapshot was created"))
            continue
        except OSError as error:
            inaccessible.setdefault(path.as_posix(), PathProblem(path, str(error)))
            continue
        try:
            absolute.stat()
        except FileNotFoundError:
            broken.append(PathProblem(path, "The link target does not exist"))
        except OSError as error:
            inaccessible.setdefault(path.as_posix(), PathProblem(path, str(error)))
    return broken, changed


def find_duplicate_files(
    snapshot: FileSnapshot,
    database: sqlite3.Connection,
    inaccessible: dict[str, PathProblem],
    candidate_count: int,
    cancelled: threading.Event | None,
    progress: ProgressCallback | None,
) -> tuple[list[DuplicateGroup], int, int, list[PathProblem]]:
    root = snapshot.root.resolve(strict=True)
    groups: list[DuplicateGroup] = []
    changed: list[PathProblem] = []
    hashes: dict[str, list[PurePosixPath]] = {}
    current_size: int | None = None
    hashed_file_count = 0
    hashed_bytes = 0

    def finish_size_group() -> None:
        if current_size is None:
            return
        for digest, paths in hashes.items():
            if len(paths) > 1:
                groups.append(DuplicateGroup(digest, current_size, tuple(paths)))

    rows = database.execute(
        "SELECT entry.path, entry.size, entry.modified_ns "
        "FROM entries AS entry "
        "LEFT JOIN errors ON errors.path = entry.path "
        "WHERE entry.is_directory = 0 AND entry.is_symlink = 0 "
        "AND errors.path IS NULL AND entry.size IN ("
        "  SELECT size FROM entries "
        "  WHERE is_directory = 0 AND is_symlink = 0 "
        "  GROUP BY size HAVING COUNT(*) > 1"
        ") ORDER BY entry.size, entry.path"
    )
    for path_text, size_value, modified_ns_value in rows:
        check_cancelled(cancelled)
        path = PurePosixPath(str(path_text))
        size = int(size_value)
        modified_ns = int(modified_ns_value)
        if current_size != size:
            finish_size_group()
            current_size = size
            hashes = {}

        absolute = snapshot.root.joinpath(*path.parts)
        try:
            digest = hash_snapshot_file(
                root,
                absolute,
                expected_size=size,
                expected_modified_ns=modified_ns,
                cancelled=cancelled,
            )
        except FileChangedError as error:
            changed.append(PathProblem(path, str(error)))
            continue
        except FileNotFoundError:
            changed.append(PathProblem(path, "Missing since the Snapshot was created"))
            continue
        except OSError as error:
            inaccessible.setdefault(path.as_posix(), PathProblem(path, str(error)))
            continue

        hashes.setdefault(digest, []).append(path)
        hashed_file_count += 1
        hashed_bytes += size
        if hashed_file_count % 64 == 0 or hashed_file_count == candidate_count:
            emit(
                progress,
                f"Hashing duplicate candidates: {hashed_file_count:,} / "
                f"{candidate_count:,}",
            )

    finish_size_group()
    return groups, hashed_file_count, hashed_bytes, changed


def hash_snapshot_file(
    root: Path,
    path: Path,
    *,
    expected_size: int,
    expected_modified_ns: int,
    cancelled: threading.Event | None,
) -> str:
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError as error:
        raise FileChangedError("Missing since the Snapshot was created") from error
    except RuntimeError as error:
        raise FileChangedError("The path can no longer be resolved safely") from error
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise FileChangedError(
            "The path now resolves outside the Snapshot root"
        ) from error

    details = path.lstat()
    if not stat.S_ISREG(details.st_mode):
        raise FileChangedError("The item is no longer a regular file")
    verify_snapshot_details(details, expected_size, expected_modified_ns)

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode):
            raise FileChangedError("The item is no longer a regular file")
        verify_snapshot_details(opened, expected_size, expected_modified_ns)
        while chunk := stream.read(HASH_CHUNK_SIZE):
            check_cancelled(cancelled)
            digest.update(chunk)
        verify_snapshot_details(
            os.fstat(stream.fileno()), expected_size, expected_modified_ns
        )
    return digest.hexdigest()


def verify_snapshot_details(
    details, expected_size: int, expected_modified_ns: int
) -> None:
    modified_ns = getattr(
        details,
        "st_mtime_ns",
        int(details.st_mtime * 1_000_000_000),
    )
    if details.st_size != expected_size or modified_ns != expected_modified_ns:
        raise FileChangedError("Size or modification time changed after the Snapshot")


def check_cancelled(cancelled: threading.Event | None) -> None:
    if cancelled is not None and cancelled.is_set():
        raise DiagnosticCancelled


def emit(callback: ProgressCallback | None, message: str) -> None:
    if callback is not None:
        callback(message)


def problem_sort_key(problem: PathProblem) -> tuple[str, str]:
    path = problem.path.as_posix() if problem.path is not None else ""
    return path, problem.details
