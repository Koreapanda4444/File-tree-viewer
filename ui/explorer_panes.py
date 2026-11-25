"""Independent explorer state and the Real workspace's optional second pane."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QByteArray, QEvent, QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ui.i18n import choice_text, set_choice_text, tr
from ui.real_explorer import RealExplorerPage


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


class RealWorkspace(QWidget):
    active_changed = Signal(object)

    def __init__(self):
        super().__init__()
        self.left = RealExplorerPage("left")
        self.right = RealExplorerPage("right")
        self.panes = (self.left, self.right)
        self.active = self.left
        self.dual = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.toggle = QToolButton()
        self.toggle.setText(tr("Dual pane"))
        self.toggle.setCheckable(True)
        self.toggle.toggled.connect(self.set_dual)
        row.addWidget(self.toggle)
        self.active_label = QLabel()
        row.addWidget(self.active_label)
        row.addStretch()
        layout.addLayout(row)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        for page in self.panes:
            self.splitter.addWidget(page)
        self.splitter.setSizes([700, 700])
        self.right.hide()
        layout.addWidget(self.splitter, 1)
        self.compact_saved = None
        QApplication.instance().installEventFilter(self)
        self.activate(self.left)

    @property
    def busy(self):
        return any(
            page.operation_is_running
            or page.search_is_running
            or page.preview_is_running
            or page.search_export_thread is not None
            for page in self.panes
        )

    def activate(self, page):
        if page not in self.panes or (page is self.right and not self.dual):
            return
        self.active = page
        self.active_label.setText(
            tr("Active pane: {side}", side=tr("Left" if page is self.left else "Right"))
        )
        for pane in self.panes:
            pane.setProperty("activePane", pane is page)
            pane.setStyleSheet(
                "RealExplorerPage { border-top: 2px solid #3b7957; }"
                if pane is page and self.dual
                else ""
            )
        self.active_changed.emit(page)

    def eventFilter(self, watched, event):
        if event.type() in (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.FocusIn,
        ) and isinstance(watched, QWidget):
            for page in self.panes:
                if watched is page or page.isAncestorOf(watched):
                    if page is not self.active:
                        self.activate(page)
                    break
        return False

    def set_dual(self, enabled):
        enabled = bool(enabled)
        if enabled == self.dual:
            return True
        if self.busy or any(
            not page._confirm_editor_transition() for page in self.panes
        ):
            with QSignalBlocker(self.toggle):
                self.toggle.setChecked(self.dual)
            return False
        if enabled:
            self.compact_saved = []
            for page in self.panes:
                widgets = (
                    page.navigation_splitter.widget(0),
                    page.content_splitter.widget(1),
                )
                self.compact_saved.append(
                    tuple(not widget.isHidden() for widget in widgets)
                )
                for widget in widgets:
                    widget.hide()
                page.name_input.setMaximumWidth(150)
                page.name_input.setMinimumWidth(80)
                page.name_input.setSizePolicy(
                    QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
                )
            if (
                self.right.model.root_path is None
                and self.left.model.root_path is not None
            ):
                self.right.set_root(self.left.model.root_path)
        elif self.compact_saved:
            for page, visible in zip(self.panes, self.compact_saved, strict=True):
                for widget, shown in zip(
                    (
                        page.navigation_splitter.widget(0),
                        page.content_splitter.widget(1),
                    ),
                    visible,
                    strict=True,
                ):
                    widget.setVisible(shown)
        self.dual = enabled
        self.right.setVisible(enabled)
        with QSignalBlocker(self.toggle):
            self.toggle.setChecked(enabled)
        self.activate(self.active if enabled else self.left)
        return True

    def focus_other(self):
        if self.dual:
            self.activate(self.right if self.active is self.left else self.left)
            self.active.folder_panel.active_view.setFocus()

    def save(self, settings):
        for page in self.panes:
            save_pane(page, settings, page.pane_id)
        settings.setValue("panes/dual", self.dual)
        settings.setValue("panes/active", self.active.pane_id)
        settings.setValue("panes/splitter", self.splitter.saveState())

    def restore(self, settings):
        if any(page.close_requested for page in self.panes):
            return
        if not restore_pane(self.left, settings, "left"):
            self.left.explorer_state.load_session(settings)
        restore_pane(self.right, settings, "right")
        enabled = settings.value("panes/dual", False)
        if enabled is True or str(enabled).lower() == "true":
            self.set_dual(True)
        sizes = settings.value("panes/splitter")
        if isinstance(sizes, QByteArray):
            self.splitter.restoreState(sizes)
        if self.dual and settings.value("panes/active") == "right":
            self.activate(self.right)

    def prepare_close(self):
        ready = True
        for page in self.panes:
            if not page.prepare_close():
                if page.close_cancelled:
                    for other in self.panes:
                        other.cancel_close_request()
                    return False
                ready = False
        return ready

    @property
    def close_cancelled(self):
        return any(page.close_cancelled for page in self.panes)

    def cancel_close_request(self):
        for page in self.panes:
            page.cancel_close_request()

    def finalize_close(self):
        QApplication.instance().removeEventFilter(self)
        for page in self.panes:
            page.finalize_close()
