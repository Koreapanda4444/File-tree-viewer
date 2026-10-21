from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, Qt, Signal, Slot
from PySide6.QtGui import QImageReader

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

    def __init__(self, path: Path, **kwargs) -> None:
        super().__init__()
        self.task = FilePreviewTask(path, **kwargs)

    def cancel(self) -> None:
        self.task.cancel()

    @Slot()
    def run(self) -> None:
        result = self.task.run()
        if result.image_bytes is not None:
            buffer = QBuffer()
            buffer.setData(QByteArray(result.image_bytes))
            buffer.open(QIODevice.OpenModeFlag.ReadOnly)
            reader = QImageReader(buffer)
            reader.setAllocationLimit(64)
            reader.setAutoTransform(True)
            size = reader.size()
            if size.isValid() and size.width() * size.height() <= 50000000:
                if size.width() > 1024 or size.height() > 1024:
                    size.scale(1024, 1024, Qt.AspectRatioMode.KeepAspectRatio)
                reader.setScaledSize(size)
                image = reader.read()
                if not image.isNull():
                    result = replace(result, image=image, image_bytes=None)
                else:
                    result = replace(
                        result,
                        text="Image preview unavailable: "
                        + reader.errorString()
                        + "\n\n"
                        + result.text,
                        image_bytes=None,
                    )
            else:
                result = replace(
                    result,
                    text="Image dimensions exceed the preview limit or are invalid.\n\n"
                    + result.text,
                    image_bytes=None,
                )
            buffer.close()
        self.finished.emit(result)


__all__ = [
    "FilePreviewWorker",
    "PreviewResult",
    "decode_text",
    "decode_with_encoding",
    "detect_newline",
    "looks_binary",
    "save_text_atomic",
]
