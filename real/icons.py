from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QFileInfo
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QFileIconProvider

MAX_ICON_CACHE = 256


class FileIconCache:
    def __init__(self) -> None:
        self.provider = QFileIconProvider()
        self.provider.setOptions(QFileIconProvider.Option.DontUseCustomDirectoryIcons)
        self._cache: OrderedDict[str, QIcon] = OrderedDict()

    def icon(self, path: Path, *, is_directory: bool, is_link: bool = False) -> QIcon:
        suffix = path.suffix.casefold()
        key = (
            "folder"
            if is_directory
            else "link"
            if is_link
            else str(path)
            if suffix in {".exe", ".ico", ".lnk"}
            else suffix
        )
        icon = self._cache.get(key)
        if icon is not None:
            self._cache.move_to_end(key)
            return icon
        if is_directory or is_link:
            kind = (
                QFileIconProvider.IconType.Folder
                if is_directory
                else QFileIconProvider.IconType.File
            )
            icon = self.provider.icon(kind)
        else:
            icon = self.provider.icon(QFileInfo(str(path)))
        self._cache[key] = icon
        while len(self._cache) > MAX_ICON_CACHE:
            self._cache.popitem(last=False)
        return icon

    def clear(self) -> None:
        self._cache.clear()
