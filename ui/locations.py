from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import QMenu, QToolButton

from fs_paths import normalized_path
from ui.preferences import application_settings


class LocationStore:
    def __init__(self):
        self.settings = application_settings()
        self.recent = self.load("recent", 20)
        self.favorites = self.load("favorites", 64)

    def load(self, name, limit):
        try:
            values = json.loads(self.settings.value("navigation/" + name, "[]"))
        except (TypeError, ValueError):
            return []
        if not isinstance(values, list):
            return []
        result, seen = [], set()
        for value in values:
            if (
                not isinstance(value, str)
                or not value
                or len(value) > 4096
                or "\x00" in value
                or not Path(value).is_absolute()
            ):
                continue
            key = normalized_path(value)
            if key not in seen:
                result.append(str(Path(value)))
                seen.add(key)
            if len(result) >= limit:
                break
        return result

    def save(self):
        self.settings.setValue(
            "navigation/recent", json.dumps(self.recent, ensure_ascii=False)
        )
        self.settings.setValue(
            "navigation/favorites", json.dumps(self.favorites, ensure_ascii=False)
        )
        self.settings.sync()
        return self.settings.status() == self.settings.Status.NoError

    def visit(self, path):
        text = str(path)
        self.recent = [
            text,
            *(p for p in self.recent if normalized_path(p) != normalized_path(text)),
        ][:20]
        return self.save()

    def toggle_favorite(self, path):
        text = str(path)
        if any(normalized_path(p) == normalized_path(text) for p in self.favorites):
            self.favorites = [
                p for p in self.favorites if normalized_path(p) != normalized_path(text)
            ]
        elif len(self.favorites) >= 64:
            raise ValueError("At most 64 favorite locations can be saved")
        else:
            self.favorites.append(text)
        return self.save()


class LocationsButton(QToolButton):
    def __init__(self, page):
        super().__init__()
        self.page = page
        self.setText("Locations")
        self.location_menu = QMenu(self)
        self.setMenu(self.location_menu)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.location_menu.aboutToShow.connect(self.populate)

    def populate(self):
        self.location_menu.clear()
        root = self.page.model.root_path
        store = self.page.locations
        action = self.location_menu.addAction("Toggle Current Favorite")
        action.setEnabled(root is not None)
        action.triggered.connect(lambda: self.page.toggle_favorite(root))
        for title, values in (
            ("Favorites", store.favorites),
            ("Recent Locations", store.recent),
        ):
            submenu = self.location_menu.addMenu(title)
            submenu.setEnabled(bool(values))
            for value in values:
                action = submenu.addAction(value.replace("&", "&&"))
                action.triggered.connect(
                    lambda checked=False, p=value: self.page.set_root(Path(p))
                )
        remove = self.location_menu.addMenu("Remove Favorite")
        remove.setEnabled(bool(store.favorites))
        for value in store.favorites:
            action = remove.addAction(value.replace("&", "&&"))
            action.triggered.connect(
                lambda checked=False, p=value: self.page.toggle_favorite(Path(p))
            )
