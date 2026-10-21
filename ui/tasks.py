from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot


class CancellableWorker(QObject):
    def __init__(self) -> None:
        super().__init__()
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()


class TaskRunner(QObject):
    finished = Signal()
    failed = Signal(str)

    def __init__(self, worker: QObject) -> None:
        super().__init__()
        self.worker = worker

    @Slot()
    def run(self) -> None:
        try:
            self.worker.run()
        except Exception as error:
            logging.getLogger(__name__).exception("Background task failed")
            self.failed.emit(f"{type(error).__name__}: {error}")
        finally:
            snapshot = getattr(self.worker, "snapshot", None)
            release = getattr(snapshot, "release_thread_connection", None)
            try:
                if callable(release):
                    release()
            finally:
                self.finished.emit()


def prepare_worker_thread(owner: QObject, worker: QObject, *, on_finished) -> QThread:
    thread = QThread(owner)
    runner = TaskRunner(worker)
    worker.moveToThread(thread)
    runner.moveToThread(thread)
    thread.started.connect(runner.run)
    runner.finished.connect(worker.deleteLater)
    runner.finished.connect(runner.deleteLater)
    runner.finished.connect(thread.quit, Qt.ConnectionType.DirectConnection)
    if hasattr(owner, "status_changed"):
        runner.failed.connect(owner.status_changed)
    thread.finished.connect(on_finished)
    thread.finished.connect(thread.deleteLater)
    thread.runner = runner
    thread.worker = worker
    return thread
