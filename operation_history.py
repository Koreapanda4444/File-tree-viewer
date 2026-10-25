from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QStandardPaths


class HistoryStore:
    fields = (
        "id",
        "time",
        "category",
        "action",
        "status",
        "source",
        "target",
        "root",
        "transaction_id",
        "journal",
        "details",
    )

    def __init__(self, path=None):
        self.path = (
            Path(path)
            if path is not None
            else Path(
                QStandardPaths.writableLocation(
                    QStandardPaths.StandardLocation.AppLocalDataLocation
                )
            )
            / "operation-history.sqlite3"
        )

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        database = sqlite3.connect(self.path, timeout=5)
        try:
            database.execute("PRAGMA journal_mode=WAL")
            database.execute(
                "CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,time TEXT NOT NULL,category TEXT NOT NULL,action TEXT NOT NULL,status TEXT NOT NULL,source TEXT NOT NULL,target TEXT NOT NULL,root TEXT NOT NULL,transaction_id TEXT NOT NULL,journal TEXT NOT NULL,details TEXT NOT NULL)"
            )
            database.execute(
                "CREATE INDEX IF NOT EXISTS history_time ON events(time DESC,id DESC)"
            )
        except BaseException:
            database.close()
            raise
        return database

    def append(self, events):
        database = self.connect()
        try:
            with database:
                database.executemany(
                    "INSERT INTO events(time,category,action,status,source,target,root,transaction_id,journal,details) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        (
                            event.get("time") or datetime.now(timezone.utc).isoformat(),
                            *(
                                str(event.get(field) or "")
                                for field in self.fields[2:-1]
                            ),
                            json.dumps(
                                event.get("details", {}),
                                ensure_ascii=False,
                                default=str,
                            ),
                        )
                        for event in events
                    ),
                )
        finally:
            database.close()

    def rows(self, *, limit=250, **filters):
        return list(self.iter_rows(limit=limit, **filters))

    def iter_rows(
        self,
        *,
        category="",
        status="",
        since="",
        until="",
        text="",
        limit=None,
        offset=0,
        cancelled=None,
    ):
        if (limit is not None and not 0 < limit <= 4096) or offset < 0:
            raise ValueError("Invalid history page")
        clauses, values = [], []
        for column, value in (("category", category), ("status", status)):
            if value:
                clauses.append(column + "=?")
                values.append(value)
        if since:
            clauses.append("time>=?")
            values.append(since)
        if until:
            clauses.append("time<?")
            values.append(until)
        if text:
            clauses.append(
                "(instr(lower(source),lower(?))>0 OR instr(lower(target),lower(?))>0 OR instr(lower(action),lower(?))>0)"
            )
            values.extend([text] * 3)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        database = self.connect()
        if cancelled is not None:
            database.set_progress_handler(lambda: int(cancelled.is_set()), 10000)
        try:
            rows = database.execute(
                "SELECT * FROM events"
                + where
                + " ORDER BY time DESC,id DESC LIMIT ? OFFSET ?",
                (*values, -1 if limit is None else limit, offset),
            )
            for row in rows:
                from snapshot_store import check_cancelled

                check_cancelled(cancelled)
                yield dict(zip(self.fields, row, strict=True))
        except sqlite3.OperationalError:
            from snapshot_store import check_cancelled

            check_cancelled(cancelled)
            raise
        finally:
            database.close()


def record_events(events):
    try:
        HistoryStore().append(events)
    except (OSError, sqlite3.Error) as error:
        logging.getLogger(__name__).warning(
            "Operation history could not be saved: %s", error
        )


def transaction_events(action, record, category):
    common = {
        "category": category,
        "status": record.status.value,
        "root": record.root,
        "transaction_id": record.transaction_id,
        "journal": record.journal_path,
    }
    yield {**common, "action": action, "details": record.as_dict()}
    for mutation in record.mutations:
        yield {
            **common,
            "action": action + ":" + mutation.kind.value,
            "source": mutation.source,
            "target": mutation.target,
            "details": {
                "operation_id": mutation.operation_id,
                "mutation_id": mutation.key,
            },
        }


def record_result(action, result, *, category="plan"):
    if hasattr(result, "journal_path"):
        record_events(transaction_events(action, result, category))
    elif hasattr(result, "journals"):

        def events():
            for index, root in enumerate(result.roots):
                journal = (
                    result.journals[index] if index < len(result.journals) else None
                )
                yield {
                    "category": "sync",
                    "action": action,
                    "status": result.status,
                    "root": root,
                    "transaction_id": result.transaction_id,
                    "journal": journal,
                    "details": {
                        "error": result.error,
                        "recovery_errors": result.recovery_errors,
                        "completed": result.completed,
                    },
                }
                if journal is not None:
                    from plan_apply import load_application_record

                    try:
                        record = load_application_record(journal)
                    except (OSError, ValueError, TypeError, KeyError) as error:
                        yield {
                            "category": "sync",
                            "action": action + ":journal",
                            "status": "failed",
                            "root": root,
                            "journal": journal,
                            "details": {"error": str(error)},
                        }
                    else:
                        for event in transaction_events(action, record, "sync"):
                            if event["action"] != action:
                                yield event

        record_events(events())
    else:
        record_events(
            [
                {
                    "category": category,
                    "action": action,
                    "status": "failed" if result.errors else "recovered",
                    "root": result.root,
                    "details": {"recovered": result.recovered, "errors": result.errors},
                }
            ]
        )
