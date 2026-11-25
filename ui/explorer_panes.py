"""Independent explorer state and the Real workspace's optional second pane."""

from __future__ import annotations

import json
from pathlib import Path

from ui.i18n import choice_text, set_choice_text


def save_pane(page, settings, key):
    document = {
        "root": str(page.model.root_path) if page.model.root_path else None,
        "history": [str(path) for path in page.history.paths],
        "position": page.history.position,
        "view": choice_text(page.folder_panel.mode),
        "sort": page.sort_field.currentIndex(),
        "order": page.sort_order.currentIndex(),
        "group": choice_text(page.group_field),
        "folders_first": page.folders_first.isChecked(),
        "hidden": page.show_hidden.isChecked(),
    }
    settings.setValue("panes/" + key, json.dumps(document, ensure_ascii=False))
    settings.beginGroup("pane_layout/" + key)
    try:
        page.explorer_state.save_session(settings)
        settings.setValue("content", page.content_splitter.saveState())
        settings.setValue("navigation", page.navigation_splitter.saveState())
    finally:
        settings.endGroup()


def restore_pane(page, settings, key):
    raw = settings.value("panes/" + key, "")
    if not isinstance(raw, str) or not raw or len(raw) > 256 * 1024:
        return False
    try:
        state = json.loads(raw)
        root = Path(state["root"]) if state["root"] else None
        if root is not None and (not root.is_absolute() or not root.is_dir()):
            return False
        history = state.get("history", [])
        if (
            not isinstance(history, list)
            or len(history) > 128
            or any(
                not isinstance(path, str) or not Path(path).is_absolute()
                for path in history
            )
        ):
            return False
        position = state.get("position", -1)
        if type(position) is not int or not -1 <= position < max(1, len(history)):
            return False
        for name, maximum in (("sort", 3), ("order", 1)):
            value = state.get(name, 0)
            if type(value) is not int or not 0 <= value <= maximum:
                return False
        page.sort_field.setCurrentIndex(state.get("sort", 0))
        page.sort_order.setCurrentIndex(state.get("order", 0))
        set_choice_text(page.group_field, state.get("group", "None"))
        set_choice_text(page.folder_panel.mode, state.get("view", "Details"))
        page.folders_first.setChecked(state.get("folders_first") is not False)
        page.show_hidden.setChecked(state.get("hidden") is True)
        if not page.set_root(root):
            return False
        page.history.paths = list(map(Path, history))
        page.history.position = position if history else -1
        page.update_navigation_buttons()
        settings.beginGroup("pane_layout/" + key)
        try:
            page.explorer_state.load_session(settings)
            for name, splitter in (
                ("content", page.content_splitter),
                ("navigation", page.navigation_splitter),
            ):
                value = settings.value(name)
                if value is not None:
                    splitter.restoreState(value)
        finally:
            settings.endGroup()
        return True
    except (ValueError, TypeError, KeyError, OSError):
        return False
