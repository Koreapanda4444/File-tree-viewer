from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

from fs_paths import checked_boundary
from persistence import atomic_text_write
from reports import csv_value
from snapshot_store import check_cancelled


def export_search(
    database_path, destination, *, format, request, cancelled=None, progress=None
):
    destination = Path(destination).absolute()
    checked_boundary(destination.parent, destination, allow_leaf_link=False)
    if destination.exists():
        raise FileExistsError("Choose a new report filename to preserve existing files")
    if format not in {"json", "csv"}:
        raise ValueError("Choose JSON or CSV")
    database = sqlite3.connect(Path(database_path).as_uri() + "?mode=ro", uri=True)
    fields = ("name", "path", "line", "column", "snippet")

    def write(stream):
        if format == "json":
            stream.write(
                json.dumps(
                    {
                        "format": "file-tree-viewer-search",
                        "version": 1,
                        "search": request.as_dict(),
                    }
                )[:-1]
                + ',"results":['
            )
        else:
            writer = csv.writer(stream)
            writer.writerow(fields)
        for count, row in enumerate(
            database.execute(
                "SELECT name,path,line,column_number,snippet FROM results ORDER BY category,position"
            ),
            start=1,
        ):
            check_cancelled(cancelled)
            if format == "json":
                if count > 1:
                    stream.write(",")
                json.dump(
                    dict(zip(fields, row, strict=True)), stream, ensure_ascii=True
                )
            else:
                writer.writerow(csv_value(value) for value in row)
            if progress and count % 1024 == 0:
                progress(count)
        if format == "json":
            stream.write("]}\n")
        check_cancelled(cancelled)

    try:
        atomic_text_write(destination, write, overwrite=False)
    finally:
        database.close()
    return destination
