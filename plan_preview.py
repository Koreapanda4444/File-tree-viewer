from __future__ import annotations

import sqlite3
import tempfile
import time
from pathlib import Path, PurePosixPath

from plan_diff import simulate_plan
from planning import ConflictPolicy, FilePlan, PlanAction
from snapshot import FileSnapshot, count_errors
from snapshot_store import check_cancelled, copy_database


def build_plan_preview(
    snapshot: FileSnapshot, plan: FilePlan, *, cancelled=None, progress=None
) -> FileSnapshot:
    frozen = FilePlan(plan.root)
    frozen.extend(plan.operations)
    simulation = simulate_plan(
        snapshot, frozen, validate_live=False, cancelled=cancelled
    )
    if not simulation.can_apply:
        raise ValueError(
            "Resolve Plan conflicts before previewing the resulting structure"
        )
    temporary = tempfile.TemporaryDirectory(prefix="file-tree-viewer-preview-")
    path = Path(temporary.name) / "preview.sqlite3"
    database = sqlite3.connect(path)
    operations = [
        op for op in frozen.operations if op.conflict_policy is not ConflictPolicy.SKIP
    ]

    def where(path):
        text = path.as_posix()
        return text, text + "/", text + "0"

    def remove(path):
        parameters = where(path)
        for table in ("entries", "errors"):
            database.execute(
                f"DELETE FROM {table} WHERE path=? OR (path>=? AND path<?)", parameters
            )

    def insert(entry_path, directory, *, values=None):
        name = entry_path.name
        parent = (
            "" if entry_path.parent == PurePosixPath(".") else str(entry_path.parent)
        )
        remaining = (
            values
            if values is not None
            else (int(directory), 0, 0, time.time_ns(), None)
        )
        database.execute(
            "INSERT INTO entries(path,parent,name,is_directory,is_symlink,size,modified_ns,identity) VALUES (?,?,?,?,?,?,?,?)",
            (str(entry_path), parent, name, *remaining),
        )

    try:
        copy_database(
            snapshot._connection(), database, cancelled=cancelled, progress=progress
        )
        database.execute(
            "CREATE TEMP TABLE staged AS SELECT '' AS operation_id, entries.*, errors.message AS error FROM entries LEFT JOIN errors ON errors.path=entries.path WHERE 0"
        )
        moves = [
            op
            for op in operations
            if op.action in {PlanAction.MOVE, PlanAction.RENAME, PlanAction.COPY}
        ]
        for op in moves:
            check_cancelled(cancelled)
            database.execute(
                "INSERT INTO staged SELECT ?, entries.*, errors.message FROM entries LEFT JOIN errors ON errors.path=entries.path WHERE entries.path=? OR (entries.path>=? AND entries.path<?)",
                (op.operation_id, *where(op.source)),
            )
        database.execute("CREATE INDEX staged_operation ON staged(operation_id,path)")
        for position, op in enumerate(operations):
            check_cancelled(cancelled)
            if progress:
                progress("Simulating folder structure", position)
            if op.source is not None and op.action is not PlanAction.COPY:
                remove(op.source)
        for op in operations:
            if op.conflict_policy is ConflictPolicy.OVERWRITE and op.target is not None:
                remove(op.target)
        for op in sorted(
            (op for op in operations if op.action is PlanAction.CREATE_FOLDER),
            key=lambda op: len(op.target.parts),
        ):
            check_cancelled(cancelled)
            insert(op.target, True)
        for op in moves:
            check_cancelled(cancelled)
            cursor = database.execute(
                "SELECT path,is_directory,is_symlink,size,modified_ns,identity,error FROM staged WHERE operation_id=? ORDER BY path",
                (op.operation_id,),
            )
            for old, directory, link, size, modified, identity, error in cursor:
                check_cancelled(cancelled)
                target = PurePosixPath(str(op.target) + old[len(str(op.source)) :])
                insert(
                    target,
                    directory,
                    values=(
                        directory,
                        link,
                        size,
                        modified,
                        None if op.action is PlanAction.COPY else identity,
                    ),
                )
                if error:
                    database.execute(
                        "INSERT OR REPLACE INTO errors VALUES (?,?)",
                        (str(target), error),
                    )
        for op in operations:
            check_cancelled(cancelled)
            if op.action is PlanAction.CREATE_FILE:
                insert(op.target, False)
        database.commit()
        count = database.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        errors = count_errors(database)
        database.close()
        result = FileSnapshot(snapshot.root, path, temporary, count, errors)
        result.name = snapshot.name + " (planned)"
        return result
    except BaseException:
        database.close()
        temporary.cleanup()
        raise
