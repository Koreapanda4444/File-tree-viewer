from __future__ import annotations

from dataclasses import replace
from math import sqrt
from pathlib import Path

from PySide6.QtCore import (
    QBuffer,
    QByteArray,
    QIODevice,
    QObject,
    QSize,
    Qt,
    Signal,
    Slot,
)
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


def decode_preview_image(result, *, pdf=False):
    if result.document_bytes is not None:
        if pdf:
            result = decode_pdf_page(result)
        result = replace(result, document_bytes=None)
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
    return result


def decode_pdf_page(result):
    # Qt's bundled PDF engine; only one page and a bounded raster are retained.
    try:
        from PySide6.QtPdf import QPdfDocument
    except ImportError:
        return replace(
            result,
            text="PDF preview is unavailable in this Qt build.\n\n" + result.text,
        )
    buffer = QBuffer()
    buffer.setData(QByteArray(result.document_bytes))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    document = QPdfDocument()
    try:
        document.load(buffer)
        if document.status() != QPdfDocument.Status.Ready or not document.pageCount():
            return replace(
                result,
                text="PDF is damaged, encrypted or unsupported.\n\n" + result.text,
            )
        points = document.pagePointSize(0)
        width, height = points.width(), points.height()
        if not (0 < width <= 100000 and 0 < height <= 100000):
            return replace(result, text="PDF page dimensions exceed the preview limit.")
        scale = min(2.0, 1600 / width, 1600 / height)
        size = QSize(max(1, int(width * scale)), max(1, int(height * scale)))
        image = document.render(0, size)
        if image.isNull():
            return replace(result, text="PDF preview unavailable.\n\n" + result.text)
        return replace(
            result,
            image=image,
            image_original_size=(size.width(), size.height()),
            text="PDF · first page only",
        )
    finally:
        document.close()
        buffer.close()


class FilePreviewWorker(QObject):
    finished = Signal(object)

    def __init__(self, path: Path, **kwargs) -> None:
        super().__init__()
        self.pdf = kwargs.pop("pdf", False)
        self.task = FilePreviewTask(path, **kwargs)

    def cancel(self) -> None:
        self.task.cancel()

    @Slot()
    def run(self) -> None:
        result = self.task.run()
        if not result.cancelled and result.error is None:
            result = decode_preview_image(result, pdf=self.pdf)
        if self.task._cancelled.is_set():
            result = replace(
                result, cancelled=True, image=None, image_bytes=None, text=""
            )
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
