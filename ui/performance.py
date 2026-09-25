from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from persistence import PersistenceCancelled, load_plan, save_plan
from plan_diff import SimulationCancelled, simulate_plan
from planning import FilePlan
from snapshot import FileSnapshot


class PlanLoadWorker(QObject):
    progress = Signal(str, int)
    finished = Signal(object, bool, str)

    def __init__(self, path: Path, root: Path) -> None:
        super().__init__()
        self.path = path
        self.root = root
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    @Slot()
    def run(self) -> None:
        try:
            result = load_plan(
                self.path,
                self.root,
                cancelled=self._cancelled,
                progress=self.progress.emit,
            )
        except PersistenceCancelled:
            self.finished.emit(None, True, "")
        except (OSError, UnicodeError, TypeError, ValueError) as error:
            self.finished.emit(None, False, str(error))
        else:
            self.finished.emit(result, False, "")


class PlanSaveWorker(QObject):
    progress = Signal(str, int)
    finished = Signal(object, bool, str)

    def __init__(self, path: Path, plan: FilePlan) -> None:
        super().__init__()
        self.path = path
        self.plan = plan
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    @Slot()
    def run(self) -> None:
        try:
            save_plan(
                self.path,
                self.plan,
                cancelled=self._cancelled,
                progress=self.progress.emit,
            )
        except PersistenceCancelled:
            self.finished.emit(None, True, "")
        except (OSError, TypeError, ValueError) as error:
            self.finished.emit(None, False, str(error))
        else:
            self.finished.emit(self.path, False, "")


class PlanSimulationWorker(QObject):
    progress = Signal(str, int, int)
    finished = Signal(object, bool, str)

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
            result = simulate_plan(
                self.snapshot,
                self.plan,
                cancelled=self._cancelled,
                progress=self.progress.emit,
            )
        except SimulationCancelled:
            self.finished.emit(None, True, "")
        except (OSError, RuntimeError, ValueError, sqlite3.Error) as error:
            self.finished.emit(None, False, str(error))
        else:
            self.finished.emit(result, False, "")
