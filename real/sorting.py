from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QSortFilterProxyModel, Qt
from PySide6.QtGui import QColor

from fs_paths import normalized_path

GROUP_ROLE = int(Qt.ItemDataRole.UserRole) + 8


class FileSortModel(QSortFilterProxyModel):
    def __init__(self, source, parent=None):
        super().__init__(parent)
        self.setSourceModel(source)
        self.cut_paths = set()
        self.grouping = "None"
        self.folders_first = True
        self.setDynamicSortFilter(True)
        self.sort(0)

    def path_from_index(self, index):
        return self.sourceModel().path_from_index(self.mapToSource(index))

    def node(self, index):
        return self.sourceModel().node_from_index(index)

    def group(self, node):
        if node is None or node.parent is None or self.grouping == "None":
            return None
        if self.grouping == "Type":
            return (
                "Folders"
                if node.is_directory
                else node.path.suffix.casefold() or "No extension"
            )
        node.load_details()
        if self.grouping == "Date":
            try:
                return (
                    datetime.fromtimestamp(node.modified).astimezone().strftime("%Y-%m")
                    if node.modified is not None
                    else "Unknown date"
                )
            except (ValueError, OverflowError, OSError):
                return "Unknown date"
        if node.is_directory:
            return "Folders"
        size = node.size
        return (
            "Unknown size"
            if size is None
            else "Empty"
            if size == 0
            else "Small (<1 MiB)"
            if size < 1024**2
            else "Medium (<100 MiB)"
            if size < 100 * 1024**2
            else "Large (>=100 MiB)"
        )

    def key(self, node, column):
        if column == 1:
            return (
                "" if node.is_directory else node.path.suffix.casefold(),
                node.name.casefold(),
            )
        if column in (2, 3):
            node.load_details()
            value = node.size if column == 2 else node.modified
            return (value if value is not None else -1, node.name.casefold())
        return node.name.casefold()

    def lessThan(self, left, right):
        a, b = self.node(left), self.node(right)
        if a is None or b is None:
            return super().lessThan(left, right)
        if self.folders_first and a.is_directory != b.is_directory:
            return (
                a.is_directory
                if self.sortOrder() == Qt.SortOrder.AscendingOrder
                else b.is_directory
            )
        ga, gb = self.group(a), self.group(b)
        if ga != gb:
            return (ga or "") < (gb or "")
        return self.key(a, left.column()) < self.key(b, right.column())

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if (
            role == Qt.ItemDataRole.ForegroundRole
            and index.isValid()
            and normalized_path(self.path_from_index(index)) in self.cut_paths
        ):
            return QColor("gray")
        if role == GROUP_ROLE and index.isValid():
            group = self.group(self.node(self.mapToSource(index)))
            if group is None:
                return None
            if index.row() == 0:
                return group
            previous = self.index(index.row() - 1, 0, index.parent())
            return (
                group
                if group != self.group(self.node(self.mapToSource(previous)))
                else None
            )
        return super().data(index, role)
