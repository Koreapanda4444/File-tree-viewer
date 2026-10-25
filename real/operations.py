from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, Signal, Slot

from file_operations import (
    FileChange,
    FileOperationTask,
    OperationRecord,
    create_item,
    normalize_sources,
    record_change,
    rename_item,
    unique_destination,
)
from fs_paths import validate_name
from operation_history import record_events


class FileOperationWorker(QObject):
    progress = Signal(str, int, int, str)
    finished = Signal(str, object, object)
    items_ready = Signal(object)

    def __init__(self, action: str, **kwargs) -> None:
        super().__init__()
        self.action = action
        self.options = kwargs
        self.task = None
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    @Slot()
    def run(self) -> None:
        batch = []
        last_emit = time.monotonic()

        def item_finished(item):
            nonlocal last_emit
            batch.append(item)
            if len(batch) >= 128 or time.monotonic() - last_emit >= 0.1:
                self.items_ready.emit(list(batch))
                batch.clear()
                last_emit = time.monotonic()

        try:
            self.task = FileOperationTask(
                self.action, cancelled=self._cancelled, **self.options
            )
            changes, errors = self.task.run(
                progress=self.progress.emit, item_finished=item_finished
            )
        except (OSError, RuntimeError, ValueError) as error:
            changes, errors = [], [str(error)]
        if batch:
            self.items_ready.emit(list(batch))
        outcomes = self.task.outcomes if self.task is not None else []
        events = [
            {
                "category": "real",
                "action": self.action,
                "status": item.status,
                "source": item.source,
                "target": item.target,
                "root": (item.source or item.target).parent
                if (item.source or item.target)
                else "",
                "details": {"message": item.message},
            }
            for item in outcomes
        ]
        if not outcomes and errors:
            events.append(
                {
                    "category": "real",
                    "action": self.action,
                    "status": "failed",
                    "details": {"errors": errors},
                }
            )
        record_events(events)
        self.finished.emit(self.action, changes, errors)


__all__ = [
    "FileChange",
    "FileOperationWorker",
    "OperationRecord",
    "create_item",
    "normalize_sources",
    "record_change",
    "rename_item",
    "unique_destination",
    "validate_name",
]
