from __future__ import annotations

import os
from collections import defaultdict
from collections.abc import Mapping
from enum import Enum
from pathlib import PurePosixPath

from plan_diff import (
    IssueCode,
    PlanSimulation,
    SimulationIssue,
    path_collision_key,
)
from planning import ConflictPolicy, FilePlan, PlanAction, PlanOperation
from snapshot import FileSnapshot


class ResolutionChoice(str, Enum):
    OVERWRITE = "overwrite"
    AUTO_RENAME = "auto_rename"
    SKIP = "skip"


def resolution_options(
    issues: tuple[SimulationIssue, ...] | list[SimulationIssue],
) -> tuple[ResolutionChoice, ...]:
    codes = {issue.code for issue in issues}
    target_codes = {IssueCode.TARGET_EXISTS, IssueCode.TARGET_DUPLICATE}
    if codes and codes <= {IssueCode.TARGET_EXISTS}:
        return (
            ResolutionChoice.OVERWRITE,
            ResolutionChoice.AUTO_RENAME,
            ResolutionChoice.SKIP,
        )
    if codes and codes <= target_codes:
        return (ResolutionChoice.AUTO_RENAME, ResolutionChoice.SKIP)
    return (ResolutionChoice.SKIP,)


def resolve_plan_conflicts(
    snapshot: FileSnapshot,
    plan: FilePlan,
    simulation: PlanSimulation,
    choices: Mapping[str, ResolutionChoice | str],
) -> FilePlan:
    if plan.root is None or plan.root != snapshot.root:
        raise ValueError("The Plan and Snapshot roots do not match")

    grouped: dict[str, list[SimulationIssue]] = defaultdict(list)
    for issue in simulation.issues:
        grouped[issue.operation_id].append(issue)
    required = set(grouped)
    if set(choices) != required:
        raise ValueError("Every conflicting operation needs one resolution")

    normalized_choices = {
        operation_id: ResolutionChoice(choice)
        for operation_id, choice in choices.items()
    }
    reserved = {
        path_collision_key(operation.target)
        for operation in plan.operations
        if operation.target is not None
        and operation.conflict_policy is not ConflictPolicy.SKIP
    }
    next_numbers: dict[PurePosixPath, int] = defaultdict(lambda: 1)
    resolved = FilePlan(plan.root)

    for operation in plan.operations:
        operation_issues = grouped.get(operation.operation_id)
        if not operation_issues:
            resolved.append(operation)
            continue
        choice = normalized_choices[operation.operation_id]
        if choice not in resolution_options(operation_issues):
            raise ValueError(f"Resolution is not valid for {operation.operation_id}")

        if choice is ResolutionChoice.SKIP:
            resolved.append(with_resolution(operation, ConflictPolicy.SKIP))
        elif choice is ResolutionChoice.OVERWRITE:
            resolved.append(with_resolution(operation, ConflictPolicy.OVERWRITE))
        else:
            original_target = operation.target
            if original_target is None:
                raise ValueError("Auto Rename requires a target path")
            target, next_number = unique_target(
                snapshot,
                plan,
                operation,
                reserved,
                start=next_numbers[original_target],
            )
            next_numbers[original_target] = next_number
            reserved.add(path_collision_key(target))
            resolved.append(
                with_resolution(
                    operation,
                    ConflictPolicy.AUTO_RENAME,
                    target=target,
                )
            )
    return resolved


def with_resolution(
    operation: PlanOperation,
    policy: ConflictPolicy,
    *,
    target: PurePosixPath | None = None,
) -> PlanOperation:
    return PlanOperation(
        action=operation.action,
        source=operation.source,
        target=target if target is not None else operation.target,
        operation_id=operation.operation_id,
        conflict_policy=policy,
    )


def unique_target(
    snapshot: FileSnapshot,
    plan: FilePlan,
    operation: PlanOperation,
    reserved: set[str],
    *,
    start: int = 1,
) -> tuple[PurePosixPath, int]:
    target = operation.target
    if target is None:
        raise ValueError("Auto Rename requires a target path")
    is_directory = operation.action is PlanAction.CREATE_FOLDER
    if operation.source is not None:
        source_entry = snapshot.entry(operation.source)
        is_directory = bool(source_entry and source_entry.is_directory)

    suffix = "" if is_directory else target.suffix
    stem = target.name[: -len(suffix)] if suffix else target.name
    for number in range(start, 1_000_001):
        candidate = target.with_name(f"{stem} ({number}){suffix}")
        if (
            path_collision_key(candidate) in reserved
            or snapshot.entry(candidate) is not None
        ):
            continue
        try:
            exists = os.path.lexists(plan.absolute_path(candidate))
        except OSError:
            exists = True
        if not exists:
            return candidate, number + 1
    raise RuntimeError(f"Cannot find an available name for {target}")
