"""Command identities and safety are independent of translated display text."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache

from PySide6.QtWidgets import QPushButton

from application_paths import resource_path
from ui.i18n import source_text


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
    if (
        not target.isEnabled()
        or not window.isEnabled()
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
