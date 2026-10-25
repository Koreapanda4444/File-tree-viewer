from __future__ import annotations

import os
import sqlite3
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from fs_paths import checked_boundary, is_link_stat
from snapshot_store import check_cancelled


@dataclass(frozen=True, slots=True)
class UsageEntry:
    path: str
    parent: str
    directory: bool
    logical: int
    unique: int
    allocated: int
    error: str


class DiskUsage:
    def __init__(self, root, temporary, database_path):
        self.root = root
        self.temporary = temporary
        self.database_path = database_path
        self.closed = False

    def query(self, sql, parameters=()):
        if self.closed:
            raise ValueError("Disk analysis is closed")
        database = sqlite3.connect(self.database_path)
        try:
            return database.execute(sql, parameters).fetchall()
        finally:
            database.close()

    def entry(self, path=""):
        rows = self.query("SELECT * FROM entries WHERE path=?", (path,))
        return UsageEntry(*rows[0]) if rows else None

    def children(self, path="", *, limit=400, offset=0):
        if offset < 0:
            raise ValueError("Invalid disk usage page")
        return tuple(
            UsageEntry(*row)
            for row in self.query(
                "SELECT * FROM entries WHERE parent=? AND path<>? ORDER BY logical DESC,path LIMIT ? OFFSET ?",
                (path, path, max(1, min(limit, 4096)), offset),
            )
        )

    def largest(self, *, limit=100):
        return tuple(
            UsageEntry(*row)
            for row in self.query(
                "SELECT * FROM entries WHERE directory=0 ORDER BY logical DESC,path LIMIT ?",
                (max(1, min(limit, 4096)),),
            )
        )

    @property
    def error_count(self):
        return self.query("SELECT COUNT(*) FROM entries WHERE error<>''")[0][0]

    def close(self):
        if not self.closed:
            self.closed = True
            self.temporary.cleanup()


def scan_disk_usage(root, *, cancelled=None, progress=None):
    root = Path(root).expanduser().absolute()
    checked_boundary(root, root, allow_leaf_link=False)
    if not root.is_dir():
        raise NotADirectoryError(root)
    temporary = tempfile.TemporaryDirectory(prefix="file-tree-viewer-usage-")
    database_path = Path(temporary.name) / "usage.sqlite3"
    database = sqlite3.connect(database_path)
    try:
        database.execute("PRAGMA journal_mode=OFF")
        database.execute("PRAGMA cache_size=-2048")
        database.execute(
            "CREATE TABLE entries(path TEXT PRIMARY KEY,parent TEXT,directory INTEGER,logical INTEGER,unique_size INTEGER,allocated INTEGER,error TEXT) WITHOUT ROWID"
        )
        database.execute("CREATE INDEX usage_parent ON entries(parent,logical DESC)")
        database.execute(
            "CREATE TABLE seen(device TEXT,inode TEXT,PRIMARY KEY(device,inode)) WITHOUT ROWID"
        )
        database.execute("INSERT INTO entries VALUES ('','',1,0,0,0,'')")
        stack = [(root, False)]
        count = 0
        while stack:
            check_cancelled(cancelled)
            current, exiting = stack.pop()
            relative = current.relative_to(root).as_posix()
            relative = "" if relative == "." else relative
            if exiting:
                totals = database.execute(
                    "SELECT COALESCE(SUM(logical),0),COALESCE(SUM(unique_size),0),COALESCE(SUM(allocated),0) FROM entries WHERE parent=? AND path<>?",
                    (relative, relative),
                ).fetchone()
                database.execute(
                    "UPDATE entries SET logical=?,unique_size=?,allocated=? WHERE path=?",
                    (*totals, relative),
                )
                continue
            stack.append((current, True))
            try:
                checked_boundary(root, current, allow_leaf_link=False)
                with os.scandir(current) as iterator:
                    for item in iterator:
                        check_cancelled(cancelled)
                        if item.name.casefold() == ".file-tree-viewer":
                            continue
                        name = (Path(relative) / item.name).as_posix()
                        directory = False
                        logical = unique = allocated = 0
                        error = ""
                        try:
                            details = item.stat(follow_symlinks=False)
                            if is_link_stat(details):
                                error = "Link or reparse point skipped"
                            elif stat.S_ISDIR(details.st_mode):
                                directory = True
                            elif stat.S_ISREG(details.st_mode):
                                logical = details.st_size
                                fresh = True
                                if details.st_ino:
                                    cursor = database.execute(
                                        "INSERT OR IGNORE INTO seen VALUES (?,?)",
                                        (str(details.st_dev), str(details.st_ino)),
                                    )
                                    fresh = cursor.rowcount == 1
                                if fresh:
                                    unique = logical
                                    allocated = getattr(details, "st_blocks", None)
                                    allocated = (
                                        logical
                                        if allocated is None
                                        else allocated * 512
                                    )
                            else:
                                error = "Special file skipped"
                        except OSError as failure:
                            error = str(failure)
                        database.execute(
                            "INSERT INTO entries VALUES (?,?,?,?,?,?,?)",
                            (
                                name,
                                relative,
                                int(directory),
                                logical,
                                unique,
                                allocated,
                                error,
                            ),
                        )
                        if directory:
                            stack.append((Path(item.path), False))
                        count += 1
                        if progress and count % 256 == 0:
                            progress("Scanning disk usage", count)
            except (OSError, ValueError) as failure:
                database.execute(
                    "UPDATE entries SET error=? WHERE path=?", (str(failure), relative)
                )
        database.commit()
        check_cancelled(cancelled)
        return DiskUsage(root, temporary, database_path)
    except BaseException:
        database.close()
        temporary.cleanup()
        raise
    finally:
        database.close()
