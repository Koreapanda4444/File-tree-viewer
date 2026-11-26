from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from file_preview import require_preview_unchanged
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
        decoded = (self.source.width(), self.source.height())
        reduced = (
            decoded[0] * decoded[1] < self.original_size[0] * self.original_size[1]
        )
        self.ratio.setText(
            f"{self.zoom * 100:.0f}%" + (tr(" preview") if reduced else "")
        )
        self.buttons["actual"].setText(tr("100% preview") if reduced else "100%")
        self.ratio.setToolTip(
            tr(
                "Zoom is relative to the decoded preview. Large images may be reduced for memory safety."
            )
            if reduced
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


class ImagePreviewDialog(QDialog):
    """A single shared decoded frame; never rereads or edits the source file."""

    def __init__(self, result, parent=None):
        super().__init__(parent)
        self.result = result
        self.stale = False
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(tr("Image Preview") + " — " + result.path.name)
        self.resize(1000, 760)
        layout = QVBoxLayout(self)
        self.status = QLabel(str(result.path))
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.preview = ImagePreview()
        self.preview.buttons["open"].hide()
        self.preview.set_image(result.image, result.image_original_size)
        layout.addWidget(self.preview, 1)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.check_source)
        self.timer.start()
        self.check_source()

    def check_source(self):
        try:
            if self.result.signature is None:
                raise OSError("Missing source signature")
            require_preview_unchanged(self.result)
        except OSError:
            self.stale = True
            self.status.setText(
                tr(
                    "The source changed or is unavailable. This window shows the previously decoded image; reopen it to refresh."
                )
            )
            self.timer.stop()

    def closeEvent(self, event):
        self.timer.stop()
        self.preview.clear()
        self.result = None
        super().closeEvent(event)
