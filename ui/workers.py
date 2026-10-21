from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from snapshot import SnapshotCancelled, build_snapshot


class SnapshotWorker(QObject):
    progress = Signal(int, int)
    finished = Signal(object, bool, str)

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = root
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

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
