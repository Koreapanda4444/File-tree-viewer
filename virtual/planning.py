from __future__ import annotations

import os
import tempfile
from pathlib import Path, PurePosixPath

from fs_paths import normalize_plan_path
from planning import PlanAction, PlanOperation
from virtual.storage import VirtualNode, export_workspace_root


def workspace_plan(
    root: VirtualNode, target: str = "Virtual Workspace"
) -> tuple[PlanOperation, ...]:
    prefix = normalize_plan_path(target)
    operations = [PlanOperation(PlanAction.CREATE_FOLDER, target=prefix)]
    total_bytes = 0
    with tempfile.TemporaryDirectory(
        prefix="file-tree-viewer-virtual-plan-"
    ) as directory:
        exported = export_workspace_root(Path(directory), root)
        for folder, directories, files in os.walk(exported):
            parent = PurePosixPath(Path(folder).relative_to(exported).as_posix())
            for name in sorted(directories):
                operations.append(
                    PlanOperation(
                        PlanAction.CREATE_FOLDER, target=prefix / parent / name
                    )
                )
            for name in sorted(files):
                source = Path(folder) / name
                size = source.stat().st_size
                total_bytes += size
                if size > 1024 * 1024 or total_bytes > 64 * 1024 * 1024:
                    raise ValueError(
                        "Virtual staging supports files up to 1 MiB and a total of 64 MiB; use Export for larger workspaces"
                    )
                with source.open(encoding="utf-8", newline="") as stream:
                    content = stream.read()
                operations.append(
                    PlanOperation(
                        PlanAction.CREATE_FILE,
                        target=prefix / parent / name,
                        content=content,
                    )
                )
    return tuple(operations)
