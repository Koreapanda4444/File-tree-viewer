from __future__ import annotations

import stat
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from diagnostics import DiagnosticCancelled, FileChangedError, hash_snapshot_file
from fs_ops import file_signature, require_unchanged
from fs_paths import checked_boundary
from snapshot import SnapshotCancelled
from snapshot_store import check_cancelled


class FileHashCache:
    def __init__(self, limit=2048):
        self.limit = max(1, min(16384, limit))
        self.values = OrderedDict()

    def digest(self, root, entry, *, cancelled=None):
        check_cancelled(cancelled)
        path = Path(root).joinpath(*entry["path"].split("/"))
        checked_boundary(root, path, allow_leaf_link=False)
        signature = file_signature(path)
        if (
            not stat.S_ISREG(signature["st_mode"])
            or signature["st_size"] != entry["size"]
            or signature["st_mtime_ns"] != entry["modified_ns"]
            or (
                entry["identity"] is not None
                and f"{signature['st_dev']}:{signature['st_ino']}" != entry["identity"]
            )
        ):
            raise FileExistsError(f"File changed after comparison scan: {path}")
        key = (str(path), tuple(signature.items()))
        if key in self.values:
            self.values.move_to_end(key)
            return self.values[key]
        try:
            value = hash_snapshot_file(
                root,
                path,
                expected_size=entry["size"],
                expected_modified_ns=entry["modified_ns"],
                expected_identity=entry["identity"],
                cancelled=cancelled,
            )
        except DiagnosticCancelled as error:
            raise SnapshotCancelled from error
        except FileChangedError as error:
            raise FileExistsError(str(error)) from error
        self.values[key] = value
        while len(self.values) > self.limit:
            self.values.popitem(last=False)
        return value


@dataclass(frozen=True, slots=True)
class FileHashResult:
    path: Path
    digest: str
    size: int
    signature: dict[str, int]

    def verify(self):
        require_unchanged(self.path, self.signature)


def hash_file(path, *, cancelled=None, progress=None):
    path = Path(path).absolute()
    checked_boundary(path.parent, path, allow_leaf_link=False)
    signature = file_signature(path)
    if not stat.S_ISREG(signature["st_mode"]):
        raise ValueError("Choose a regular file")
    try:
        digest = hash_snapshot_file(
            path.parent,
            path,
            expected_size=signature["st_size"],
            expected_modified_ns=signature["st_mtime_ns"],
            expected_identity=f"{signature['st_dev']}:{signature['st_ino']}",
            cancelled=cancelled,
            progress=progress,
        )
    except DiagnosticCancelled as error:
        raise SnapshotCancelled from error
    except FileChangedError as error:
        raise FileExistsError(str(error)) from error
    result = FileHashResult(path, digest, signature["st_size"], signature)
    result.verify()
    return result
