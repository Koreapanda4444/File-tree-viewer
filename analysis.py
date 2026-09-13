from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath

from snapshot import FileSnapshot

EXTENSION_LIMIT = 200
RANKING_LIMIT = 20
ProgressCallback = Callable[[str], None]


class AnalysisCancelled(Exception):
    pass


@dataclass(frozen=True, slots=True)
class ExtensionUsage:
    extension: str
    file_count: int
    total_size: int


@dataclass(frozen=True, slots=True)
class RankedPath:
    path: PurePosixPath
    size: int


@dataclass(frozen=True, slots=True)
class StructureAnalysis:
    file_count: int
    folder_count: int
    total_size: int
    error_count: int
    extension_group_count: int
    extensions: tuple[ExtensionUsage, ...]
    largest_files: tuple[RankedPath, ...]
    largest_folders: tuple[RankedPath, ...]
    deepest_path: PurePosixPath | None
    maximum_depth: int


def analyze_snapshot(
    snapshot: FileSnapshot,
    *,
    cancelled: threading.Event | None = None,
    progress: ProgressCallback | None = None,
) -> StructureAnalysis:
    database = sqlite3.connect(snapshot.database_path)
    database.execute("PRAGMA query_only=ON")
    database.execute("PRAGMA cache_size=-8192")
    database.create_function("file_extension", 1, file_extension, deterministic=True)

    def interrupt_if_cancelled() -> int:
        return int(cancelled is not None and cancelled.is_set())

    database.set_progress_handler(interrupt_if_cancelled, 10_000)
    try:
        emit(progress, "Counting files and folders…")
        check_cancelled(cancelled)
        file_count, folder_count, total_size = database.execute(
            "SELECT "
            "SUM(CASE WHEN is_directory = 0 THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN is_directory = 1 THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN is_directory = 0 THEN size ELSE 0 END) "
            "FROM entries"
        ).fetchone()

        emit(progress, "Calculating extension distribution…")
        check_cancelled(cancelled)
        extension_group_count = int(
            database.execute(
                "SELECT COUNT(DISTINCT file_extension(name)) "
                "FROM entries WHERE is_directory = 0"
            ).fetchone()[0]
        )
        extensions = tuple(
            ExtensionUsage(str(extension), int(count), int(size))
            for extension, count, size in database.execute(
                "SELECT file_extension(name), COUNT(*), SUM(size) "
                "FROM entries WHERE is_directory = 0 "
                "GROUP BY file_extension(name) "
                "ORDER BY SUM(size) DESC, COUNT(*) DESC, file_extension(name) "
                "LIMIT ?",
                (EXTENSION_LIMIT,),
            )
        )

        emit(progress, "Finding the largest files…")
        check_cancelled(cancelled)
        largest_files = tuple(
            RankedPath(PurePosixPath(str(path)), int(size))
            for path, size in database.execute(
                "SELECT path, size FROM entries WHERE is_directory = 0 "
                "ORDER BY size DESC, path LIMIT ?",
                (RANKING_LIMIT,),
            )
        )

        emit(progress, "Calculating folder sizes…")
        check_cancelled(cancelled)
        largest_folders = tuple(
            RankedPath(PurePosixPath(str(path)), int(size))
            for path, size in database.execute(
                "WITH RECURSIVE ancestry(folder, size) AS ("
                "  SELECT parent, size FROM entries WHERE is_directory = 0 "
                "  UNION ALL "
                "  SELECT entries.parent, ancestry.size "
                "  FROM ancestry JOIN entries ON entries.path = ancestry.folder "
                "  WHERE ancestry.folder <> ''"
                "), folder_sizes AS ("
                "  SELECT folder, SUM(size) AS total_size FROM ancestry "
                "  WHERE folder <> '' GROUP BY folder"
                ") "
                "SELECT entries.path, COALESCE(folder_sizes.total_size, 0) "
                "FROM entries LEFT JOIN folder_sizes "
                "ON folder_sizes.folder = entries.path "
                "WHERE entries.is_directory = 1 "
                "ORDER BY COALESCE(folder_sizes.total_size, 0) DESC, entries.path "
                "LIMIT ?",
                (RANKING_LIMIT,),
            )
        )

        emit(progress, "Finding the deepest path…")
        check_cancelled(cancelled)
        deepest = database.execute(
            "SELECT path, "
            "length(path) - length(replace(path, '/', '')) + 1 AS depth "
            "FROM entries ORDER BY depth DESC, path LIMIT 1"
        ).fetchone()
    except sqlite3.OperationalError as error:
        if cancelled is not None and cancelled.is_set():
            raise AnalysisCancelled from error
        raise
    finally:
        database.close()

    deepest_path = PurePosixPath(str(deepest[0])) if deepest is not None else None
    maximum_depth = int(deepest[1]) if deepest is not None else 0
    return StructureAnalysis(
        file_count=int(file_count or 0),
        folder_count=int(folder_count or 0),
        total_size=int(total_size or 0),
        error_count=snapshot.error_count,
        extension_group_count=extension_group_count,
        extensions=extensions,
        largest_files=largest_files,
        largest_folders=largest_folders,
        deepest_path=deepest_path,
        maximum_depth=maximum_depth,
    )


def file_extension(name: object) -> str:
    text = str(name)
    position = text.rfind(".")
    if position <= 0 or position == len(text) - 1:
        return "(no extension)"
    return text[position:].casefold()


def check_cancelled(cancelled: threading.Event | None) -> None:
    if cancelled is not None and cancelled.is_set():
        raise AnalysisCancelled


def emit(callback: ProgressCallback | None, message: str) -> None:
    if callback is not None:
        callback(message)
