from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel, QSizePolicy


class ImagePreview(QLabel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.source = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setMinimumSize(1, 1)

    def set_image(self, image) -> None:
        self.source = image
        self.refresh_image()

    def clear(self) -> None:
        self.source = None
        super().clear()

    def refresh_image(self) -> None:
        if self.source is not None:
            pixmap = QPixmap.fromImage(self.source)
            self.setPixmap(
                pixmap.scaled(
                    self.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.refresh_image()
