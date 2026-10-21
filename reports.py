from __future__ import annotations

import csv
import html
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from analysis import analyze_snapshot
from fs_paths import checked_boundary, normalized_path
from persistence import atomic_text_write
from snapshot_store import check_cancelled


def report_rows(snapshot, structure, diagnostics=None):
    yield {
        "category": "snapshot_summary",
        "path": str(snapshot.root),
        "related": snapshot.name,
        "value": snapshot.entry_count,
        "details": {
            "captured_at": snapshot.captured_at,
            "error_count": snapshot.error_count,
            "structure": asdict(structure),
            "duplicate_scan_included": diagnostics is not None,
        },
    }
    for entry in snapshot.iter_entries():
        yield {
            "category": "snapshot_entry",
            "path": str(entry.path),
            "related": str(entry.parent) if entry.parent else "",
            "value": entry.size,
            "details": asdict(entry),
        }
    for extension in structure.extensions:
        yield {
            "category": "extension",
            "path": extension.extension,
            "related": "",
            "value": extension.total_size,
            "details": {"file_count": extension.file_count},
        }
    for label, items in (
        ("largest_file", structure.largest_files),
        ("largest_folder", structure.largest_folders),
    ):
        for item in items:
            yield {
                "category": label,
                "path": str(item.path),
                "related": "",
                "value": item.size,
                "details": {},
            }
    if diagnostics is None:
        return
    for group in diagnostics.duplicate_groups:
        yield {
            "category": "duplicate_group",
            "path": "",
            "related": group.digest,
            "value": group.reclaimable_size,
            "details": {
                "size": group.size,
                "copies": len(group.paths),
                "physical_copies": group.physical_copies,
            },
        }
        for path in group.paths:
            yield {
                "category": "duplicate_member",
                "path": str(path),
                "related": group.digest,
                "value": group.size,
                "details": {},
            }
    for path in diagnostics.empty_folders:
        yield {
            "category": "empty_folder",
            "path": str(path),
            "related": "",
            "value": 0,
            "details": {},
        }
    for number, collision in enumerate(diagnostics.name_collisions, 1):
        for path in collision.paths:
            yield {
                "category": "name_collision",
                "path": str(path),
                "related": str(number),
                "value": 0,
                "details": {},
            }
    for category in (
        "long_paths",
        "inaccessible_items",
        "broken_links",
        "changed_items",
    ):
        for problem in getattr(diagnostics, category):
            yield {
                "category": category,
                "path": str(problem.path) if problem.path else "",
                "related": "",
                "value": 0,
                "details": problem.details,
            }


def csv_value(value) -> str:
    text = str(value)
    return (
        "'" + text if text.startswith(("=", "+", "-", "@", "\t", "\r", "\n")) else text
    )


def export_analysis_report(
    snapshot,
    path: Path | str,
    *,
    format: str,
    structure=None,
    diagnostics=None,
    cancelled=None,
    progress=None,
) -> Path:
    format = format.casefold()
    if format not in {"json", "csv", "html"}:
        raise ValueError("Choose JSON, CSV or HTML")
    destination = Path(path).expanduser().absolute()
    checked_boundary(destination.parent, destination, allow_leaf_link=False)
    if normalized_path(destination) == normalized_path(snapshot.database_path):
        raise ValueError("The Snapshot database cannot be used as report output")
    try:
        relative = destination.relative_to(snapshot.root)
    except ValueError:
        relative = None
    if relative is not None:
        if relative.parts and relative.parts[0].casefold() == ".file-tree-viewer":
            raise ValueError("Reports cannot replace recovery data")
        if snapshot.entry(relative.as_posix()) is not None:
            raise ValueError("A report cannot replace a file recorded in this Snapshot")
    check_cancelled(cancelled)
    if structure is None:
        structure = analyze_snapshot(
            snapshot,
            cancelled=cancelled,
            progress=lambda message: progress(message, 0) if progress else None,
        )
    metadata = {
        "format": "file-tree-viewer-analysis",
        "version": 1,
        "snapshot": {
            "name": snapshot.name,
            "root": str(snapshot.root),
            "captured_at": snapshot.captured_at,
            "entry_count": snapshot.entry_count,
            "error_count": snapshot.error_count,
        },
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "structure": asdict(structure),
        "duplicate_scan_included": diagnostics is not None,
    }
    fields = ("category", "path", "related", "value", "details")

    def write(stream):
        if format == "json":
            header = json.dumps(metadata, default=str, ensure_ascii=True)
            stream.write(header[:-1] + ',"records":[')
        elif format == "csv":
            writer = csv.writer(stream)
            writer.writerow(fields)
        else:
            stream.write(
                '<!doctype html><html lang="en"><meta charset="utf-8"><title>File System Analysis</title><style>body{font:14px system-ui;margin:2rem;color:#17243a}table{border-collapse:collapse;width:100%}th,td{padding:8px;border:1px solid #ccd4de;text-align:left;vertical-align:top;overflow-wrap:anywhere}th{background:#eaf0f7}tr:nth-child(even){background:#f7f9fc}pre{white-space:pre-wrap}</style><h1>File System Analysis</h1><pre>'
            )
            stream.write(
                html.escape(
                    json.dumps(metadata, default=str, ensure_ascii=False, indent=2)
                )
            )
            stream.write(
                "</pre><table><thead><tr>"
                + "".join("<th>" + field.title() + "</th>" for field in fields)
                + "</tr></thead><tbody>"
            )
        for position, row in enumerate(report_rows(snapshot, structure, diagnostics)):
            check_cancelled(cancelled)
            if progress and position % 512 == 0:
                progress("Exporting analysis records", position)
            if format == "json":
                if position:
                    stream.write(",")
                stream.write(json.dumps(row, default=str, ensure_ascii=True))
            else:
                values = [
                    json.dumps(row[field], default=str, ensure_ascii=False)
                    if field == "details"
                    else str(row[field])
                    for field in fields
                ]
                if format == "csv":
                    writer.writerow(csv_value(value) for value in values)
                else:
                    stream.write(
                        "<tr>"
                        + "".join(
                            "<td>" + html.escape(value) + "</td>" for value in values
                        )
                        + "</tr>"
                    )
        stream.write(
            "]}\n"
            if format == "json"
            else "</tbody></table></html>\n"
            if format == "html"
            else ""
        )
        check_cancelled(cancelled)

    atomic_text_write(destination, write)
    return destination
