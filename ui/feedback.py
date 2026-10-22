from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QWidget,
)

from ui.widgets import PlainLabel


def show_error(
    parent, title: str, summary: str, details: str, *, recovery_path=None
) -> None:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(title)
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setText(summary)
    if recovery_path is not None:
        box.setInformativeText(
            f"Keep the recovery files and resolve conflicts before applying another Plan.\nRecovery record: {recovery_path}"
        )
    box.setDetailedText(details)
    box.setStandardButtons(QMessageBox.StandardButton.Close)
    box.exec()


class TaskMonitor(QWidget):
    def __init__(self, window) -> None:
        super().__init__(window)
        self.window = window
        self.selected = None
        self.started = time.monotonic()
        self.cancelled = set()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.label = PlainLabel()
        self.label.setMaximumWidth(240)
        self.bar = QProgressBar()
        self.bar.setFixedWidth(150)
        self.show_task = QPushButton("Show Task")
        self.cancel = QPushButton("Cancel Task")
        for widget in (self.label, self.bar, self.show_task, self.cancel):
            layout.addWidget(widget)
        self.show_task.clicked.connect(self.focus_task)
        self.cancel.clicked.connect(self.cancel_task)
        self.timer = QTimer(self)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.hide()

    def active_tasks(self):
        for page, prefixes in (
            (
                self.window.real_page,
                (
                    ("operation", "File operation"),
                    ("search", "Search"),
                    ("search_export", "Search export"),
                    ("preview", "Preview"),
                ),
            ),
            (self.window.virtual_page, (("task", "Virtual task"),)),
            (
                self.window.plan_page,
                (
                    ("scan", "Snapshot scan"),
                    ("apply", "Plan apply"),
                    ("undo", "Undo / recovery"),
                    ("analysis", "Analysis"),
                    ("diagnostic", "Problem scan"),
                    ("plan_io", "Plan / snapshot file"),
                    ("simulation", "Simulation"),
                ),
            ),
        ):
            for prefix, label in prefixes:
                thread = getattr(page, prefix + "_thread", None)
                if thread is not None:
                    yield (
                        page,
                        prefix,
                        label,
                        thread,
                        getattr(page, prefix + "_worker", None),
                    )

    def refresh(self):
        tasks = list(self.active_tasks())
        keys = {id(task[3]) for task in tasks}
        self.cancelled.intersection_update(keys)
        selected = next(
            (task for task in tasks if task[0] is self.window.tabs.currentWidget()),
            tasks[0] if tasks else None,
        )
        if selected is None:
            self.selected = None
            self.hide()
            return
        if self.selected is None or self.selected[3] is not selected[3]:
            self.started = time.monotonic()
            self.bar.setRange(0, 0)
        self.selected = selected
        cancelling = id(selected[3]) in self.cancelled
        self.label.setText(("Cancelling: " if cancelling else "") + selected[2])
        self.label.setToolTip(
            f"{len(tasks)} active task(s); elapsed {time.monotonic() - self.started:.0f}s"
        )
        self.cancel.setEnabled(
            not cancelling and callable(getattr(selected[4], "cancel", None))
        )
        self.cancel.setText("Cancelling…" if cancelling else "Cancel Task")
        self.show()

    def advance(self, page, message: str, completed: int, total: int):
        self.refresh()
        if self.selected is None or page is not self.selected[0]:
            return
        if total > 0:
            self.bar.setRange(0, 1000)
            self.bar.setValue(max(0, min(1000, int(completed / total * 1000))))
            self.bar.setFormat(f"{completed:,}/{total:,}")
        else:
            self.bar.setRange(0, 0)
        self.bar.setToolTip(message)

    def focus_task(self):
        self.refresh()
        if self.selected is not None:
            self.window.tabs.setCurrentWidget(self.selected[0])

    def cancel_task(self):
        self.refresh()
        if self.selected is None:
            return
        _page, prefix, _label, thread, worker = self.selected
        cancel = getattr(worker, "cancel", None)
        if not callable(cancel) or id(thread) in self.cancelled:
            return
        cancel()
        self.cancelled.add(id(thread))
        message = (
            "Cancellation requested; completed Plan changes will be rolled back. Keep the app open."
            if prefix == "apply"
            else "Cancellation requested; waiting for a safe stopping point."
        )
        self.window.statusBar().showMessage(message)
        self.refresh()
