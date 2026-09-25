from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any, TextIO

from organization import OrganizationRule
from planning import ConflictPolicy, FilePlan, PlanAction, PlanOperation, normalize_root

PLAN_FORMAT = "file-tree-viewer-plan"
RULE_FORMAT = "file-tree-viewer-organization-rule"
FORMAT_VERSION = 1
PLAN_IO_BATCH_SIZE = 1_024
JSON_READ_CHUNK_SIZE = 64 * 1_024
MAX_SINGLE_JSON_VALUE = 8 * 1_024 * 1_024
PersistenceProgress = Callable[[str, int], None]


class PersistenceCancelled(Exception):
    pass


def save_plan(
    path: Path | str,
    plan: FilePlan,
    *,
    cancelled: threading.Event | None = None,
    progress: PersistenceProgress | None = None,
) -> None:
    operations = plan.operations

    def write(stream: TextIO) -> None:
        stream.write('{"format":')
        write_json_value(stream, PLAN_FORMAT)
        stream.write(',"version":')
        write_json_value(stream, FORMAT_VERSION)
        stream.write(',"source_root":')
        write_json_value(stream, str(plan.root) if plan.root is not None else None)
        stream.write(',"operations":[\n')
        for position, operation in enumerate(operations):
            check_persistence_cancelled(cancelled)
            if position:
                stream.write(",\n")
            write_json_value(stream, operation_to_dict(operation))
            if progress is not None and (position + 1) % PLAN_IO_BATCH_SIZE == 0:
                progress("Saving Plan", position + 1)
        stream.write("\n]}\n")
        if progress is not None:
            progress("Plan saved", len(operations))

    atomic_text_write(path, write)


def load_plan(
    path: Path | str,
    root: Path | str,
    *,
    cancelled: threading.Event | None = None,
    progress: PersistenceProgress | None = None,
) -> tuple[FilePlan, str | None]:
    plan = FilePlan(normalize_root(root))
    metadata: dict[str, Any] = {}
    seen_keys: set[str] = set()
    operation_count = 0
    operations_seen = False
    with Path(path).expanduser().open(encoding="utf-8") as stream:
        reader = StreamingJsonReader(stream)
        reader.expect("{")
        while reader.peek() != "}":
            key = reader.read_value()
            if not isinstance(key, str):
                raise TypeError("Plan object keys must be text")
            if key in seen_keys:
                raise ValueError(f"Duplicate Plan field: {key}")
            seen_keys.add(key)
            reader.expect(":")
            if key == "operations":
                operations_seen = True
                reader.expect("[")
                batch: list[PlanOperation] = []
                while reader.peek() != "]":
                    check_persistence_cancelled(cancelled)
                    raw_operation = reader.read_value()
                    operation_count += 1
                    try:
                        batch.append(operation_from_dict(raw_operation))
                    except (TypeError, ValueError) as error:
                        raise ValueError(
                            f"Invalid operation {operation_count}: {error}"
                        ) from error
                    if len(batch) >= PLAN_IO_BATCH_SIZE:
                        plan.extend(batch)
                        batch.clear()
                        if progress is not None:
                            progress("Loading Plan", operation_count)
                    if reader.peek() == ",":
                        reader.expect(",")
                        if reader.peek() == "]":
                            raise ValueError("Trailing comma in Plan operations")
                    elif reader.peek() != "]":
                        raise ValueError("Expected ',' or ']' in Plan operations")
                reader.expect("]")
                plan.extend(batch)
            else:
                metadata[key] = reader.read_value()
            if reader.peek() == ",":
                reader.expect(",")
                if reader.peek() == "}":
                    raise ValueError("Trailing comma in Plan file")
            elif reader.peek() != "}":
                raise ValueError("Expected ',' or '}' in Plan file")
        reader.expect("}")
        reader.finish()

    if not operations_seen:
        raise ValueError("Plan operations are missing")
    require_format(metadata, PLAN_FORMAT)
    source_root = metadata.get("source_root")
    if source_root is not None and not isinstance(source_root, str):
        raise TypeError("Plan source_root must be text or null")
    if progress is not None:
        progress("Plan loaded", operation_count)
    return plan, source_root


def save_rule(path: Path | str, rule: OrganizationRule) -> None:
    payload = {
        "format": RULE_FORMAT,
        "version": FORMAT_VERSION,
        "rule": rule_to_dict(rule),
    }
    atomic_json_write(path, payload)


def load_rule(path: Path | str) -> OrganizationRule:
    payload = read_json_object(path)
    require_format(payload, RULE_FORMAT)
    raw_rule = payload.get("rule")
    if not isinstance(raw_rule, dict):
        raise TypeError("Organization rule must be an object")
    return rule_from_dict(raw_rule)


def organization_presets() -> dict[str, OrganizationRule]:
    return {
        "Downloads": OrganizationRule(
            destination="Sorted",
            grouping="Extension",
        ),
        "Photos": OrganizationRule(
            destination="Photos",
            extensions=("jpg;jpeg;png;gif;webp;bmp;tif;tiff;heic;heif;raw;dng"),
            grouping="Year / Month",
        ),
        "Development": OrganizationRule(
            destination="Development",
            extensions=(
                "c;h;cpp;hpp;cc;cs;go;java;kt;rs;py;js;jsx;ts;tsx;rb;php;"
                "swift;lua;sh;ps1;sql;html;css;scss;vue;svelte;json;yaml;yml;"
                "toml;xml;md"
            ),
            grouping="Extension",
        ),
    }


