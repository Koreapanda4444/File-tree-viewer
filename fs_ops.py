from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import shutil
import stat
import sys
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from fs_paths import checked_boundary, is_link_stat


def path_exists(path: Path) -> bool:
    return os.path.lexists(path)


def create_exclusive(target: Path, *, directory: bool) -> None:
    checked_boundary(target.parent, target, allow_leaf_link=False)
    if directory:
        target.mkdir()
    else:
        with target.open("xb"):
            pass


def copy_exclusive(source: Path, target: Path) -> None:
    checked_boundary(source.parent, source)
    checked_boundary(target.parent, target, allow_leaf_link=False)
    expected = file_signature(source)
    details = source.lstat()
    if is_link_stat(details) and not stat.S_ISLNK(details.st_mode):
        raise ValueError("This reparse point cannot be copied safely")
    if stat.S_ISLNK(details.st_mode):
        link = os.readlink(source)
        require_unchanged(source, expected)
        os.symlink(link, target, target_is_directory=source.is_dir())
    elif stat.S_ISDIR(details.st_mode):
        target.mkdir()
        try:

            def check_entries(directory, names):
                for name in names:
                    entry = (Path(directory) / name).lstat()
                    if is_link_stat(entry) and not stat.S_ISLNK(entry.st_mode):
                        raise ValueError(
                            f"Cannot copy reparse point: {Path(directory) / name}"
                        )
                return ()

            shutil.copytree(
                source, target, symlinks=True, dirs_exist_ok=True, ignore=check_entries
            )
            require_unchanged(source, expected)
        except (OSError, ValueError, shutil.Error) as error:
            raise OSError(f"Incomplete copy preserved at {target}: {error}") from error
    elif stat.S_ISREG(details.st_mode):
        created = False
        try:
            with source.open("rb") as reader, target.open("xb") as writer:
                created = True
                opened = os.fstat(reader.fileno())
                if (opened.st_dev, opened.st_ino) != (
                    expected["st_dev"],
                    expected["st_ino"],
                ):
                    raise FileExistsError("Source replaced while opening")
                shutil.copyfileobj(reader, writer, length=1_024 * 1_024)
            require_unchanged(source, expected)
            shutil.copystat(source, target, follow_symlinks=False)
        except (OSError, ValueError, shutil.Error) as error:
            if created:
                raise OSError(
                    f"Incomplete copy preserved at {target}: {error}"
                ) from error
            raise
    else:
        raise ValueError("This file type cannot be copied")


def move_no_replace(
    source: Path, target: Path, *, allow_cross_device: bool = True
) -> None:
    checked_boundary(source.parent, source)
    checked_boundary(target.parent, target, allow_leaf_link=False)
    expected = file_signature(source)
    if path_exists(target):
        raise FileExistsError(target)
    require_unchanged(source, expected)
    try:
        if os.name == "nt":
            os.rename(source, target)
        elif sys.platform.startswith("linux"):
            library = ctypes.CDLL(None, use_errno=True)
            rename = getattr(library, "renameat2", None)
            if rename is None:
                raise OSError(errno.ENOTSUP, "Atomic no-overwrite move is unavailable")
            rename.argtypes = (
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            )
            rename.restype = ctypes.c_int
            if rename(-100, os.fsencode(source), -100, os.fsencode(target), 1) != 0:
                code = ctypes.get_errno()
                raise OSError(code, os.strerror(code), str(target))
        elif sys.platform == "darwin":
            library = ctypes.CDLL(None, use_errno=True)
            rename = library.renamex_np
            rename.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
            rename.restype = ctypes.c_int
            if rename(os.fsencode(source), os.fsencode(target), 4) != 0:
                code = ctypes.get_errno()
                raise OSError(code, os.strerror(code), str(target))
        else:
            raise OSError(errno.ENOTSUP, "Atomic no-overwrite move is unavailable")
    except OSError as error:
        if error.errno != errno.EXDEV or not allow_cross_device:
            raise
        copy_exclusive(source, target)
        require_unchanged(source, expected)
        try:
            if source.is_dir() and not source.is_symlink():
                shutil.rmtree(source)
            else:
                source.unlink()
        except OSError as removal_error:
            raise OSError(
                f"Copy preserved at {target}; source removal failed: {removal_error}"
            ) from removal_error


def file_signature(path: Path) -> dict[str, int]:
    details = path.lstat()
    return {
        key: int(getattr(details, key))
        for key in (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
    }


def require_unchanged(path: Path, expected: dict[str, int]) -> None:
    if file_signature(path) != expected:
        raise FileExistsError(
            f"Item changed externally; refresh before retrying: {path}"
        )


def operation_error(error: BaseException) -> str:
    if isinstance(error, PermissionError) or getattr(error, "winerror", None) in {
        5,
        32,
        33,
    }:
        return f"Access denied or file in use. Close applications using it and check permissions. {error}"
    if isinstance(error, FileNotFoundError):
        return f"Item is missing. Refresh the folder before retrying. {error}"
    if isinstance(error, FileExistsError):
        return f"A conflicting or changed item was preserved. Refresh before retrying. {error}"
    return str(error)


@contextmanager
def transaction_lock(root: Path):
    directory = root / ".file-tree-viewer"
    checked_boundary(root, directory, allow_leaf_link=False)
    directory.mkdir(exist_ok=True)
    path = directory / "transaction.lock"
    token = uuid4().hex
    payload = json.dumps({"pid": os.getpid(), "token": token})
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as error:
        raise RuntimeError(
            "Another transaction or interrupted session owns this folder. Check recovery before retrying."
        ) from error
    try:
        yield
    finally:
        try:
            if path.read_text(encoding="utf-8") == payload:
                path.unlink()
        except FileNotFoundError:
            pass


def tree_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    stack = [(path, "")]
    while stack:
        item, relative = stack.pop()
        checked_boundary(path.parent, item)
        details = item.lstat()
        metadata = [relative, details.st_dev, details.st_ino, details.st_mode]
        if not stat.S_ISDIR(details.st_mode):
            metadata.extend((details.st_size, details.st_mtime_ns))
            if relative:
                metadata.append(details.st_ctime_ns)
        digest.update(json.dumps(metadata, ensure_ascii=True).encode("utf-8"))
        if is_link_stat(details):
            digest.update(os.fsencode(os.readlink(item)))
        elif stat.S_ISDIR(details.st_mode):
            with os.scandir(item) as iterator:
                children = sorted(iterator, key=lambda entry: entry.name, reverse=True)
            for child in children:
                stack.append((Path(child.path), f"{relative}/{child.name}"))
        elif not relative and stat.S_ISREG(details.st_mode):
            before = file_signature(item)
            with item.open("rb") as stream:
                opened = os.fstat(stream.fileno())
                if (opened.st_dev, opened.st_ino) != (details.st_dev, details.st_ino):
                    raise FileExistsError(f"Item changed during verification: {item}")
                while block := stream.read(1_048_576):
                    digest.update(block)
            require_unchanged(item, before)
    return digest.hexdigest()
