"""One bounded preview worker and a coalesced, generation-checked request."""

from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal

from fs_ops import require_unchanged
from real.preview import FilePreviewWorker
from ui.i18n import tr
from ui.tasks import prepare_worker_thread


class PreviewSession(QObject):
    ready = Signal(object)
    status_changed = Signal(str)
    invalidated = Signal()
    stopped = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.path = None
        self.result = None
        self.thread = self.worker = None
        self.generation = 0
        self.running_generation = 0
        self.pending = False
        self.closing = False
        self.outcome = None
        self.timer = QTimer(self)
        self.timer.setInterval(750)
        self.timer.timeout.connect(self.check_source)
        self.timer.start()

    def request(self, path):
        if self.closing:
            return
        self.generation += 1
        self.path = path
        self.pending = True
        self.result = None
        self.invalidated.emit()
        self.status_changed.emit(tr("Loading preview…"))
        if self.worker is not None:
            self.worker.cancel()
        else:
            self.start()

    def start(self):
        if not self.pending or self.closing:
            return
        self.pending = False
        self.running_generation = self.generation
        self.outcome = None
        self.worker = FilePreviewWorker(self.path)
        self.worker.finished.connect(self.receive)
        self.thread = prepare_worker_thread(
            self, self.worker, on_finished=self.finished
        )
        self.thread.start()

    def receive(self, result):
        self.outcome = result

    def task_failed(self, error):
        self.status_changed.emit(error)

    def finished(self):
        self.thread = self.worker = None
        result, self.outcome = self.outcome, None
        if self.closing:
            self.stopped.emit()
            return
        if self.pending:
            self.start()
            return
        if (
            result is None
            or self.running_generation != self.generation
            or result.cancelled
        ):
            return
        if result.error:
            self.status_changed.emit(
                tr("Preview unavailable: {error}", error=result.error)
            )
            return
        try:
            if result.signature is None:
                raise OSError(tr("Missing file identity"))
            require_unchanged(result.path, result.signature)
        except OSError as error:
            self.invalidate(str(error))
            return
        self.result = result
        self.ready.emit(result)

    def invalidate(self, error):
        self.result = None
        self.invalidated.emit()
        self.status_changed.emit(
            tr(
                "File changed or is unavailable. Refresh to reread it. {error}",
                error=error,
            )
        )

    def check_source(self):
        if self.result is None:
            return
        try:
            require_unchanged(self.result.path, self.result.signature)
        except OSError as error:
            self.invalidate(str(error))

    def stop(self):
        self.closing = True
        self.timer.stop()
        self.pending = False
        self.generation += 1
        self.result = self.outcome = None
        self.invalidated.emit()
        if self.worker is not None:
            self.worker.cancel()
        else:
            self.stopped.emit()
