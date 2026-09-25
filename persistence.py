from __future__ import annotations

import json
import os
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from organization import OrganizationRule
from planning import ConflictPolicy, FilePlan, PlanAction, PlanOperation, normalize_root

PLAN_FORMAT = "file-tree-viewer-plan"
RULE_FORMAT = "file-tree-viewer-organization-rule"
FORMAT_VERSION = 1


def save_plan(path: Path | str, plan: FilePlan) -> None:
    payload = {
        "format": PLAN_FORMAT,
        "version": FORMAT_VERSION,
        "source_root": str(plan.root) if plan.root is not None else None,
        "operations": [operation_to_dict(operation) for operation in plan.operations],
    }
    atomic_json_write(path, payload)


def load_plan(path: Path | str, root: Path | str) -> tuple[FilePlan, str | None]:
    payload = read_json_object(path)
    require_format(payload, PLAN_FORMAT)
    operations = payload.get("operations")
    if not isinstance(operations, list):
        raise TypeError("Plan operations must be a list")
    source_root = payload.get("source_root")
    if source_root is not None and not isinstance(source_root, str):
        raise ValueError("Plan source_root must be text or null")

    plan = FilePlan(normalize_root(root))
    for position, raw_operation in enumerate(operations, start=1):
        try:
            plan.append(operation_from_dict(raw_operation))
        except (TypeError, ValueError) as error:
            raise ValueError(f"Invalid operation {position}: {error}") from error
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
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


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
