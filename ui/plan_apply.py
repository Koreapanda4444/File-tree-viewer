from __future__ import annotations

import sqlite3

from PySide6.QtCore import QObject, Signal, Slot

from operation_history import record_events, record_result
from plan_apply import PlanApplicationRecord, apply_plan, undo_application
from planning import FilePlan
from snapshot import FileSnapshot
from ui.tasks import CancellableWorker


class PlanApplyWorker(CancellableWorker):
    progress = Signal(str, int, int, str)
    finished = Signal(object, str)

    def __init__(self, snapshot: FileSnapshot, plan: FilePlan) -> None:
        super().__init__()
        self.snapshot = snapshot
        self.plan = plan

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
            record_events(
                [
                    {
                        "category": "plan",
                        "action": type(self).__name__,
                        "status": "failed",
                        "details": {"error": str(error)},
                    }
                ]
            )
            self.finished.emit(None, str(error))
        else:
            record_result(
                "apply" if isinstance(self, PlanApplyWorker) else "undo", record
            )
            self.finished.emit(record, "")


class PlanUndoWorker(QObject):
    progress = Signal(str, int, int, str)
    finished = Signal(object, str)

    def __init__(self, record: PlanApplicationRecord) -> None:
        super().__init__()
        self.record = record

    @Slot()
    def run(self) -> None:
        try:
            marker = self.record.transaction_directory / "sync.json"
            if marker.exists():
                from sync_apply import recover_sync_group

                record = recover_sync_group(
                    marker, undo=True, progress=self.progress.emit
                )
            else:
                record = undo_application(self.record, progress=self.progress.emit)
        except (OSError, RuntimeError, ValueError) as error:
            record_events(
                [
                    {
                        "category": "plan",
                        "action": type(self).__name__,
                        "status": "failed",
                        "details": {"error": str(error)},
                    }
                ]
            )
            self.finished.emit(None, str(error))
        else:
            record_result(
                "apply" if isinstance(self, PlanApplyWorker) else "undo", record
            )
            self.finished.emit(record, "")


class PlanRecoveryWorker(QObject):
    progress = Signal(str, int, int, str)
    finished = Signal(object, str)

    def __init__(self, root) -> None:
        super().__init__()
        self.root = root

    @Slot()
    def run(self) -> None:
        from recovery import recover_root

        try:
            result = recover_root(self.root, progress=self.progress.emit)
        except (OSError, RuntimeError, ValueError, TypeError, KeyError) as error:
            record_events(
                [
                    {
                        "category": "plan",
                        "action": type(self).__name__,
                        "status": "failed",
                        "details": {"error": str(error)},
                    }
                ]
            )
            self.finished.emit(None, str(error))
        else:
            record_result("recover", result)
            self.finished.emit(result, "")
