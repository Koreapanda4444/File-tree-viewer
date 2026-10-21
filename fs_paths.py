from __future__ import annotations

import os
import stat
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
    clean = name
    if not clean.strip() or clean in {".", ".."}:
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
        stem = clean.split(".", 1)[0].rstrip(" ").casefold()
        reserved = {"con", "prn", "aux", "nul", "conin$", "conout$"}
        reserved.update(
            f"{prefix}{number}"
            for prefix in ("com", "lpt")
            for number in "123456789¹²³"
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


def is_link_stat(details: os.stat_result) -> bool:
    return stat.S_ISLNK(details.st_mode) or bool(
        getattr(details, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def checked_boundary(root: Path, path: Path, *, allow_leaf_link: bool = True) -> Path:
    root = Path(os.path.abspath(root))
    path = Path(os.path.abspath(path))
    if not path_is_within(path, root):
        raise ValueError(f"Path is outside the operation root: {path}")
    for component in reversed((path, *path.parents)):
        try:
            details = component.lstat()
        except FileNotFoundError:
            continue
        if is_link_stat(details) and (component != path or not allow_leaf_link):
            raise ValueError(
                f"Linked folders cannot be used for file operations: {component}"
            )
    if not root.is_dir():
        raise NotADirectoryError(root)
    return path


def is_hidden(entry: os.DirEntry[str]) -> bool:
    if entry.name.startswith("."):
        return True
    if os.name != "nt":
        return False

    try:
        attributes = entry.stat(follow_symlinks=False).st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_HIDDEN)


def is_reparse_point(entry: os.DirEntry[str]) -> bool:
    try:
        if entry.is_symlink():
            return True
    except OSError:
        return False
    if os.name != "nt":
        return False

    try:
        attributes = entry.stat(follow_symlinks=False).st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
