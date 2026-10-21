from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem

from real.sorting import GROUP_ROLE


class FileGroupDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        if index.data(GROUP_ROLE) is not None:
            size.setHeight(size.height() + 22)
        return size

    def paint(self, painter, option, index):
        group = index.data(GROUP_ROLE)
        if group is None:
            super().paint(painter, option, index)
            return
        heading = option.rect.adjusted(0, 0, 0, 0)
        heading.setHeight(22)
        painter.save()
        painter.fillRect(heading, option.palette.alternateBase())
        if index.column() == 0:
            font = painter.font()
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(option.palette.text().color())
            painter.drawText(
                heading.adjusted(4, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter, group
            )
        painter.restore()
        item = QStyleOptionViewItem(option)
        item.rect.adjust(0, 22, 0, 0)
        super().paint(painter, item, index)
