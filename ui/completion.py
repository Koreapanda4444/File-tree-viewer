from __future__ import annotations

import os
import time
from collections import OrderedDict

from PySide6.QtCore import QEvent, QObject, QStringListModel, Qt, QTimer, Signal
from PySide6.QtWidgets import QCompleter

from real.path_suggestions import suggest_paths
from ui.tasks import CancellableWorker, prepare_worker_thread


class SuggestionWorker(CancellableWorker):
    finished = Signal(object, str)

    def __init__(self, text, base, *, directories_only, show_hidden):
        super().__init__()
        self.text, self.base = text, base
        self.directories_only, self.show_hidden = directories_only, show_hidden

    def run(self):
        try:
            result = suggest_paths(
                self.text,
                self.base,
                directories_only=self.directories_only,
                show_hidden=self.show_hidden,
                cancelled=self._cancelled,
            )
        except (OSError, ValueError) as error:
            self.finished.emit(((), False), str(error))
        else:
            self.finished.emit(result, "")


class PathCompletion(QObject):
    chosen = Signal(str)
    idle = Signal()

    def __init__(self, edit, base, hidden, *, directories_only=True):
        super().__init__(edit)
        self.edit, self.base, self.hidden = edit, base, hidden
        self.directories_only = directories_only
        self.thread = self.worker = None
        self.pending = self.active = self.outcome = None
        self.closed = False
        self.cache = OrderedDict()
        self.generation = 0
        self.suggestions = ()
        self.model = QStringListModel(self)
        self.completer = QCompleter(self.model, self)
        self.completer.setCompletionMode(
            QCompleter.CompletionMode.UnfilteredPopupCompletion
        )
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setMaxVisibleItems(12)
        edit.setCompleter(self.completer)
        self.completer.activated.connect(self.accept)
        edit.installEventFilter(self)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(180)
        self.timer.timeout.connect(self.start)
        edit.textEdited.connect(self.request)

    def request(self, text):
        self.generation += 1
        self.model.setStringList([])
        self.pending = (
            (self.generation, text, self.base(), self.hidden())
            if text and not self.closed
            else None
        )
        if self.worker is not None:
            self.worker.cancel()
        self.timer.start()

    def start(self):
        if self.thread is not None or self.pending is None or self.closed:
            return
        self.active, self.pending = self.pending, None
        generation, text, base, hidden = self.active
        key = (text, str(base), hidden)
        cached = self.cache.get(key)
        if cached and time.monotonic() - cached[0] < 3:
            self.present(cached[1], "", generation)
            return
        self.outcome = None
        worker = SuggestionWorker(
            text, base, directories_only=self.directories_only, show_hidden=hidden
        )
        worker.finished.connect(self.receive)
        self.thread = prepare_worker_thread(self, worker, on_finished=self.finished)
        self.worker = worker
        self.thread.start()

    def receive(self, result, error):
        self.outcome = result, error

    def finished(self):
        self.thread = self.worker = None
        generation, text, base, hidden = self.active
        if (
            self.outcome is not None
            and generation == self.generation
            and not self.closed
        ):
            result, error = self.outcome
            if not error:
                self.cache[(text, str(base), hidden)] = time.monotonic(), result
                while len(self.cache) > 32:
                    self.cache.popitem(last=False)
            self.present(result, error, generation)
        self.start()
        if self.thread is None:
            self.idle.emit()

    def present(self, result, error, generation):
        if generation != self.generation or self.closed:
            return
        self.suggestions, partial = result
        values = [
            str(item.path) + (os.sep if item.is_directory else "")
            for item in self.suggestions
        ]
        self.model.setStringList(values)
        self.edit.setToolTip(
            error or ("Suggestions limited; type more characters" if partial else "")
        )
        if values and self.edit.hasFocus():
            self.completer.setCompletionPrefix("")
            self.completer.complete()

    def accept(self, value):
        self.edit.setText(value)
        self.completer.popup().hide()
        self.chosen.emit(value)

    def eventFilter(self, watched, event):
        if (
            watched is self.edit
            and event.type() == QEvent.Type.KeyPress
            and event.key() == Qt.Key.Key_Tab
            and self.completer.popup().isVisible()
        ):
            index = self.completer.popup().currentIndex()
            value = index.data() if index.isValid() else self.model.index(0, 0).data()
            if value:
                self.edit.setText(value)
                self.completer.popup().hide()
                return True
        return super().eventFilter(watched, event)

    def stop(self):
        self.closed = True
        self.pending = None
        self.generation += 1
        self.timer.stop()
        self.completer.popup().hide()
        if self.worker is not None:
            self.worker.cancel()
        return self.thread is None
