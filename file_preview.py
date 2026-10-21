from __future__ import annotations

import codecs
import locale
import os
import stat
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from file_content import ContentCancelled, read_file_bytes
from fs_ops import file_signature, require_unchanged
from fs_paths import checked_boundary

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
    signature: dict[str, int] | None = None
    image_bytes: bytes | None = None
    image: object | None = None


class FilePreviewTask:
    def __init__(
        self,
        path: Path,
        *,
        max_edit_bytes: int = MAX_EDIT_BYTES,
        preview_bytes: int = LARGE_PREVIEW_BYTES,
    ) -> None:
        self.max_edit_bytes = max(1, max_edit_bytes)
        self.preview_bytes = max(1, preview_bytes)
        self.path = path
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def run(self) -> PreviewResult:
        try:
            initial = self.path.stat()
            limit = (
                self.preview_bytes
                if initial.st_size > self.max_edit_bytes
                else self.max_edit_bytes
            )
            content = read_file_bytes(
                self.path, limit=limit, cancelled=self._cancelled, allow_links=True
            )
            if self._cancelled.is_set():
                return self._cancelled_result(
                    content.details.st_size, content.details.st_mtime_ns
                )
            text, encoding, binary = decode_text(
                content.data, truncated=content.truncated
            )
            image = image_signature(content.data)
            binary = binary or image
            if binary:
                text = hex_preview(content.data[:4096])
                encoding = None
            return PreviewResult(
                path=self.path,
                text=text,
                encoding=encoding,
                newline=detect_newline(text),
                size=content.details.st_size,
                modified_ns=content.details.st_mtime_ns,
                editable=not binary and not content.truncated and not content.is_link,
                truncated=content.truncated,
                binary=binary,
                is_link=content.is_link,
                signature=content.signature,
                image_bytes=content.data if image and not content.truncated else None,
            )
        except ContentCancelled:
            return self._cancelled_result(0, 0)
        except (OSError, RuntimeError, UnicodeError, ValueError) as error:
            return PreviewResult(
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
    expected_signature: dict[str, int] | None = None,
) -> tuple[int, int]:
    checked_boundary(path.parent, path, allow_leaf_link=False)
    expected = expected_signature or file_signature(path)
    require_unchanged(path, expected)
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
        checked_boundary(path.parent, path, allow_leaf_link=False)
        require_unchanged(path, expected)
        os.replace(temporary_path, path)
    except (OSError, UnicodeError):
        temporary_path.unlink(missing_ok=True)
        raise

    updated = path.stat(follow_symlinks=False)
    return updated.st_size, updated.st_mtime_ns


def image_signature(data: bytes) -> bool:
    return data.startswith(
        (
            b"\x89PNG\r\n\x1a\n",
            b"\xff\xd8\xff",
            b"GIF87a",
            b"GIF89a",
            b"BM",
            b"II*\x00",
            b"MM\x00*",
            b"\x00\x00\x01\x00",
        )
    ) or (data.startswith(b"RIFF") and data[8:12] == b"WEBP")


def hex_preview(data: bytes) -> str:
    return "\n".join(
        f"{offset:08x}  {data[offset : offset + 16].hex(' '):47}  "
        + "".join(
            chr(value) if 32 <= value < 127 else "."
            for value in data[offset : offset + 16]
        )
        for offset in range(0, len(data), 16)
    )
