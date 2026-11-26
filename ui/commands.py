"""Command identities and safety are independent of translated display text."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache

from PySide6.QtWidgets import QPushButton

from application_paths import resource_path
from ui.i18n import source_text, tr


@dataclass(frozen=True, slots=True)
class CommandSpec:
    identifier: str
    mutates_files: bool = False
    confirmation: str = "none"  # none, handler, palette


def identify(target, identifier, *, mutates_files=False, confirmation="none"):
    target.command_spec = CommandSpec(identifier, mutates_files, confirmation)
    return target


def menu_command(
    menu, identifier, title, callback, *, mutates_files=False, confirmation="none"
):
    return identify(
        menu.addAction(title, callback),
        identifier,
        mutates_files=mutates_files,
        confirmation=confirmation,
    )


# These are widget attribute names, never translated labels or keyword matches.
REAL_MUTATIONS = frozenset(
    (
        "new_file_button",
        "new_folder_button",
        "rename_button",
        "copy_button",
        "move_button",
        "delete_button",
        "undo_button",
        "save_file_button",
    )
)
PLAN_MUTATIONS = frozenset(("apply_button", "undo_plan_button", "recovery_button"))
HANDLER_CONFIRMATIONS = frozenset(
    (
        "delete_button",
        "apply_button",
        "undo_plan_button",
        "recovery_button",
        "reset_button",
        "export_button",
    )
)


def identify_page(page, scope):
    for name, widget in vars(page).items():
        if not isinstance(widget, QPushButton) or hasattr(widget, "command_spec"):
            continue
        mutation = (
            (scope == "real" and name in REAL_MUTATIONS)
            or (scope == "plan" and name in PLAN_MUTATIONS)
            or (scope == "virtual" and name == "export_button")
        )
        confirmation = (
            "handler"
            if name in HANDLER_CONFIRMATIONS
            else ("palette" if scope == "real" and name == "undo_button" else "none")
        )
        identify(
            widget,
            scope + "." + name.removesuffix("_button"),
            mutates_files=mutation,
            confirmation=confirmation,
        )


@cache
def korean_catalog():
    with resource_path("ui", "translations", "ko.json").open(
        encoding="utf-8"
    ) as stream:
        return json.load(stream)


def legacy_aliases(prefix, title):
    korean = korean_catalog()
    prefixes = {prefix, source_text(prefix), korean.get(source_text(prefix), prefix)}
    titles = {title, source_text(title), korean.get(source_text(title), title)}
    return tuple(
        p.replace("&", "") + " / " + t.replace("&", "")
        for p in prefixes
        for t in titles
    )


def walk_actions(menu):
    for action in menu.actions():
        if action.menu() is not None:
            yield from walk_actions(action.menu())
        elif not action.isSeparator():
            yield action


def migrate_keys(keys, commands):
    aliases = {alias: command.key for command in commands for alias in command.aliases}
    return list(dict.fromkeys(aliases.get(key, key) for key in keys))


def command_available(window, target, spec):
    try:
        if hasattr(target, "command_enabled"):
            refresh = getattr(window, "refresh_explorer_actions", None)
            if refresh is not None:
                refresh()
        if (
            not window.isVisible()
            or not target.isEnabled()
            or not window.isEnabled()
            or (isinstance(target, QPushButton) and not target.isVisibleTo(window))
            or not getattr(target, "command_enabled", lambda: True)()
        ):
            return False
        if (
            spec.identifier.startswith("real.")
            and isinstance(target, QPushButton)
            and not window.real_page.isAncestorOf(target)
        ):
            return False
        guard = getattr(window, "write_policy", None)
        return not (spec.mutates_files and guard is not None and guard.read_only)
    except RuntimeError:
        return False


def organize_explorer_actions(window, workspace_menu):
    """Move existing actions; keep their identity, callbacks and shortcut objects."""
    actions = list(workspace_menu.actions())
    for action in actions:
        workspace_menu.removeAction(action)
    for action in actions:
        spec = getattr(action, "command_spec", None)
        if spec is not None and spec.identifier in {
            "workspace.real",
            "workspace.virtual",
            "workspace.plan",
        }:
            workspace_menu.addAction(action)
    groups = {
        "Explore": {
            "workspace.quick_look",
            "workspace.archive",
            "workspace.structure_summary",
            "workspace.folder_size",
            "workspace.export_tree",
        },
        "Plan and design": {
            "workspace.import_real",
            "workspace.open_snapshot",
            "workspace.stage_virtual",
            *[
                "workspace.stage_" + kind
                for kind in ("copy", "move", "rename", "delete")
            ],
        },
        "Compare and analyze": {
            "workspace.compare_folders",
            "workspace.disk_usage",
            "workspace.file_hash",
        },
        "History and recovery": {"workspace.history", "workspace.recovery_history"},
        "Pane transfers": {
            "workspace.panes." + kind for kind in ("copy", "move", "same", "swap")
        },
    }
    for title, identifiers in groups.items():
        submenu = workspace_menu.addMenu(tr(title))
        for action in actions:
            spec = getattr(action, "command_spec", None)
            if spec is not None and spec.identifier in identifiers:
                submenu.addAction(action)
    tree_menu = workspace_menu.addMenu(tr("Tree navigation"))
    for key, title, callback in (
        ("reveal", "Reveal selection in tree", lambda p: p.reveal_in_tree()),
        ("expand", "Expand selected folder", lambda p: p.tree_navigation.start(1)),
        ("depth", "Expand to depth…", lambda p: p.tree_navigation.choose_depth()),
        (
            "collapse",
            "Collapse selected folder",
            lambda p: p.tree_navigation.start(1, collapse=True),
        ),
        (
            "collapse_subtree",
            "Collapse subtree",
            lambda p: p.tree_navigation.start(8, collapse=True, recursive=True),
        ),
        ("cancel", "Cancel tree task", lambda p: p.tree_navigation.cancel()),
    ):
        action = menu_command(
            tree_menu,
            "workspace.tree_" + key,
            tr(title),
            lambda checked=False, cb=callback: cb(window.real_page),
        )
        action.command_enabled = lambda k=key: (
            window.tabs.currentWidget() is window.real_workspace
            and (
                window.real_page.tree_navigation.running
                if k == "cancel"
                else window.real_page.tree_navigation.available()
            )
        )

    files = window.menuBar().addMenu(tr("Files"))
    entries = []
    for attribute, identifier, title, mutation, confirmation in (
        ("new_file_button", "real.new_file", "New File", True, "none"),
        ("new_folder_button", "real.new_folder", "New Folder", True, "none"),
        ("rename_button", "real.rename", "Rename", True, "none"),
        ("copy_button", "real.copy", "Copy To…", True, "none"),
        ("move_button", "real.move", "Move To…", True, "none"),
        ("delete_button", "real.delete", "Delete", True, "handler"),
        ("undo_button", "real.undo", "Undo", True, "palette"),
        ("compare_files_button", "real.compare_files", "Compare Files", False, "none"),
        ("tree_export_button", "real.tree_export", "Export Tree…", False, "none"),
    ):
        action = menu_command(
            files,
            identifier,
            tr(title),
            lambda checked=False, name=attribute: getattr(
                window.real_page, name
            ).click(),
            mutates_files=mutation,
            confirmation=confirmation,
        )
        action.command_enabled = lambda name=attribute: (
            window.tabs.currentWidget() is window.real_workspace
            and getattr(window.real_page, name).isEnabled()
        )
        action.command_aliases = legacy_aliases(
            "Real File System", getattr(window.real_page, attribute).text()
        )
        entries.append(action)
    files.addSeparator()
    for name in ("Copy", "Cut", "Paste"):
        action = menu_command(
            files,
            "files." + name.lower(),
            tr(name),
            lambda checked=False, n=name: window.real_page.file_commands.actions[
                n
            ].trigger(),
            mutates_files=name != "Copy",
        )
        action.command_enabled = lambda n=name: (
            window.tabs.currentWidget() is window.real_workspace
            and window.real_page.file_commands.actions[n].isEnabled()
        )
        entries.append(action)

    def refresh(*args):
        dynamic = [
            action
            for action in walk_actions(workspace_menu)
            if hasattr(action, "command_enabled")
        ]
        for action in entries + dynamic:
            action.setEnabled(action.command_enabled())

    window.refresh_explorer_actions = refresh
    window.real_workspace.state_timer.timeout.connect(refresh)
    window.tabs.currentChanged.connect(refresh)
    refresh()
