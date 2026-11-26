"""Bounded ZIP indexing. Archive paths never become Real model paths."""

from __future__ import annotations

import hashlib
import stat
import struct
import unicodedata
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from types import MappingProxyType

from file_content import open_readonly_file
from fs_ops import require_unchanged
from fs_paths import INTERNAL_DIRECTORY, validate_windows_name
from snapshot_store import check_cancelled


@dataclass(frozen=True, slots=True)
class ArchiveLimits:
    max_entries: int = 50_000
    max_directory_bytes: int = 16 * 1024 * 1024
    max_depth: int = 128
    max_member_bytes: int = 512 * 1024 * 1024
    max_extract_bytes: int = 4 * 1024**3
    max_ratio: int = 1000
    preview_bytes: int = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ArchiveEntry:
    path: str
    directory: bool
    index: int | None = None
    size: int = 0
    compressed: int = 0
    method: int | None = None
    crc: int | None = None
    blocked: str = ""

    @property
    def name(self):
        return self.path.rsplit("/", 1)[-1]

    @property
    def parent(self):
        return self.path.rpartition("/")[0]


def member_path(raw, limits):
    if (
        not raw
        or len(raw) > 4096
        or "\0" in raw
        or raw.startswith(("/", "\\"))
        or PureWindowsPath(raw).drive
    ):
        raise ValueError("ZIP contains an absolute, empty or invalid path")
    value = unicodedata.normalize("NFC", raw.replace("\\", "/"))
    value = value.removesuffix("/")
    parts = value.split("/")
    if len(parts) > limits.max_depth:
        raise ValueError("ZIP directory depth exceeds the limit")
    for part in parts:
        validate_windows_name(part)
        if len(part.encode("utf-8")) > 255 or part.casefold() == INTERNAL_DIRECTORY:
            raise ValueError("ZIP contains a reserved or oversized path component")
    return "/".join(parts)


def directory_digest(stream, limits, cancelled=None):
    """Validate EOCD/ZIP64 and count central records before ZipFile allocates them."""
    stream.seek(0, 2)
    length = stream.tell()
    if length < 22:
        raise zipfile.BadZipFile("Missing ZIP directory")
    stream.seek(max(0, length - 65557))
    tail = stream.read(65557)
    position = len(tail)
    while True:
        position = tail.rfind(b"PK\x05\x06", 0, position)
        if position < 0:
            raise zipfile.BadZipFile("Missing ZIP directory")
        if len(tail) - position >= 22:
            record = struct.unpack_from("<4s4H2LH", tail, position)
            if position + 22 + record[-1] == len(tail):
                break
    _, disk, directory_disk, disk_count, count, size, offset, _ = record
    boundary = length - len(tail) + position
    if disk or directory_disk or disk_count != count:
        raise ValueError("Multipart ZIP archives are unsupported")
    if count == 0xFFFF or size == 0xFFFFFFFF or offset == 0xFFFFFFFF:
        stream.seek(boundary - 20)
        locator = stream.read(20)
        if len(locator) != 20 or locator[:4] != b"PK\x06\x07":
            raise zipfile.BadZipFile("Invalid ZIP64 locator")
        _, disk, zip64_offset, disks = struct.unpack("<4sLQL", locator)
        if disk or disks != 1:
            raise ValueError("Multipart ZIP64 archives are unsupported")
        stream.seek(zip64_offset)
        data = stream.read(56)
        if len(data) != 56 or data[:4] != b"PK\x06\x06":
            raise zipfile.BadZipFile("Invalid ZIP64 directory")
        _, record_size, _, _, disk, directory_disk, disk_count, count, size, offset = (
            struct.unpack("<4sQHHLLQQQQ", data)
        )
        if (
            disk
            or directory_disk
            or disk_count != count
            or not 44 <= record_size <= 1024**2
            or zip64_offset + 12 + record_size != boundary - 20
        ):
            raise zipfile.BadZipFile("Invalid ZIP64 bounds")
        boundary = zip64_offset
    if count > limits.max_entries or size > limits.max_directory_bytes:
        raise ValueError("ZIP entry count or central directory exceeds the limit")
    if offset + size != boundary:
        raise zipfile.BadZipFile(
            "Unsupported ZIP directory layout (embedded or self-extracting archive)"
        )
    stream.seek(offset)
    central = stream.read(size)
    if len(central) != size:
        raise zipfile.BadZipFile("Truncated ZIP directory")
    cursor = actual = 0
    while cursor < size:
        check_cancelled(cancelled)
        if central[cursor : cursor + 4] != b"PK\x01\x02" or size - cursor < 46:
            raise zipfile.BadZipFile("Invalid ZIP central record")
        name, extra, comment = struct.unpack_from("<HHH", central, cursor + 28)
        cursor += 46 + name + extra + comment
        actual += 1
        if cursor > size or actual > limits.max_entries:
            raise ValueError("ZIP directory limits exceeded")
    if actual != count:
        raise zipfile.BadZipFile("ZIP directory entry count does not match")
    return hashlib.sha256(central).hexdigest()


