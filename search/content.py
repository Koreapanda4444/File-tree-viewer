from __future__ import annotations

import codecs
from dataclasses import dataclass

from file_content import ContentCancelled, open_readonly_file

MAX_CONTENT_BYTES = 64 * 1024 * 1024
MAX_LINE_CHARACTERS = 128 * 1024
MAX_FILE_MATCHES = 20


class ContentSearchSkipped(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ContentMatch:
    line: int
    column: int
    snippet: str


def find_file_content(path, matcher, *, encoding="auto", cancelled=None):
    matches = []
    with open_readonly_file(path) as (stream, details, _signature, _linked):
        if details.st_size > MAX_CONTENT_BYTES:
            raise ContentSearchSkipped("Content search skips files larger than 64 MiB")
        prefix = stream.read(4096)
        if encoding == "auto":
            encoding = next(
                (
                    name
                    for bom, name in (
                        (codecs.BOM_UTF32_LE, "utf-32"),
                        (codecs.BOM_UTF32_BE, "utf-32"),
                        (codecs.BOM_UTF16_LE, "utf-16"),
                        (codecs.BOM_UTF16_BE, "utf-16"),
                        (codecs.BOM_UTF8, "utf-8-sig"),
                    )
                    if prefix.startswith(bom)
                ),
                "utf-8",
            )
        if not encoding.startswith(("utf-16", "utf-32")) and (
            b"\0" in prefix
            or sum(value < 32 and value not in (9, 10, 13, 12) for value in prefix)
            > max(4, len(prefix) // 20)
        ):
            raise ContentSearchSkipped("Binary file")
        decoder = codecs.getincrementaldecoder(encoding)(errors="strict")
        stream.seek(0)
        pending = ""
        line_number = 0
        consumed = 0
        while len(matches) < MAX_FILE_MATCHES:
            if cancelled is not None and cancelled.is_set():
                raise ContentCancelled
            data = stream.read(64 * 1024)
            consumed += len(data)
            if consumed > MAX_CONTENT_BYTES:
                raise ContentSearchSkipped("File grew beyond the content search limit")
            pending += decoder.decode(data, final=not data)
            lines = pending.split("\n")
            pending = lines.pop() if data else ""
            for line in lines:
                if cancelled is not None and cancelled.is_set():
                    raise ContentCancelled
                line_number += 1
                if len(line) > MAX_LINE_CHARACTERS:
                    raise ContentSearchSkipped(
                        "Content search skips files containing lines longer than 128 KiB"
                    )
                match = matcher.find(line.rstrip("\r"))
                if match is not None:
                    start = max(0, match[0] - 80)
                    matches.append(
                        ContentMatch(
                            line_number,
                            match[0] + 1,
                            line[start : start + 400].rstrip("\r"),
                        )
                    )
                    if len(matches) >= MAX_FILE_MATCHES:
                        break
            if len(pending) > MAX_LINE_CHARACTERS:
                raise ContentSearchSkipped(
                    "Content search skips files containing lines longer than 128 KiB"
                )
            if not data:
                break
    return tuple(matches)