def operation_to_dict(operation: PlanOperation) -> dict[str, Any]:
    return {
        "action": operation.action.value,
        "source": operation.source.as_posix() if operation.source is not None else None,
        "target": operation.target.as_posix() if operation.target is not None else None,
        "operation_id": operation.operation_id,
        "conflict_policy": operation.conflict_policy.value,
    }


def operation_from_dict(payload: object) -> PlanOperation:
    if not isinstance(payload, dict):
        raise TypeError("Operation must be an object")
    operation_id = required_text(payload, "operation_id")
    source = optional_text(payload, "source")
    target = optional_text(payload, "target")
    return PlanOperation(
        action=PlanAction(required_text(payload, "action")),
        source=source,
        target=target,
        operation_id=operation_id,
        conflict_policy=ConflictPolicy(
            optional_text(payload, "conflict_policy") or ConflictPolicy.ERROR.value
        ),
    )


def rule_to_dict(rule: OrganizationRule) -> dict[str, Any]:
    return {
        "destination": rule.destination,
        "pattern": rule.pattern,
        "extensions": rule.extensions,
        "minimum": rule.minimum,
        "maximum": rule.maximum,
        "after": rule.after.isoformat() if rule.after is not None else None,
        "before": rule.before.isoformat() if rule.before is not None else None,
        "grouping": rule.grouping,
    }


def rule_from_dict(payload: dict[str, Any]) -> OrganizationRule:
    minimum = payload.get("minimum", 0)
    maximum = payload.get("maximum")
    if isinstance(minimum, bool) or not isinstance(minimum, int):
        raise TypeError("Rule minimum must be an integer")
    if maximum is not None and (
        isinstance(maximum, bool) or not isinstance(maximum, int)
    ):
        raise ValueError("Rule maximum must be an integer or null")
    return OrganizationRule(
        destination=required_text(payload, "destination"),
        pattern=optional_text(payload, "pattern") or "*",
        extensions=optional_text(payload, "extensions") or "",
        minimum=minimum,
        maximum=maximum,
        after=optional_date(payload, "after"),
        before=optional_date(payload, "before"),
        grouping=optional_text(payload, "grouping") or "None",
    )


def atomic_json_write(path: Path | str, payload: object) -> None:
    atomic_text_write(
        path,
        lambda stream: (
            json.dump(payload, stream, ensure_ascii=False, indent=2),
            stream.write("\n"),
        ),
    )


def atomic_text_write(path: Path | str, writer: Callable[[TextIO], object]) -> None:
    destination = Path(path).expanduser().absolute()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            writer(stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def write_json_value(stream: TextIO, value: object) -> None:
    json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))


class StreamingJsonReader:
    def __init__(self, stream: TextIO) -> None:
        self.stream = stream
        self.decoder = json.JSONDecoder()
        self.buffer = ""
        self.position = 0
        self.eof = False

    def peek(self) -> str:
        self._skip_whitespace()
        if self.position >= len(self.buffer):
            raise ValueError("Unexpected end of JSON")
        return self.buffer[self.position]

    def expect(self, token: str) -> None:
        if self.peek() != token:
            raise ValueError(f"Expected {token!r} in JSON")
        self.position += 1
        self._compact()

    def read_value(self) -> object:
        self._skip_whitespace()
        while True:
            try:
                value, end = self.decoder.raw_decode(self.buffer, self.position)
            except json.JSONDecodeError as error:
                if self.eof:
                    raise ValueError(
                        f"Invalid JSON at line {error.lineno}, column {error.colno}"
                    ) from error
                if len(self.buffer) - self.position > MAX_SINGLE_JSON_VALUE:
                    raise ValueError("A JSON value is too large") from error
                self._fill()
                continue
            self.position = end
            self._compact()
            return value

    def finish(self) -> None:
        self._skip_whitespace()
        if self.position < len(self.buffer):
            raise ValueError("Unexpected data after the Plan JSON object")
        if not self.eof:
            self._fill()
            self._skip_whitespace()
            if self.position < len(self.buffer):
                raise ValueError("Unexpected data after the Plan JSON object")

    def _skip_whitespace(self) -> None:
        while True:
            while (
                self.position < len(self.buffer)
                and self.buffer[self.position].isspace()
            ):
                self.position += 1
            if self.position < len(self.buffer) or self.eof:
                self._compact()
                return
            self._fill()

    def _fill(self) -> None:
        chunk = self.stream.read(JSON_READ_CHUNK_SIZE)
        if chunk:
            self.buffer += chunk
        else:
            self.eof = True

    def _compact(self) -> None:
        if self.position < JSON_READ_CHUNK_SIZE:
            return
        self.buffer = self.buffer[self.position :]
        self.position = 0


def check_persistence_cancelled(cancelled: threading.Event | None) -> None:
    if cancelled is not None and cancelled.is_set():
        raise PersistenceCancelled


def read_json_object(path: Path | str) -> dict[str, Any]:
    try:
        with Path(path).expanduser().open(encoding="utf-8") as stream:
            payload = json.load(stream)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Invalid JSON at line {error.lineno}, column {error.colno}"
        ) from error
    if not isinstance(payload, dict):
        raise TypeError("Saved data must be a JSON object")
    return payload


def require_format(payload: dict[str, Any], expected: str) -> None:
    if payload.get("format") != expected:
        raise ValueError("This is not a supported File Tree Viewer file")
    version = payload.get("version")
    if version != FORMAT_VERSION:
        raise ValueError(f"Unsupported file version: {version!r}")


def required_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be non-empty text")
    return value


def optional_text(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{key} must be text or null")
    return value


def optional_date(payload: dict[str, Any], key: str) -> date | None:
    value = optional_text(payload, key)
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{key} must use YYYY-MM-DD") from error
