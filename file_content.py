from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from fs_ops import file_signature, require_unchanged
from fs_paths import checked_boundary, is_link_stat


class ContentCancelled(Exception):
    pass


@dataclass(frozen=True, slots=True)
class FileBytes:
    data: bytes
    details: os.stat_result
    is_link: bool
    truncated: bool
    signature: dict[str, int]


def stat_identity(details):
    return (
        details.st_dev,
        details.st_ino,
        details.st_mode,
        details.st_size,
        details.st_mtime_ns,
        details.st_ctime_ns,
    )


def read_file_bytes(
    path: Path, *, limit: int, cancelled=None, allow_links: bool = False
) -> FileBytes:
    if limit <= 0:
        raise ValueError("The file read limit must be positive")
    checked_boundary(path.parent, path, allow_leaf_link=allow_links)
    signature = file_signature(path)
    linked = is_link_stat(path.lstat())
    expected = path.stat() if linked else path.lstat()
    if not stat.S_ISREG(expected.st_mode):
        raise ValueError("Only regular files can be read")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
    if not allow_links:
        flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or stat_identity(opened) != stat_identity(
            expected
        ):
            raise FileExistsError("The file changed while opening it")
        chunks = []
        remaining = limit + 1
        while remaining:
            if cancelled is not None and cancelled.is_set():
                raise ContentCancelled
            data = stream.read(min(remaining, 262144))
            if not data:
                break
            chunks.append(data)
            remaining -= len(data)
        if stat_identity(os.fstat(stream.fileno())) != stat_identity(opened):
            raise FileExistsError("The file changed while reading it")
    require_unchanged(path, signature)
    data = b"".join(chunks)
    return FileBytes(
        data[:limit],
        opened,
        linked,
        len(data) > limit or opened.st_size > limit,
        signature,
    )
