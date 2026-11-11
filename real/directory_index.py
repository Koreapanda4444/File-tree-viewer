from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

from fs_paths import is_hidden, is_reparse_point


@dataclass(slots=True)
class DirectoryEntry:
    path: Path
    name: str
    is_directory: bool
    is_link: bool
    size: int | None = None
    modified: float | None = None
    details_loaded: bool = False


def entry_group(entry, grouping):
    if grouping == "None":
        return ""
    if grouping == "Type":
        return (
            "Folders"
            if entry.is_directory
            else entry.path.suffix.casefold() or "No extension"
        )
    if grouping == "Date":
        try:
            return (
                datetime.fromtimestamp(entry.modified).astimezone().strftime("%Y-%m")
                if entry.modified is not None
                else "Unknown date"
            )
        except (ValueError, OverflowError, OSError):
            return "Unknown date"
    if entry.is_directory:
        return "Folders"
    size = entry.size
    return (
        "Unknown size"
        if size is None
        else "Empty"
        if size == 0
        else "Small (<1 MiB)"
        if size < 1024**2
        else "Medium (<100 MiB)"
        if size < 100 * 1024**2
        else "Large (>=100 MiB)"
    )


def entry_key(entry, column):
    name = (entry.name.casefold(), entry.name)
    if column == 1:
        return ("" if entry.is_directory else entry.path.suffix.casefold(), name)
    if column in (2, 3):
        value = entry.size if column == 2 else entry.modified
        return (value if value is not None else -1, name)
    return name


class IndexSignals(QObject):
    finished = Signal(object, int, object, str)


class DirectoryIndexJob(QRunnable):
    def __init__(self, node, generation, hidden, options):
        super().__init__()
        self.node, self.generation = node, generation
        self.hidden, self.options = hidden, options
        self.cancelled = threading.Event()
        self.signals = IndexSignals()

    def run(self):
        entries, error = [], ""
        column, descending, grouping, folders_first = self.options
        details_needed = column in (2, 3) or grouping in ("Date", "Size")
        try:
            with os.scandir(self.node.path) as iterator:
                for item in iterator:
                    if self.cancelled.is_set():
                        return
                    if not self.hidden and is_hidden(item):
                        continue
                    try:
                        link = is_reparse_point(item)
                        entry = DirectoryEntry(
                            Path(item.path),
                            item.name,
                            item.is_dir(follow_symlinks=False) and not link,
                            link,
                        )
                        if details_needed:
                            try:
                                details = item.stat(follow_symlinks=False)
                                entry.size = (
                                    None if entry.is_directory else details.st_size
                                )
                                entry.modified = details.st_mtime
                            except OSError:
                                pass
                            entry.details_loaded = True
                        entries.append(entry)
                    except OSError:
                        continue
            entries.sort(
                key=lambda item: (entry_group(item, grouping), entry_key(item, column)),
                reverse=descending,
            )
            if folders_first:
                entries.sort(key=lambda item: not item.is_directory)
        except OSError as exception:
            error = str(exception)
        finally:
            self.signals.finished.emit(
                self.node,
                self.generation,
                None if self.cancelled.is_set() else entries,
                error,
            )
