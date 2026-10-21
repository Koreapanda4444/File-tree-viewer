from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from fs_paths import checked_boundary

MAX_EVENT_BYTES = 1_048_576


def sync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_json(path: Path, data: dict) -> None:
    checked_boundary(path.parent, path, allow_leaf_link=False)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".journal-",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        sync_directory(path.parent)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def checksum(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
    ).hexdigest()


def append_event(path: Path, sequence: int, event: str, mutation: dict) -> None:
    checked_boundary(path.parent, path, allow_leaf_link=False)
    payload = {"sequence": sequence, "event": event, "mutation": mutation}
    data = (
        json.dumps(
            {**payload, "checksum": checksum(payload)},
            ensure_ascii=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    if len(data) > MAX_EVENT_BYTES:
        raise ValueError("Recovery event is too large")
    with path.open("ab") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def read_events(path: Path) -> tuple[list[dict], int, bool]:
    checked_boundary(path.parent, path, allow_leaf_link=False)
    events = []
    valid_bytes = 0
    with path.open("rb") as stream:
        while line := stream.readline(MAX_EVENT_BYTES + 1):
            if len(line) > MAX_EVENT_BYTES:
                raise ValueError("Recovery event exceeds the size limit")
            if not line.endswith(b"\n"):
                return events, valid_bytes, True
            if not line.strip():
                valid_bytes = stream.tell()
                continue
            try:
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise TypeError("Recovery event must be an object")
                if "event" in payload:
                    digest = payload.pop("checksum", None)
                    if digest != checksum(payload):
                        raise ValueError("Recovery event checksum mismatch")
                    if payload.get("sequence") != len(events) + 1:
                        raise ValueError("Recovery event sequence mismatch")
                elif any("event" in item for item in events):
                    raise ValueError("Mixed recovery log formats")
                events.append(payload)
            except (ValueError, TypeError, UnicodeError) as error:
                raise ValueError(
                    f"Invalid recovery log at byte {valid_bytes}: {error}"
                ) from error
            valid_bytes = stream.tell()
    return events, valid_bytes, False
