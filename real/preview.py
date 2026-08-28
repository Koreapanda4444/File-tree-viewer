from __future__ import annotations

import codecs
import locale
import os
import stat
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

MAX_EDIT_BYTES = 8 * 1_024 * 1_024
LARGE_PREVIEW_BYTES = 1 * 1_024 * 1_024
READ_CHUNK_SIZE = 256 * 1_024


@dataclass(frozen=True, slots=True)
class PreviewResult:
    path: Path
    text: str
    encoding: str | None
    newline: str
    size: int
    modified_ns: int
    editable: bool
    truncated: bool
    binary: bool
    is_link: bool = False
    cancelled: bool = False
    error: str | None = None


class FilePreviewWorker(QObject):
    finished = Signal(object)

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    @Slot()
    def run(self) -> None:
        try:
            is_link = self.path.is_symlink()
            details = (
                self.path.stat() if is_link else self.path.stat(follow_symlinks=False)
            )
            if not stat.S_ISREG(details.st_mode):
                raise OSError("Preview is unavailable for this file type")
            size = details.st_size
            modified_ns = details.st_mtime_ns
            truncated = size > MAX_EDIT_BYTES
            read_limit = LARGE_PREVIEW_BYTES if truncated else size
            data = self._read_bytes(read_limit)
            if self._cancelled.is_set():
                self.finished.emit(self._cancelled_result(size, modified_ns))
                return

            text, encoding, binary = decode_text(data, truncated=truncated)
            self.finished.emit(
                PreviewResult(
                    path=self.path,
                    text=text,
                    encoding=encoding,
                    newline=detect_newline(text),
                    size=size,
                    modified_ns=modified_ns,
                    editable=not binary and not truncated and not is_link,
                    truncated=truncated,
                    binary=binary,
                    is_link=is_link,
                )
            )
        except (OSError, RuntimeError, UnicodeError, ValueError) as error:
            self.finished.emit(
                PreviewResult(
                    path=self.path,
                    text="",
                    encoding=None,
                    newline="\n",
                    size=0,
                    modified_ns=0,
                    editable=False,
                    truncated=False,
                    binary=False,
                    error=str(error),
                )
            )

    def _read_bytes(self, limit: int) -> bytearray:
        data = bytearray()
        remaining = limit
        with self.path.open("rb") as file:
            while remaining > 0 and not self._cancelled.is_set():
                chunk = file.read(min(READ_CHUNK_SIZE, remaining))
                if not chunk:
                    break
                data.extend(chunk)
                remaining -= len(chunk)
        return data

    def _cancelled_result(self, size: int, modified_ns: int) -> PreviewResult:
        return PreviewResult(
            path=self.path,
            text="",
            encoding=None,
            newline="\n",
            size=size,
            modified_ns=modified_ns,
            editable=False,
            truncated=False,
            binary=False,
            cancelled=True,
        )


def decode_text(
    data: bytes | bytearray,
    *,
    truncated: bool,
) -> tuple[str, str | None, bool]:
    if data.startswith(codecs.BOM_UTF8):
        return (
            decode_with_encoding(data, "utf-8-sig", truncated=truncated),
            "utf-8-sig",
            False,
        )
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return (
            decode_with_encoding(data, "utf-16", truncated=truncated),
            "utf-16",
            False,
        )
    if looks_binary(data):
        return "", None, True

    encodings = ["utf-8", locale.getpreferredencoding(False), "cp949"]
    tried = set()
    for encoding in encodings:
        normalized = encoding.casefold()
        if normalized in tried:
            continue
        tried.add(normalized)
        try:
            return (
                decode_with_encoding(data, encoding, truncated=truncated),
                encoding,
                False,
            )
        except UnicodeDecodeError:
            continue
    return "", None, True


def looks_binary(data: bytes | bytearray) -> bool:
    sample = data[:8_192]
    if not sample:
        return False
    if b"\0" in sample:
        return True
    allowed_controls = {8, 9, 10, 12, 13}
    controls = sum(byte < 32 and byte not in allowed_controls for byte in sample)
    return controls / len(sample) > 0.1


def decode_with_encoding(
    data: bytes | bytearray,
    encoding: str,
    *,
    truncated: bool,
) -> str:
    decoder = codecs.getincrementaldecoder(encoding)(errors="strict")
    return decoder.decode(data, final=not truncated)


def detect_newline(text: str) -> str:
    crlf = text.count("\r\n")
    remaining = text.replace("\r\n", "")
    lf = remaining.count("\n")
    cr = remaining.count("\r")
    if crlf >= lf and crlf >= cr and crlf:
        return "\r\n"
    if cr > lf:
        return "\r"
    return "\n"


def save_text_atomic(
    path: Path,
    text: str,
    *,
    encoding: str,
    newline: str,
) -> tuple[int, int]:
    if path.is_symlink():
        raise OSError("Symbolic links are read-only in the built-in editor")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if newline != "\n":
        normalized = normalized.replace("\n", newline)
    data = normalized.encode(encoding)

    original = path.stat(follow_symlinks=False)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temporary_path, stat.S_IMODE(original.st_mode))
        os.replace(temporary_path, path)
    except (OSError, UnicodeError):
        temporary_path.unlink(missing_ok=True)
        raise

    updated = path.stat(follow_symlinks=False)
    return updated.st_size, updated.st_mtime_ns
