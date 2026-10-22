from __future__ import annotations

import stat
from collections import OrderedDict
from pathlib import Path

from diagnostics import DiagnosticCancelled, FileChangedError, hash_snapshot_file
from fs_ops import file_signature
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
