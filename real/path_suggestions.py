from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path

from fs_paths import is_hidden


@dataclass(frozen=True, slots=True)
class Suggestion:
    path: Path
    is_directory: bool


def suggest_paths(
    text: str,
    base: Path,
    *,
    directories_only: bool = True,
    show_hidden: bool = False,
    cancelled: threading.Event | None = None,
    limit: int = 128,
) -> tuple[tuple[Suggestion, ...], bool]:
    expanded = os.path.expandvars(text.strip().strip('"'))
    path = Path(expanded).expanduser()
    if not path.is_absolute():
        path = base / path
    trailing = expanded.endswith((os.sep, os.altsep or os.sep))
    parent, prefix = (path, "") if trailing else (path.parent, path.name.casefold())
    matches = []
    partial = False
    with os.scandir(parent) as entries:
        for count, entry in enumerate(entries):
            if cancelled is not None and cancelled.is_set():
                return (), True
            if count >= 50000:
                partial = True
                break
            if not entry.name.casefold().startswith(prefix) or (
                not show_hidden and is_hidden(entry)
            ):
                continue
            try:
                directory = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if directories_only and not directory:
                continue
            if len(matches) < limit:
                matches.append(Suggestion(Path(entry.path), directory))
            else:
                partial = True
    matches.sort(key=lambda item: (not item.is_directory, item.path.name.casefold()))
    return tuple(matches), partial
