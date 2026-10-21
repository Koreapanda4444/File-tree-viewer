from __future__ import annotations

import sqlite3
from pathlib import Path

from PySide6.QtCore import Signal, Slot

from persistence import PersistenceCancelled, load_plan, save_plan
from plan_diff import SimulationCancelled, simulate_plan
from planning import FilePlan
from snapshot import FileSnapshot
from ui.tasks import CancellableWorker


class PlanLoadWorker(CancellableWorker):
    progress = Signal(str, int)
    finished = Signal(object, bool, str)

    def __init__(self, path: Path, root: Path) -> None:
        super().__init__()
        self.path = path
        self.root = root

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


class PlanSaveWorker(CancellableWorker):
    progress = Signal(str, int)
    finished = Signal(object, bool, str)

    def __init__(self, path: Path, plan: FilePlan) -> None:
        super().__init__()
        self.path = path
        self.plan = plan

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


class PlanSimulationWorker(CancellableWorker):
    progress = Signal(str, int, int)
    finished = Signal(object, bool, str)

    def __init__(self, snapshot: FileSnapshot, plan: FilePlan) -> None:
        super().__init__()
        self.snapshot = snapshot
        self.plan = plan

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


class SnapshotFileWorker(CancellableWorker):
    progress = Signal(str, int)
    finished = Signal(object, bool, str)

    def __init__(
        self, path: Path, *, snapshot: FileSnapshot | None = None, name: str = ""
    ) -> None:
        super().__init__()
        self.path = path
        self.snapshot = snapshot
        self.name = name

    @Slot()
    def run(self) -> None:
        from snapshot import SnapshotCancelled
        from snapshot_store import load_snapshot, save_snapshot

        try:
            if self.snapshot is None:
                result = load_snapshot(
                    self.path, cancelled=self._cancelled, progress=self.progress.emit
                )
            else:
                result = save_snapshot(
                    self.snapshot,
                    self.path,
                    name=self.name,
                    cancelled=self._cancelled,
                    progress=self.progress.emit,
                )
        except SnapshotCancelled:
            self.finished.emit(None, True, "")
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            RuntimeError,
            sqlite3.Error,
        ) as error:
            self.finished.emit(None, False, str(error))
        else:
            self.finished.emit(result, False, "")


class SnapshotCompareWorker(CancellableWorker):
    progress = Signal(str, int)
    finished = Signal(object, bool, str)

    def __init__(self, path: Path, snapshot: FileSnapshot) -> None:
        super().__init__()
        self.path = path
        self.snapshot = snapshot

    @Slot()
    def run(self) -> None:
        from snapshot import SnapshotCancelled
        from snapshot_compare import compare_snapshots
        from snapshot_store import load_snapshot

        before = None
        try:
            before = load_snapshot(
                self.path, cancelled=self._cancelled, progress=self.progress.emit
            )
            result = compare_snapshots(
                before,
                self.snapshot,
                cancelled=self._cancelled,
                progress=self.progress.emit,
            )
        except SnapshotCancelled:
            self.finished.emit(None, True, "")
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            RuntimeError,
            sqlite3.Error,
        ) as error:
            self.finished.emit(None, False, str(error))
        else:
            self.finished.emit(result, False, "")
        finally:
            if before is not None:
                before.close()


class PlanPreviewWorker(CancellableWorker):
    progress = Signal(str, int)
    finished = Signal(object, bool, str)

    def __init__(self, snapshot: FileSnapshot, plan: FilePlan) -> None:
        super().__init__()
        self.snapshot = snapshot
        self.plan = plan

    @Slot()
    def run(self) -> None:
        from plan_preview import build_plan_preview
        from snapshot import SnapshotCancelled

        try:
            result = build_plan_preview(
                self.snapshot,
                self.plan,
                cancelled=self._cancelled,
                progress=self.progress.emit,
            )
        except (SnapshotCancelled, SimulationCancelled):
            self.finished.emit(None, True, "")
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
            self.finished.emit(None, False, str(error))
        else:
            self.finished.emit(result, False, "")
