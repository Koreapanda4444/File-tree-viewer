from __future__ import annotations

import os
import stat
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from fs_paths import is_link_stat


@dataclass(frozen=True, slots=True)
class FileMetadata:
    path: str
    name: str
    kind: str
    size: int
    allocated_bytes: int | None
    permissions: str
    mode: int
    modified_ns: int
    accessed_ns: int
    changed_ns: int
    created_ns: int | None
    device: int
    inode: int
    hard_links: int
    uid: int | None
    gid: int | None
    attributes: tuple[str, ...]
    is_link: bool
    link_target: str | None
    link_exists: bool | None

    def as_dict(self) -> dict:
        return asdict(self)

    def display_values(self) -> dict[str, str]:
        return {
            "Path": self.path,
            "Name": self.name,
            "Type": self.kind,
            "Size (bytes)": str(self.size),
            "Allocated bytes": str(self.allocated_bytes)
            if self.allocated_bytes is not None
            else "Unavailable",
            "Permissions (mode bits)": f"{self.permissions} ({self.mode:o}); ACLs are not evaluated",
            "Modified": format_timestamp(self.modified_ns),
            "Accessed": format_timestamp(self.accessed_ns),
            "Platform ctime": format_timestamp(self.changed_ns),
            "Created": format_timestamp(self.created_ns),
            "Device / inode": f"{self.device} / {self.inode}",
            "Hard links": str(self.hard_links),
            "UID / GID": f"{self.uid} / {self.gid}",
            "Windows attributes": ", ".join(self.attributes) or "Unavailable",
            "Link / reparse point": str(self.is_link),
            "Link target": self.link_target or "—",
            "Link target exists": str(self.link_exists)
            if self.link_exists is not None
            else "—",
        }


def format_timestamp(nanoseconds: int | None) -> str:
    if nanoseconds is None:
        return "Unavailable"
    try:
        seconds, fraction = divmod(nanoseconds, 1000000000)
        value = datetime.fromtimestamp(seconds).astimezone()
        return (
            value.strftime("%Y-%m-%d %H:%M:%S")
            + f".{fraction:09d} "
            + value.strftime("%z")
        )
    except (ValueError, OverflowError, OSError):
        return str(nanoseconds)


def inspect_file_metadata(path: Path | str) -> FileMetadata:
    path = Path(path).expanduser().absolute()
    details = path.lstat()
    kind = (
        "Symbolic link"
        if stat.S_ISLNK(details.st_mode)
        else "Folder"
        if stat.S_ISDIR(details.st_mode)
        else "File"
        if stat.S_ISREG(details.st_mode)
        else "FIFO"
        if stat.S_ISFIFO(details.st_mode)
        else "Socket"
        if stat.S_ISSOCK(details.st_mode)
        else "Device or special file"
    )
    linked = is_link_stat(details)
    target = None
    target_exists = None
    if linked:
        try:
            target = os.readlink(path)
            target_exists = path.exists()
        except OSError as error:
            target = str(error)
    attributes = getattr(details, "st_file_attributes", 0)
    names = tuple(
        name.replace("FILE_ATTRIBUTE_", "").lower()
        for name in (
            "FILE_ATTRIBUTE_READONLY",
            "FILE_ATTRIBUTE_HIDDEN",
            "FILE_ATTRIBUTE_SYSTEM",
            "FILE_ATTRIBUTE_ARCHIVE",
            "FILE_ATTRIBUTE_COMPRESSED",
            "FILE_ATTRIBUTE_ENCRYPTED",
            "FILE_ATTRIBUTE_REPARSE_POINT",
        )
        if attributes & getattr(stat, name, 0)
    )
    birth = getattr(details, "st_birthtime_ns", None)
    if birth is None and hasattr(details, "st_birthtime"):
        birth = int(details.st_birthtime * 1000000000)
    return FileMetadata(
        str(path),
        path.name or str(path),
        kind,
        details.st_size,
        details.st_blocks * 512 if hasattr(details, "st_blocks") else None,
        stat.filemode(details.st_mode),
        stat.S_IMODE(details.st_mode),
        details.st_mtime_ns,
        details.st_atime_ns,
        details.st_ctime_ns,
        birth,
        details.st_dev,
        details.st_ino,
        details.st_nlink,
        getattr(details, "st_uid", None),
        getattr(details, "st_gid", None),
        names,
        linked,
        target,
        target_exists,
    )
