from __future__ import annotations

from diagnostics import DiagnosticReport, FileChangedError, hash_snapshot_file
from plan_diff import source_live_problem
from planning import ConflictPolicy, FilePlan, PlanAction, PlanOperation
from snapshot import FileSnapshot
from snapshot_store import check_cancelled


def sorted_duplicate_groups(report: DiagnosticReport, order: str = "savings"):
    keys = {
        "savings": lambda group: (-group.reclaimable_size, group.digest),
        "size": lambda group: (-group.size, group.digest),
        "copies": lambda group: (-len(group.paths), group.digest),
        "path": lambda group: min(str(path) for path in group.paths),
    }
    if order not in keys:
        raise ValueError("Unknown duplicate ordering")
    return tuple(sorted(report.duplicate_groups, key=keys[order]))


def duplicate_selection_groups(
    report: DiagnosticReport, selected, *, already_deleted=()
):
    selected = set(selected)
    known = {path for group in report.duplicate_groups for path in group.paths}
    if not selected <= known:
        raise ValueError("Selected paths are not confirmed duplicate candidates")
    deleted = set(already_deleted)
    affected = []
    for group in report.duplicate_groups:
        if not selected.intersection(group.paths):
            continue
        keepers = tuple(
            path
            for path in group.paths
            if path not in selected
            and not any(
                path == removed or removed in path.parents for removed in deleted
            )
        )
        if not keepers:
            raise ValueError("Keep at least one copy in every duplicate group")
        affected.append((group, keepers))
    return tuple(affected)


def prepare_duplicate_deletions(
    snapshot: FileSnapshot,
    plan: FilePlan,
    report: DiagnosticReport,
    selected,
    *,
    cancelled=None,
    progress=None,
) -> tuple[PlanOperation, ...]:
    selected = set(selected)
    active = [
        op for op in plan.operations if op.conflict_policy is not ConflictPolicy.SKIP
    ]
    deletes = {op.source for op in active if op.action is PlanAction.DELETE}
    affected = duplicate_selection_groups(report, selected, already_deleted=deletes)
    root = snapshot.root.resolve(strict=True)
    verified = {}

    def digest(path):
        check_cancelled(cancelled)
        if path not in verified:
            entry = snapshot.entry(path)
            if entry is None or entry.is_directory or entry.is_symlink:
                raise FileChangedError(
                    f"Duplicate candidate is no longer usable: {path}"
                )
            problem = source_live_problem(snapshot, entry)
            if problem:
                raise FileChangedError(f"{path}: {problem}")
            verified[path] = hash_snapshot_file(
                root,
                snapshot.root.joinpath(*path.parts),
                expected_size=entry.size,
                expected_modified_ns=entry.modified_ns,
                expected_identity=entry.identity,
                cancelled=cancelled,
            )
            if progress:
                progress("Rechecking duplicate content", len(verified))
        return verified[path]

    for group, keepers in affected:
        valid_keeper = False
        for path in keepers:
            if any(
                (
                    op.source is not None
                    and (op.source == path or op.source in path.parents)
                )
                or (
                    op.conflict_policy is ConflictPolicy.OVERWRITE
                    and op.target is not None
                    and (op.target == path or op.target in path.parents)
                )
                for op in active
            ):
                continue
            try:
                if digest(path) == group.digest:
                    valid_keeper = True
                    break
            except (OSError, FileChangedError):
                continue
        if not valid_keeper:
            raise FileChangedError(
                "No unchanged retained copy remains in a selected duplicate group"
            )
        for path in selected.intersection(group.paths):
            if digest(path) != group.digest:
                raise FileChangedError(f"Duplicate content changed: {path}")
    additions = []
    for path in sorted(selected):
        if path in deletes:
            continue
        for op in active:
            if op.source is not None and (
                op.source == path
                or op.source in path.parents
                or path in op.source.parents
            ):
                raise ValueError(
                    f"Duplicate selection conflicts with an existing Plan source: {path}"
                )
        additions.append(PlanOperation(PlanAction.DELETE, source=path))
    return tuple(additions)
