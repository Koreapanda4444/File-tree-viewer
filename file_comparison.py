from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from file_content import ContentCancelled, read_file_bytes
from file_preview import decode_text

MAX_COMPARE_BYTES = 2 * 1024 * 1024
MAX_COMPARE_LINES = 10000


@dataclass(frozen=True, slots=True)
class ComparedText:
    path: Path
    lines: tuple[str, ...]
    encoding: str
    truncated: bool


@dataclass(frozen=True, slots=True)
class TextComparison:
    left: ComparedText
    right: ComparedText
    opcodes: tuple[tuple[str, int, int, int, int], ...]

    def rows(self):
        for tag, first, last, other_first, other_last in self.opcodes:
            for offset in range(max(last - first, other_last - other_first)):
                left = first + offset if first + offset < last else None
                right = (
                    other_first + offset if other_first + offset < other_last else None
                )
                yield (
                    left + 1 if left is not None else None,
                    self.left.lines[left].rstrip("\r\n") if left is not None else "",
                    right + 1 if right is not None else None,
                    self.right.lines[right].rstrip("\r\n") if right is not None else "",
                    tag,
                )


def compare_text_files(
    left: Path,
    right: Path,
    *,
    cancelled=None,
    max_bytes: int = MAX_COMPARE_BYTES,
    max_lines: int = MAX_COMPARE_LINES,
) -> TextComparison:
    if max_lines <= 0:
        raise ValueError("The comparison line limit must be positive")

    def read(path):
        content = read_file_bytes(path, limit=max_bytes, cancelled=cancelled)
        text, encoding, binary = decode_text(content.data, truncated=content.truncated)
        if binary or encoding is None:
            raise ValueError(f"Text comparison is unavailable for this format: {path}")
        lines = text.splitlines(keepends=True)
        return ComparedText(
            path,
            tuple(lines[:max_lines]),
            encoding,
            content.truncated or len(lines) > max_lines,
        )

    before = read(left)
    after = read(right)
    if cancelled is not None and cancelled.is_set():
        raise ContentCancelled
    opcodes = SequenceMatcher(
        None, before.lines, after.lines, autojunk=True
    ).get_opcodes()
    if cancelled is not None and cancelled.is_set():
        raise ContentCancelled
    return TextComparison(before, after, tuple(opcodes))
