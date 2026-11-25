from __future__ import annotations

from dataclasses import replace
from math import sqrt
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
                original_size = (size.width(), size.height())
                pixels = size.width() * size.height()
                if pixels > 8_000_000:
                    scale = sqrt(8_000_000 / pixels)
                    size.scale(
                        max(1, int(size.width() * scale)),
                        max(1, int(size.height() * scale)),
                        Qt.AspectRatioMode.KeepAspectRatio,
                    )
                reader.setScaledSize(size)
                image = reader.read()
                if not image.isNull():
                    result = replace(
                        result,
                        image=image,
                        image_bytes=None,
                        image_original_size=original_size,
                    )
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
