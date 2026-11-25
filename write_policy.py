"""Application write restrictions; this does not change OS permissions."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from functools import wraps


class ReadOnlyError(PermissionError):
    pass


class WritePolicy:
    def __init__(self):
        self._lock = threading.RLock()
        self.read_only = False
        self.active = 0

    def set_read_only(self, enabled):
        with self._lock:
            if self.active:
                return False
            self.read_only = bool(enabled)
            return True

    @contextmanager
    def mutation(self):
        with self._lock:
            if self.read_only:
                raise ReadOnlyError("Read-only mode blocks changes to actual files")
            self.active += 1
        try:
            yield
        finally:
            with self._lock:
                self.active -= 1


policy = WritePolicy()


def mutating(function):
    @wraps(function)
    def run(*args, **kwargs):
        with policy.mutation():
            return function(*args, **kwargs)

    return run


def writes_allowed(widget):
    if not policy.read_only:
        return True
    from ui.i18n import tr

    message = tr("Read-only mode blocks changes to actual files")
    signal = getattr(widget, "status_changed", None)
    if signal is not None:
        signal.emit(message)
    else:
        window = widget.window()
        if hasattr(window, "statusBar"):
            window.statusBar().showMessage(message)
        elif hasattr(widget, "summary"):
            widget.summary.setText(message)
    return False