def blocked_reason(info, limits):
    mode = info.external_attr >> 16
    directory = info.is_dir() or info.orig_filename.endswith("\\")
    if directory and info.file_size:
        return "ZIP directory entries with data are unsupported"
    if stat.S_ISDIR(mode) and not directory:
        return "Inconsistent ZIP member type"
    kind = stat.S_IFMT(mode)
    if kind not in (0, stat.S_IFREG, stat.S_IFDIR) or info.external_attr & 0x400:
        return "Special ZIP entries are unsupported"
    if info.flag_bits & 1:
        return "Encrypted ZIP entries are unsupported"
    if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        return "Unsupported ZIP compression method"
    if info.file_size > limits.max_member_bytes:
        return "ZIP member exceeds the extraction size limit"
    if info.file_size > max(1024**2, info.compress_size * limits.max_ratio):
        return "ZIP compression ratio exceeds the limit"
    return ""


@dataclass(frozen=True, slots=True)
class ArchiveIndex:
    path: Path
    signature: dict
    entries: object
    children: object
    directory_hash: str
    limits: ArchiveLimits
    members: int
    total_size: int
    compressed_size: int

    @contextmanager
    def open(self, cancelled=None):
        require_unchanged(self.path, self.signature)
        with open_readonly_file(self.path) as (stream, _, signature, _):
            if (
                signature != self.signature
                or directory_digest(stream, self.limits, cancelled)
                != self.directory_hash
            ):
                raise FileExistsError("ZIP changed; close and reopen it")
            stream.seek(0)
            with zipfile.ZipFile(stream) as archive:
                yield archive


def index_archive(path, *, limits=None, cancelled=None, progress=None):
    limits = limits or ArchiveLimits()
    path = Path(path).absolute()
    entries = {"": ArchiveEntry("", True)}
    folded, children = {"": ""}, {"": []}
    explicit = set()
    total = 0
    with open_readonly_file(path) as (stream, details, signature, _):
        digest = directory_digest(stream, limits, cancelled)
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            infos = archive.infolist()
            if len(infos) > limits.max_entries:
                raise ValueError("ZIP entry count exceeds the limit")
            for number, info in enumerate(infos):
                check_cancelled(cancelled)
                key = member_path(info.orig_filename, limits)
                directory = info.is_dir() or info.orig_filename.endswith("\\")
                if key in explicit:
                    raise ValueError("Duplicate ZIP path")
                explicit.add(key)
                parts = key.split("/")
                for depth in range(1, len(parts) + 1):
                    part = "/".join(parts[:depth])
                    is_dir = depth < len(parts) or directory
                    collision = folded.get(part.casefold())
                    if collision is not None and collision != part:
                        raise ValueError("ZIP contains case or Unicode path collisions")
                    existing = entries.get(part)
                    if existing is not None and (not existing.directory or not is_dir):
                        raise ValueError("ZIP contains file/directory collisions")
                    if existing is None:
                        if len(entries) > limits.max_entries:
                            raise ValueError("ZIP tree entry count exceeds the limit")
                        folded[part.casefold()] = part
                        entries[part] = ArchiveEntry(part, is_dir)
                        children.setdefault(entries[part].parent, []).append(part)
                        if is_dir:
                            children[part] = []
                    if depth == len(parts):
                        entries[part] = ArchiveEntry(
                            part,
                            directory,
                            number,
                            info.file_size,
                            info.compress_size,
                            info.compress_type,
                            info.CRC,
                            blocked_reason(info, limits),
                        )
                total += info.file_size
                if progress and number % 256 == 0:
                    progress(number + 1, len(infos))
            for values in children.values():
                values.sort(
                    key=lambda key: (
                        not entries[key].directory,
                        entries[key].name.casefold(),
                        entries[key].name,
                    )
                )
    return ArchiveIndex(
        path,
        signature,
        MappingProxyType(entries),
        MappingProxyType({key: tuple(value) for key, value in children.items()}),
        digest,
        limits,
        len(infos),
        total,
        details.st_size,
    )
