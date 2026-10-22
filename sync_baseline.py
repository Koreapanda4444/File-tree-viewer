from __future__ import annotations

import hashlib
import itertools
import json
import os
import re
import sqlite3
import tempfile
from pathlib import Path

from fs_paths import checked_boundary, normalize_plan_path, normalized_path
from journal import sync_directory


def entry_state(entry):
    if entry is None:
        return None
    return (
        {"kind": "directory"}
        if entry["is_directory"]
        else {"kind": "file", "digest": entry["digest"]}
    )


def baseline_path(roots):
    key = hashlib.sha256(
        json.dumps([normalized_path(root) for root in roots]).encode()
    ).hexdigest()
    return roots[0] / ".file-tree-viewer" / "sync-baselines" / (key + ".sqlite3")


class SyncBaseline:
    def __init__(self, database):
        self.database = database

    def state(self, side, path):
        row = self.database.execute(
            "SELECT state FROM entries WHERE side=? AND path=?", (side, path)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def subtree_unchanged(self, plan, side, path):
        def current():
            for name, data in plan.database.execute(
                "SELECT path,data FROM inventory WHERE side=? AND (path=? OR (path>=? AND path<?)) ORDER BY path",
                (side, path, path + "/", path + "0"),
            ):
                yield name, entry_state(json.loads(data))

        def previous():
            for name, data in self.database.execute(
                "SELECT path,state FROM entries WHERE side=? AND (path=? OR (path>=? AND path<?)) ORDER BY path",
                (side, path, path + "/", path + "0"),
            ):
                yield name, json.loads(data)

        return all(
            first == second
            for first, second in itertools.zip_longest(current(), previous())
        )

    def close(self):
        self.database.close()


def load_sync_baseline(roots, identities):
    path = baseline_path(roots)
    checked_boundary(roots[0], path, allow_leaf_link=False)
    pending = path.with_suffix(".pending")
    checked_boundary(roots[0], pending, allow_leaf_link=False)
    if pending.exists():
        raise ValueError(
            "Synchronization history has an unfinished commit; recover it before bidirectional planning"
        )
    if not path.exists():
        return None
    database = sqlite3.connect(
        path.as_uri() + "?mode=ro", uri=True, check_same_thread=False
    )
    try:
        if database.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("Damaged synchronization baseline")
        length = database.execute("SELECT length(data) FROM metadata").fetchone()
        if length is None or length[0] > 16384:
            raise ValueError("Invalid synchronization baseline metadata")
        metadata = json.loads(
            database.execute("SELECT data FROM metadata").fetchone()[0]
        )
        if metadata != {
            "version": 1,
            "roots": [str(root) for root in roots],
            "identities": list(identities),
        }:
            raise ValueError("Synchronization baseline roots or identities changed")
        for side, path, state in database.execute(
            "SELECT side,path,state FROM entries"
        ):
            normalize_plan_path(path)
            value = json.loads(state)
            if (
                side not in (0, 1)
                or not isinstance(value, dict)
                or value not in ({"kind": "directory"},)
                and (
                    set(value) != {"kind", "digest"}
                    or value.get("kind") != "file"
                    or not isinstance(value.get("digest"), str)
                    or not re.fullmatch("[0-9a-f]{64}", value["digest"])
                )
            ):
                raise ValueError("Invalid synchronization baseline entry")
        return SyncBaseline(database)
    except BaseException:
        database.close()
        raise


def save_sync_baseline(comparison):
    if (
        not comparison.hash_verified
        or comparison.error_count
        or comparison.counts.get("error", 0)
        or comparison.counts.get("type_conflict", 0)
    ):
        raise ValueError(
            "A complete SHA-256 comparison without errors is required for a baseline"
        )
    roots = (comparison.left_root, comparison.right_root)
    path = baseline_path(roots)
    checked_boundary(roots[0], path, allow_leaf_link=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=".baseline-", suffix=".sqlite3", dir=path.parent
    )
    os.close(descriptor)
    temporary = Path(name)
    database = sqlite3.connect(temporary)
    try:
        database.execute("CREATE TABLE metadata(data TEXT NOT NULL)")
        database.execute(
            "CREATE TABLE entries(side INTEGER,path TEXT,state TEXT,PRIMARY KEY(side,path)) WITHOUT ROWID"
        )
        database.execute(
            "INSERT INTO metadata VALUES (?)",
            (
                json.dumps(
                    {
                        "version": 1,
                        "roots": [str(root) for root in roots],
                        "identities": list(comparison.identities),
                    }
                ),
            ),
        )
        for entry in comparison.iter_entries():
            for side, data, digest in (
                (0, entry.left, entry.left_digest),
                (1, entry.right, entry.right_digest),
            ):
                if data:
                    state = entry_state({**data, "digest": digest})
                    if state["kind"] == "file" and not digest:
                        raise ValueError("Baseline file content was not verified")
                    database.execute(
                        "INSERT INTO entries VALUES (?,?,?)",
                        (side, entry.path, json.dumps(state)),
                    )
        database.commit()
        database.close()
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        database.close()
        temporary.unlink(missing_ok=True)
    return path
