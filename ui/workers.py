from __future__ import annotations

import sqlite3
from pathlib import Path

from PySide6.QtCore import Signal, Slot

from snapshot import SnapshotCancelled, build_snapshot
from ui.tasks import CancellableWorker


class SnapshotWorker(CancellableWorker):
    progress = Signal(int, int)
    finished = Signal(object, bool, str)

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = root

    @Slot()
    def run(self) -> None:
        try:
            snapshot = build_snapshot(
                self.root,
                cancelled=self._cancelled,
                progress=self.progress.emit,
            )
        except SnapshotCancelled:
            self.finished.emit(None, True, "")
        except (
            OSError,
            RuntimeError,
            UnicodeError,
            ValueError,
            sqlite3.Error,
        ) as error:
            self.finished.emit(None, False, str(error))
        else:
            self.finished.emit(snapshot, False, "")
