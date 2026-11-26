"""Layouts arrange the existing views without replacing models or selections."""

from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import QObject, QSignalBlocker, QTimer, Signal
from PySide6.QtWidgets import QComboBox, QToolButton

from ui.i18n import add_choices, choice_text, set_choice_text, tr
from ui.widgets import ElidedPathLabel, FlowLayout

MODES = ("Explorer", "Tree Focus", "Compact")
DEFAULTS = {
    "Explorer": {
        "visible": [True, True, True],
        "content": [1000, 320],
        "navigation": [300, 700],
    },
    "Tree Focus": {
        "visible": [True, False, True],
        "content": [1050, 270],
        "navigation": [1000, 0],
    },
    "Compact": {
        "visible": [False, True, False],
        "content": [1000, 0],
        "navigation": [0, 1000],
    },
}


class ExplorerLayout(QObject):
    changed = Signal(str)

    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.mode = "Explorer"
        self.configurations = deepcopy(DEFAULTS)
        self.generation = 0
        self.context = "single"
        self.contexts = {}
        bar = FlowLayout()
        self.choice = QComboBox()
        add_choices(self.choice, MODES)
        self.choice.setToolTip(tr("Explorer layout"))
        self.choice.currentIndexChanged.connect(
            lambda: self.set_mode(choice_text(self.choice))
        )
        bar.addWidget(self.choice)
        self.buttons = {}
        for name, title, column in (
            ("tree", "Folder tree", 0),
            ("details", "Details and preview", 2),
        ):
            button = QToolButton()
            button.setText(tr(title))
            button.setCheckable(True)
            button.toggled.connect(lambda checked, c=column: self.set_panel(c, checked))
            self.buttons[name] = button
            bar.addWidget(button)
        page.layout().insertLayout(2, bar)
        self.bar = bar
        self.path_label = ElidedPathLabel(page)
        page.layout().insertWidget(3, self.path_label)
        page.preview_path_changed.connect(self.update_path)
        page.location_changed.connect(
            lambda path: self.update_path(page.current_preview_path or path)
        )
        self.update_path(None)
        self.apply()

    def update_path(self, path):
        self.path_label.set_path(
            tr("Selected path: {path}", path=path)
            if path
            else tr("Select a file or folder in the tree")
        )

    @property
    def widgets(self):
        page = self.page
        return (
            page.navigation_splitter.widget(0),
            page.folder_panel,
            page.content_splitter.widget(1),
        )

    def capture(self):
        if self.page.search_view_active:
            return
        state = self.configurations[self.mode]
        state["visible"] = [not widget.isHidden() for widget in self.widgets]
        for name, splitter in (
            ("content", self.page.content_splitter),
            ("navigation", self.page.navigation_splitter),
        ):
            sizes = splitter.sizes()
            if sum(sizes):
                state[name] = sizes

    def set_mode(self, mode):
        if mode not in MODES or self.page.close_requested:
            with QSignalBlocker(self.choice):
                set_choice_text(self.choice, self.mode)
            return False
        if mode == self.mode:
            return True
        self.capture()
        scroll = [
            (view.verticalScrollBar().value(), view.horizontalScrollBar().value())
            for view in self.views()
        ]
        self.mode = mode
        self.apply()
        self.generation += 1
        generation = self.generation
        QTimer.singleShot(0, lambda: self.restore_scroll(generation, scroll))
        return True

    def views(self):
        return self.page.tree, self.page.folder_view, self.page.folder_icons

    def restore_scroll(self, generation, values):
        if generation == self.generation and not self.page.close_requested:
            for view, (vertical, horizontal) in zip(self.views(), values, strict=True):
                view.verticalScrollBar().setValue(vertical)
                view.horizontalScrollBar().setValue(horizontal)

    def apply(self):
        state = self.configurations[self.mode]
        visible = (
            state["visible"]
            if not self.page.search_view_active
            else [True, False, state["visible"][2]]
        )
        for widget, shown in zip(self.widgets, visible, strict=True):
            widget.setVisible(shown)
        self.page.quick_access.setVisible(self.mode == "Explorer")
        self.page.navigation_splitter.setSizes(state["navigation"])
        self.page.content_splitter.setSizes(state["content"])
        with QSignalBlocker(self.choice):
            set_choice_text(self.choice, self.mode)
        for key, column in (("tree", 0), ("details", 2)):
            with QSignalBlocker(self.buttons[key]):
                self.buttons[key].setChecked(visible[column])
        self.changed.emit(self.mode)

    def set_panel(self, column, shown):
        if self.page.close_requested or (
            self.page.search_view_active and column in (0, 1)
        ):
            self.apply()
            return False
        self.capture()
        state = self.configurations[self.mode]
        if not shown and column in (0, 1) and not state["visible"][1 - column]:
            self.apply()
            return False
        state["visible"][column] = bool(shown)
        for key, index in (
            ("content", 0 if column != 2 else 1),
            ("navigation", column),
        ):
            if key == "navigation" and column == 2:
                continue
            if shown and state[key][index] == 0:
                state[key][index] = 400
        self.apply()
        return True

    def switch_context(self, dual):
        context = "dual" if dual else "single"
        if context == self.context:
            return
        self.capture()
        current = {"mode": self.mode, "configurations": deepcopy(self.configurations)}
        self.contexts[self.context] = current
        target = self.contexts.get(context, deepcopy(current))
        self.context = context
        self.mode = target["mode"]
        self.configurations = deepcopy(target["configurations"])
        self.generation += 1
        self.apply()

    def export_state(self):
        self.capture()
        contexts = deepcopy(self.contexts)
        contexts[self.context] = {
            "mode": self.mode,
            "configurations": deepcopy(self.configurations),
        }
        return {
            "version": 1,
            "contexts": contexts,
            "columns": [self.page.tree.columnWidth(i) for i in range(4)],
        }

    def load_state(self, document):
        try:
            if not isinstance(document, dict) or document.get("version") != 1:
                return False
            contexts = document["contexts"]
            if (
                not isinstance(contexts, dict)
                or not contexts
                or set(contexts) - {"single", "dual"}
            ):
                return False
            for context in contexts.values():
                if context["mode"] not in MODES or set(
                    context["configurations"]
                ) != set(MODES):
                    return False
                for state in context["configurations"].values():
                    visible = state["visible"]
                    if (
                        not isinstance(visible, list)
                        or len(visible) != 3
                        or any(type(v) is not bool for v in visible)
                        or not any(visible[:2])
                    ):
                        return False
                    for field in ("content", "navigation"):
                        sizes = state[field]
                        if (
                            not isinstance(sizes, list)
                            or len(sizes) != 2
                            or any(
                                type(v) is not int or not 0 <= v <= 1_000_000
                                for v in sizes
                            )
                            or not sum(sizes)
                        ):
                            return False
            columns = document.get("columns", [])
            if columns and (
                not isinstance(columns, list)
                or len(columns) != 4
                or any(type(v) is not int or not 24 <= v <= 10000 for v in columns)
            ):
                return False
            self.contexts = deepcopy(contexts)
            current = contexts.get(self.context, next(iter(contexts.values())))
            self.mode = current["mode"]
            self.configurations = deepcopy(current["configurations"])
            self.apply()
            if columns and not self.page.search_view_active:
                for column, width in enumerate(columns):
                    self.page.tree.setColumnWidth(column, width)
            return True
        except (KeyError, TypeError, ValueError):
            return False

    def focus_files(self):
        (
            self.page.tree
            if not self.widgets[0].isHidden() and self.widgets[1].isHidden()
            else self.page.folder_panel.active_view
        ).setFocus()
