from __future__ import annotations

import json
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from snapshot import FileSnapshot, finalize_database, initialize_database
from snapshot_store import check_cancelled, copy_database
from sync_plan import SyncAction


@dataclass(slots=True)
class SyncPreview:
    before: tuple[FileSnapshot, FileSnapshot]
    after: tuple[FileSnapshot, FileSnapshot]

    def close(self):
        for snapshot in (*self.before, *self.after):
            snapshot.close()


def build_sync_preview(plan, *, cancelled=None, progress=None):
    if not plan.can_apply:
        raise ValueError("Resolve blocking synchronization issues before previewing")
    snapshots = []

    def insert(database, path, values, *, planned=False):
        relative = PurePosixPath(path)
        database.execute(
            "INSERT OR REPLACE INTO entries VALUES (?,?,?,?,?,?,?,?)",
            (
                path,
                "" if relative.parent == PurePosixPath(".") else str(relative.parent),
                relative.name,
                int(values["is_directory"]),
                int(values.get("is_symlink", False)),
                values.get("size", 0),
                values.get("modified_ns", 0),
                None if planned else values.get("identity"),
            ),
        )

    def build(side, before=None):
        temporary = tempfile.TemporaryDirectory(prefix="file-tree-viewer-sync-preview-")
        path = Path(temporary.name) / "snapshot.sqlite3"
        database = sqlite3.connect(path)
        try:
            if before is None:
                initialize_database(database)
                cursor = plan.database.execute(
                    "SELECT path,data FROM inventory WHERE side=? ORDER BY path",
                    (side,),
                )
                try:
                    for position, (relative, data) in enumerate(cursor):
                        check_cancelled(cancelled)
                        insert(database, relative, json.loads(data))
                        if progress and position % 512 == 0:
                            progress("Loading staged inventory", position)
                finally:
                    cursor.close()
                finalize_database(database)
            else:
                copy_database(
                    before._connection(),
                    database,
                    cancelled=cancelled,
                    progress=progress,
                )
                for position, operation in enumerate(plan.operations()):
                    check_cancelled(cancelled)
                    if operation.destination_side != side:
                        continue
                    relative = operation.path
                    if operation.action is SyncAction.DELETE:
                        for table in ("entries", "errors"):
                            database.execute(
                                f"DELETE FROM {table} WHERE path=? OR (path>=? AND path<?)",
                                (relative, relative + "/", relative + "0"),
                            )
                    elif operation.action is SyncAction.MKDIR:
                        insert(database, relative, {"is_directory": True}, planned=True)
                    else:
                        insert(database, relative, operation.source, planned=True)
                    if progress and position % 512 == 0:
                        progress("Simulating synchronization", position)
                database.commit()
            count = database.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
            check_cancelled(cancelled)
            result = FileSnapshot(plan.roots[side], path, temporary, count, 0)
            snapshots.append(result)
            return result
        except BaseException:
            database.close()
            temporary.cleanup()
            raise
        finally:
            database.close()

    try:
        before = tuple(build(side) for side in (0, 1))
        after = tuple(build(side, before[side]) for side in (0, 1))
        return SyncPreview(before, after)
    except BaseException:
        for snapshot in snapshots:
            snapshot.close()
        raise
    finally:
        for snapshot in snapshots:
            snapshot.release_thread_connection()
