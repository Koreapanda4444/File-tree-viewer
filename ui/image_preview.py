from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import (
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ui.i18n import tr


class ImageView(QGraphicsView):
    zoom_requested = Signal(float)
    resized = Signal()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta:
            self.zoom_requested.emit(1.2 if delta > 0 else 1 / 1.2)
            event.accept()
        else:
            super().wheelEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resized.emit()


class ImagePreview(QWidget):
    open_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.source = None
        self.zoom = 1.0
        self.fit_mode = True
        self.original_size = None
        self.setMinimumSize(1, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        controls = QHBoxLayout()
        controls.setSpacing(3)
        self.buttons = {}
        for key, title, callback in (
            ("out", "Zoom out", lambda: self.zoom_by(1 / 1.2)),
            ("in", "Zoom in", lambda: self.zoom_by(1.2)),
            ("actual", "100%", self.actual_size),
            ("fit", "Fit", self.fit_image),
            ("open", "Separate window", self.open_requested.emit),
        ):
            button = QToolButton()
            button.setText({"out": "−", "in": "+"}.get(key, tr(title)))
            button.setToolTip(tr(title))
            button.clicked.connect(callback)
            self.buttons[key] = button
            controls.addWidget(button)
        self.ratio = QLabel()
        controls.addWidget(self.ratio)
        controls.addStretch()
        layout.addLayout(controls)
        self.view = ImageView()
        self.view.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.view.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.view.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.scene = QGraphicsScene(self.view)
        self.view.setScene(self.scene)
        self.item = None
        self.view.zoom_requested.connect(self.zoom_by)
        self.view.resized.connect(self.refresh_image)
        layout.addWidget(self.view, 1)
        self.clear()

    def set_image(self, image, original_size=None) -> None:
        self.clear()
        if image is None or image.isNull():
            return
        self.source = image
        self.original_size = original_size or (image.width(), image.height())
        self.item = self.scene.addPixmap(QPixmap.fromImage(image))
        self.item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.scene.setSceneRect(self.item.boundingRect())
        for button in self.buttons.values():
            button.setEnabled(True)
        self.fit_image()

    def clear(self) -> None:
        self.source = None
        self.original_size = None
        self.item = None
        self.scene.clear()
        self.view.resetTransform()
        self.fit_mode = True
        self.zoom = 1.0
        self.ratio.setText("")
        for button in self.buttons.values():
            button.setEnabled(False)

    def set_zoom(self, zoom, *, fit=False):
        if self.source is None:
            return
        self.fit_mode = fit
        self.zoom = max(0.01, min(16.0, float(zoom)))
        self.view.resetTransform()
        self.view.scale(self.zoom, self.zoom)
        self.ratio.setText(f"{self.zoom * 100:.0f}%")
        decoded = (self.source.width(), self.source.height())
        self.ratio.setToolTip(
            tr(
                "Zoom is relative to the decoded preview. Large images may be reduced for memory safety."
            )
            if decoded != self.original_size
            else tr("Original image pixels")
        )

    def zoom_by(self, factor):
        self.set_zoom(self.zoom * factor)

    def actual_size(self):
        self.set_zoom(1.0)

    def fit_image(self):
        if self.source is None:
            return
        viewport = self.view.viewport().size()
        self.set_zoom(
            min(
                (viewport.width() - 4) / self.source.width(),
                (viewport.height() - 4) / self.source.height(),
            ),
            fit=True,
        )

    def refresh_image(self):
        if self.fit_mode:
            self.fit_image()
