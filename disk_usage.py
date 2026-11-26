from __future__ import annotations

import os
import sqlite3
import stat
import tempfile
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from file_content import stat_identity
from fs_ops import file_signature, require_unchanged
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
    def __init__(self, root, temporary, database_path, stats=None):
        self.root = root
        self.temporary = temporary
        self.database_path = database_path
        self.closed = False
        self.stats = stats or {}

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


def scan_disk_usage(root, *, cancelled=None, progress=None, verify=False):
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
        stats = {
            "files": 0,
            "folders": 0,
            "skipped": 0,
            "allocated_estimated": False,
            "unique_estimated": False,
            "changed": False,
        }
        if verify:
            database.execute(
                "CREATE TABLE observations(path TEXT PRIMARY KEY, device TEXT, inode TEXT, mode INTEGER, size INTEGER, modified TEXT) WITHOUT ROWID"
            )
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
                if verify:
                    observed = stat_identity(current.lstat())
                    database.execute(
                        "INSERT INTO observations VALUES (?,?,?,?,?,?)",
                        (relative, *map(str, observed)),
                    )
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
                                stats["skipped"] += 1
                            elif stat.S_ISDIR(details.st_mode):
                                directory = True
                                stats["folders"] += 1
                            elif stat.S_ISREG(details.st_mode):
                                logical = details.st_size
                                stats["files"] += 1
                                if verify:
                                    database.execute(
                                        "INSERT INTO observations VALUES (?,?,?,?,?,?)",
                                        (name, *map(str, stat_identity(details))),
                                    )
                                fresh = True
                                if details.st_ino:
                                    cursor = database.execute(
                                        "INSERT OR IGNORE INTO seen VALUES (?,?)",
                                        (str(details.st_dev), str(details.st_ino)),
                                    )
                                    fresh = cursor.rowcount == 1
                                else:
                                    stats["unique_estimated"] = True
                                if fresh:
                                    unique = logical
                                    allocated = getattr(details, "st_blocks", None)
                                    if allocated is None:
                                        stats["allocated_estimated"] = True
                                    allocated = (
                                        logical
                                        if allocated is None
                                        else allocated * 512
                                    )
                            else:
                                error = "Special file skipped"
                                stats["skipped"] += 1
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
        if verify:
            for number, row in enumerate(
                database.execute("SELECT * FROM observations"), 1
            ):
                check_cancelled(cancelled)
                name, *expected = row
                try:
                    checked_boundary(root, root / name, allow_leaf_link=False)
                    if tuple(map(str, stat_identity((root / name).lstat()))) != tuple(
                        map(str, expected)
                    ):
                        raise FileExistsError("Item changed during inspection")
                except (OSError, ValueError) as error:
                    stats["changed"] = True
                    database.execute(
                        "UPDATE entries SET error=? WHERE path=?", (str(error), name)
                    )
                if progress and number % 256 == 0:
                    progress("Verifying folder observations", number)
        database.commit()
        check_cancelled(cancelled)
        return DiskUsage(root, temporary, database_path, stats)
    except BaseException:
        database.close()
        temporary.cleanup()
        raise
    finally:
        database.close()


@dataclass(frozen=True, slots=True)
class FolderSize:
    root: Path
    signature: dict
    started: str
    completed: str
    logical: int
    unique: int
    allocated: int | None
    files: int
    folders: int
    errors: int
    skipped: int
    changed: bool
    unique_estimated: bool

    @property
    def complete(self):
        return not self.errors and not self.changed


def inspect_folder_size(root, *, cancelled=None, progress=None):
    root = Path(root).expanduser().absolute()
    signature = file_signature(root)
    started = datetime.now(timezone.utc).isoformat()
    usage = scan_disk_usage(root, cancelled=cancelled, progress=progress, verify=True)
    try:
        entry, stats = usage.entry(), usage.stats
        check_cancelled(cancelled)
        return FolderSize(
            root,
            signature,
            started,
            datetime.now(timezone.utc).isoformat(),
            entry.logical,
            entry.unique,
            None if stats["allocated_estimated"] else entry.allocated,
            stats["files"],
            stats["folders"],
            usage.error_count,
            stats["skipped"],
            stats["changed"],
            stats["unique_estimated"],
        )
    finally:
        usage.close()


class FolderSizeCache:
    """Explicit scans only; directory identity plus TTL is best-effort freshness."""

    def __init__(self, *, limit=128, ttl=30.0, clock=time.monotonic):
        if limit < 1 or ttl <= 0:
            raise ValueError("Folder cache bounds must be positive")
        self.limit, self.ttl, self.clock = limit, ttl, clock
        self.entries = OrderedDict()
        self.revision = 0

    def prune(self):
        now = self.clock()
        removed = {
            root for root, (_, expires) in self.entries.items() if now >= expires
        }
        for root in removed:
            del self.entries[root]
        return removed

    def get(self, root):
        root = Path(root).absolute()
        self.prune()
        item = self.entries.get(root)
        if item is None:
            return None
        result, _ = item
        try:
            require_unchanged(root, result.signature)
        except OSError:
            del self.entries[root]
            return None
        self.entries.move_to_end(root)
        return result

    def put(self, result, *, revision=None):
        if not result.complete or (revision is not None and revision != self.revision):
            return False
        try:
            require_unchanged(result.root, result.signature)
        except OSError:
            return False
        self.prune()
        self.entries[result.root] = result, self.clock() + self.ttl
        self.entries.move_to_end(result.root)
        while len(self.entries) > self.limit:
            self.entries.popitem(last=False)
        return True

    def invalidate(self, paths):
        paths = tuple(Path(path).absolute() for path in paths)
        self.revision += 1
        removed = {
            root
            for root in self.entries
            if any(
                root == path or root in path.parents or path in root.parents
                for path in paths
            )
        }
        for root in removed:
            del self.entries[root]
        return removed

    def results(self):
        self.prune()
        return {root: result for root, (result, _) in self.entries.items()}
