from __future__ import annotations

from PySide6.QtGui import QAction

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
        menu = window.menuBar().addMenu("View")
        for key, splitter, names in panel_splitters(window):
            splitter.setChildrenCollapsible(True)
            group = []
            submenu = menu.addMenu(key.replace("_", " ").title())
            for index, name in enumerate(names):
                widget = splitter.widget(index)
                action = QAction(name, window, checkable=True)
                shown = boolean(
                    settings.value("panels/" + key + "/" + str(index)), True
                )
                action.setChecked(shown)
                widget.setVisible(shown)
                action.toggled.connect(
                    lambda checked, s=splitter, i=index, g=group: self.toggle(
                        s, i, g, checked
                    )
                )
                submenu.addAction(action)
                group.append(action)
            if not any(action.isChecked() for action in group):
                group[0].setChecked(True)
            self.groups.append((key, splitter, group))
        menu.addSeparator()
        menu.addAction("Restore All Panels", self.restore)

    def toggle(self, splitter, index, group, checked):
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
            for action in actions:
                action.setChecked(True)
            splitter.setSizes([400] * splitter.count())

    def save(self):
        for key, splitter, actions in self.groups:
            for index, action in enumerate(actions):
                self.settings.setValue(
                    "panels/" + key + "/" + str(index), action.isChecked()
                )
