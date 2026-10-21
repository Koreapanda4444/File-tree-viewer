from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from file_preview import (
    FilePreviewTask,
    PreviewResult,
    decode_text,
    decode_with_encoding,
    detect_newline,
    looks_binary,
    save_text_atomic,
)


class FilePreviewWorker(QObject):
    finished = Signal(object)

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.task = FilePreviewTask(path)

    def cancel(self) -> None:
        self.task.cancel()

    @Slot()
    def run(self) -> None:
        self.finished.emit(self.task.run())


__all__ = [
    "FilePreviewWorker",
    "PreviewResult",
    "decode_text",
    "decode_with_encoding",
    "detect_newline",
    "looks_binary",
    "save_text_atomic",
]
