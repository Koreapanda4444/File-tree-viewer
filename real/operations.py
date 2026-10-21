from __future__ import annotations

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


class FileOperationWorker(QObject):
    progress = Signal(str, int, int, str)
    finished = Signal(str, object, object)

    def __init__(self, action: str, **kwargs) -> None:
        super().__init__()
        self.task = FileOperationTask(action, **kwargs)

    @Slot()
    def run(self) -> None:
        changes, errors = self.task.run(progress=self.progress.emit)
        self.finished.emit(self.task.action, changes, errors)


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
