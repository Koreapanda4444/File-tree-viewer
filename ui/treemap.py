from __future__ import annotations

import hashlib
from pathlib import PurePosixPath

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QToolTip, QWidget

from ui.analysis import format_size


def partition_tiles(items, rectangle):
    if not items:
        return []
    if len(items) == 1:
        return [(rectangle, items[0])]
    total = sum(size for _, size in items)
    subtotal = 0
    split = 1
    for index, (_, size) in enumerate(items[:-1], 1):
        subtotal += size
        split = index
        if subtotal >= total / 2:
            break
    fraction = sum(size for _, size in items[:split]) / total
    first, second = QRectF(rectangle), QRectF(rectangle)
    if rectangle.width() >= rectangle.height():
        width = rectangle.width() * fraction
        first.setWidth(width)
        second.setLeft(rectangle.left() + width)
    else:
        height = rectangle.height() * fraction
        first.setHeight(height)
        second.setTop(rectangle.top() + height)
    return partition_tiles(items[:split], first) + partition_tiles(
        items[split:], second
    )


class UsageTreemap(QWidget):
    activated = Signal(object)
    selected = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items = []
        self.tiles = []
        self.chosen = None
        self.setMinimumHeight(180)
        self.setMouseTracking(True)
        self.setAccessibleName("Disk usage treemap")

    def set_entries(self, entries, total):
        self.chosen = None
        self.items = [(entry, entry.logical) for entry in entries if entry.logical > 0]
        remainder = total - sum(size for _, size in self.items)
        if remainder > 0:
            self.items.append((None, remainder))
        self.rebuild()

    def rebuild(self):
        self.tiles = partition_tiles(
            self.items, QRectF(self.rect()).adjusted(2, 2, -2, -2)
        )
        self.update()

    def resizeEvent(self, event):
        self.rebuild()
        super().resizeEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().base())
        if not self.tiles:
            painter.setPen(self.palette().text().color())
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "No non-empty files"
            )
        for rectangle, (entry, size) in self.tiles:
            kind = (
                "Other"
                if entry is None
                else "Folders"
                if entry.directory
                else PurePosixPath(entry.path).suffix.casefold()
            )
            hue = int.from_bytes(hashlib.sha256(kind.encode()).digest()[:2]) % 360
            painter.fillRect(
                rectangle.adjusted(1, 1, -1, -1), QColor.fromHsv(hue, 125, 175)
            )
            painter.setPen(QPen(QColor("#ffffff"), 3 if entry is self.chosen else 1))
            painter.drawRect(rectangle.adjusted(1, 1, -1, -1))
            if rectangle.width() > 65 and rectangle.height() > 34:
                label = (
                    "Other items" if entry is None else PurePosixPath(entry.path).name
                )
                text = painter.fontMetrics().elidedText(
                    label, Qt.TextElideMode.ElideMiddle, int(rectangle.width()) - 12
                )
                painter.drawText(
                    rectangle.adjusted(6, 5, -6, -5),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                    text + "\n" + format_size(size),
                )

    def entry_at(self, point):
        return next(
            (
                entry
                for rectangle, (entry, _) in self.tiles
                if rectangle.contains(point)
            ),
            None,
        )

    def mouseMoveEvent(self, event):
        for rectangle, (entry, size) in self.tiles:
            if rectangle.contains(event.position()):
                label = (
                    "Other items; use the folder table to explore"
                    if entry is None
                    else entry.path
                )
                QToolTip.showText(
                    event.globalPosition().toPoint(),
                    f"{label}\n{format_size(size)}",
                    self,
                )
                return
        QToolTip.hideText()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.chosen = self.entry_at(event.position())
            self.selected.emit(self.chosen)
            self.update()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            entry = self.entry_at(event.position())
            if entry is not None:
                self.activated.emit(entry)
