from __future__ import annotations

import stat
from pathlib import PurePosixPath

from file_operations import normalize_sources
from fs_paths import checked_boundary, normalize_plan_path, normalized_path
from planning import FilePlan, PlanAction, PlanOperation
from snapshot import FileSnapshot


def stage_real_items(
    snapshot: FileSnapshot, plan: FilePlan, paths, action, target=None
):
    action = PlanAction(action)
    if action not in {PlanAction.MOVE, PlanAction.RENAME, PlanAction.DELETE}:
        raise ValueError("Unsupported staging action")
    if plan.root is None or normalized_path(plan.root) != normalized_path(
        snapshot.root
    ):
        raise ValueError("The plan and snapshot roots differ")
    sources = normalize_sources(list(paths))
    if not sources or (action is PlanAction.RENAME and len(sources) != 1):
        raise ValueError("Select one item to rename, or one or more items to stage")
    target_path = normalize_plan_path(target) if target is not None else None
    if action is PlanAction.MOVE and target_path is not None:
        parent = snapshot.entry(target_path)
        if (
            parent is None
            or not parent.is_directory
            or parent.is_symlink
            or parent.error
        ):
            raise ValueError("Choose an accessible destination folder in the snapshot")
    additions = []
    for path in sources:
        checked_boundary(snapshot.root, path)
        relative = normalize_plan_path(path.relative_to(snapshot.root).as_posix())
        entry = snapshot.entry(relative)
        details = path.lstat()
        identity = f"{details.st_dev}:{details.st_ino}" if details.st_ino else None
        if (
            entry is None
            or entry.error
            or entry.identity != identity
            or entry.is_directory != stat.S_ISDIR(details.st_mode)
            or (
                not entry.is_directory
                and (
                    entry.size != details.st_size
                    or entry.modified_ns != details.st_mtime_ns
                )
            )
        ):
            raise ValueError(
                f"Item differs from the snapshot; refresh it first: {path}"
            )
        destination = None
        if action is PlanAction.MOVE:
            destination = (target_path or PurePosixPath()) / relative.name
        elif action is PlanAction.RENAME:
            if target_path is None:
                raise ValueError("A new relative path is required")
            destination = target_path
        additions.append(PlanOperation(action, relative, destination))
    plan.extend(additions)
    return tuple(additions)
