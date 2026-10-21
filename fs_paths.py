from __future__ import annotations

import os
from pathlib import Path, PurePosixPath, PureWindowsPath

INTERNAL_DIRECTORY = ".file-tree-viewer"


def normalize_root(root: Path | str | None) -> Path | None:
    if root is None:
        return None
    if isinstance(root, str) and not root.strip():
        raise ValueError("The root cannot be empty")
    return Path(os.path.abspath(Path(root).expanduser()))


def normalize_plan_path(path: PurePosixPath | str) -> PurePosixPath:
    raw = str(path).replace("\\", "/")
    if not raw or raw.startswith("/") or PureWindowsPath(raw).drive:
        raise ValueError("Plan paths must be relative")
    parts = raw.split("/")
    if any(not part or part in {".", ".."} for part in parts):
        raise ValueError("Plan paths cannot contain empty, current, or parent parts")
    for part in parts:
        validate_name(part)
    if parts[0].casefold() == INTERNAL_DIRECTORY:
        raise ValueError("This path is reserved for File Tree Viewer recovery data")
    return PurePosixPath(*parts)


def validate_name(name: str, *, windows: bool | None = None) -> str:
    clean = name.strip()
    if not clean or clean in {".", ".."}:
        raise ValueError("Enter a valid name")
    if any(ord(character) < 32 for character in clean):
        raise ValueError("A name cannot contain control characters")
    if any(character in clean for character in "/\\"):
        raise ValueError("A name cannot contain path separators")
    if windows if windows is not None else os.name == "nt":
        if any(character in clean for character in '<>:"|?*'):
            raise ValueError("The name contains an invalid Windows character")
        if clean.endswith((".", " ")):
            raise ValueError("A Windows name cannot end with a dot or space")
        stem = clean.split(".", 1)[0].casefold()
        reserved = {"con", "prn", "aux", "nul"}
        reserved.update(
            f"{prefix}{number}" for prefix in ("com", "lpt") for number in range(1, 10)
        )
        if stem in reserved:
            raise ValueError("The name is reserved by Windows")
    return clean


def validate_windows_name(name: str) -> str:
    return validate_name(name, windows=True)


def normalized_path(path: Path | str) -> str:
    return os.path.normcase(os.path.abspath(path))


def path_collision_key(path: PurePosixPath) -> str:
    value = path.as_posix()
    return value.casefold() if os.name == "nt" else value


def path_is_within(path: Path, parent: Path) -> bool:
    try:
        return os.path.commonpath(
            (normalized_path(path), normalized_path(parent))
        ) == normalized_path(parent)
    except ValueError:
        return False
