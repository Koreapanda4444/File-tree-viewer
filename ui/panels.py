from __future__ import annotations

from PySide6.QtCore import QSignalBlocker
from PySide6.QtGui import QAction

from ui.commands import identify
from ui.i18n import tr
from ui.preferences import boolean


def panel_splitters(window):
    return (
        (
            "real_content",
            window.real_page.content_splitter,
            ("File browser", "Details and preview"),
        ),
        (
            "real_navigation",
            window.real_page.navigation_splitter,
            ("Folder tree", "Folder listing"),
        ),
        (
            "virtual_content",
            window.virtual_page.content_splitter,
            ("Virtual tree", "Virtual details and editor"),
        ),
        (
            "plan_content",
            window.plan_page.workspace_splitter,
            ("Snapshot tree", "Planned operations"),
        ),
    )


class PanelManager:
    def __init__(self, window, settings):
        self.window, self.settings = window, settings
        self.groups = []
        menu = window.menuBar().addMenu(tr("View"))
        for key, splitter, names in panel_splitters(window):
            splitter.setChildrenCollapsible(True)
            group = []
            submenu = menu.addMenu(tr(key.replace("_", " ").title()))
            for index, name in enumerate(names):
                widget = splitter.widget(index)
                action = QAction(tr(name), window, checkable=True)
                identify(action, "view." + key + "." + str(index))
                shown = boolean(
                    settings.value("panels/" + key + "/" + str(index)), True
                )
                action.setChecked(shown)
                widget.setVisible(shown)
                action.toggled.connect(
                    lambda checked, k=key, s=splitter, i=index, g=group: self.toggle(
                        self.current_splitter(k, s), i, g, checked
                    )
                )
                submenu.addAction(action)
                group.append(action)
            if not any(action.isChecked() for action in group):
                group[0].setChecked(True)
            self.groups.append((key, splitter, group))
        window.real_workspace.active_changed.connect(self.active_changed)
        for page in window.real_workspace.panes:
            page.explorer_layout.changed.connect(
                lambda mode, p=page: (
                    self.active_changed(p) if window.real_page is p else None
                )
            )
        menu.addSeparator()
        identify(
            menu.addAction(tr("Restore All Panels"), self.restore),
            "view.restore_panels",
        )

    def current_splitter(self, key, fallback):
        return {
            "real_content": self.window.real_page.content_splitter,
            "real_navigation": self.window.real_page.navigation_splitter,
        }.get(key, fallback)

    def active_changed(self, page):
        for key, original, actions in self.groups:
            if key.startswith("real_"):
                splitter = self.current_splitter(key, original)
                for i, action in enumerate(actions):
                    with QSignalBlocker(action):
                        action.setChecked(not splitter.widget(i).isHidden())

    def toggle(self, splitter, index, group, checked):
        page = self.window.real_page
        if splitter is page.navigation_splitter:
            page.explorer_layout.set_panel(index, checked)
            self.active_changed(page)
            return
        if splitter is page.content_splitter:
            if index == 1:
                page.explorer_layout.set_panel(2, checked)
            else:
                splitter.widget(0).show()
            self.active_changed(page)
            return
        if not checked and not any(action.isChecked() for action in group):
            group[index].setChecked(True)
            return
        splitter.widget(index).setVisible(checked)
        if checked:
            sizes = splitter.sizes()
            if sizes[index] == 0:
                sizes[index] = max(200, sum(sizes) // max(1, len(sizes)))
                splitter.setSizes(sizes)

    def restore(self):
        for key, splitter, actions in self.groups:
            splitter = self.current_splitter(key, splitter)
            for action in actions:
                action.setChecked(True)
            splitter.setSizes([400] * splitter.count())

    def save(self):
        for key, splitter, actions in self.groups:
            for index, action in enumerate(actions):
                self.settings.setValue(
                    "panels/" + key + "/" + str(index),
                    not splitter.widget(index).isHidden(),
                )
