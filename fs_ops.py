from __future__ import annotations

import ctypes
import errno
import os
import shutil
import stat
import sys
from pathlib import Path

from fs_paths import checked_boundary


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
    details = source.lstat()
    if stat.S_ISLNK(details.st_mode):
        os.symlink(os.readlink(source), target, target_is_directory=source.is_dir())
    elif stat.S_ISDIR(details.st_mode):
        target.mkdir()
        try:
            shutil.copytree(source, target, symlinks=True, dirs_exist_ok=True)
        except BaseException:
            shutil.rmtree(target, ignore_errors=True)
            raise
    elif stat.S_ISREG(details.st_mode):
        created = False
        try:
            with source.open("rb") as reader, target.open("xb") as writer:
                created = True
                shutil.copyfileobj(reader, writer, length=1_024 * 1_024)
            shutil.copystat(source, target, follow_symlinks=False)
        except BaseException:
            if created:
                target.unlink(missing_ok=True)
            raise
    else:
        raise ValueError("This file type cannot be copied")


def move_no_replace(
    source: Path, target: Path, *, allow_cross_device: bool = True
) -> None:
    checked_boundary(source.parent, source)
    checked_boundary(target.parent, target, allow_leaf_link=False)
    if path_exists(target):
        raise FileExistsError(target)
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
        try:
            if source.is_dir() and not source.is_symlink():
                shutil.rmtree(source)
            else:
                source.unlink()
        except OSError as removal_error:
            raise OSError(
                f"Copy preserved at {target}; source removal failed: {removal_error}"
            ) from removal_error
