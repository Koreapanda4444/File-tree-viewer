from __future__ import annotations

import sqlite3
import threading

from PySide6.QtCore import QObject, Signal, Slot

from plan_apply import apply_plan
from planning import FilePlan
from snapshot import FileSnapshot


class PlanApplyWorker(QObject):
    progress = Signal(str, int, int, str)
    finished = Signal(object, str)

    def __init__(self, snapshot: FileSnapshot, plan: FilePlan) -> None:
        super().__init__()
        self.snapshot = snapshot
        self.plan = plan
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    @Slot()
    def run(self) -> None:
        try:
            record = apply_plan(
                self.snapshot,
                self.plan,
                cancelled=self._cancelled,
                progress=self.progress.emit,
            )
        except (OSError, RuntimeError, ValueError, sqlite3.Error) as error:
            self.finished.emit(None, str(error))
        else:
            self.finished.emit(record, "")
