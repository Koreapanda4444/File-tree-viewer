"""On-demand folder totals from the existing disk analysis worker."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from ui.disk_usage import DiskUsageWorker
from ui.i18n import tr
from ui.tasks import prepare_worker_thread
from ui.widgets import PlainLabel
from virtual.storage import format_size


class FolderSizeDialog(QDialog):
    updated = Signal()

    def __init__(self, root, cache, parent=None, *, summary=False):
        super().__init__(parent)
        self.root, self.cache = root, cache
        self.summary = summary
        self.thread = self.worker = None
        self.result = self.outcome = None
        self.closing = False
        self.cached_result = False
        self.cancel_requested = False
        self.setWindowTitle(
            tr("Subtree Structure Summary") if summary else tr("Folder Size")
        )
        self.resize(740, 680) if summary else self.resize(700, 420)
        layout = QVBoxLayout(self)
        path = PlainLabel(str(root))
        path.setWordWrap(True)
        path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(path)
        self.details = PlainLabel()
        self.details.setWordWrap(True)
        layout.addWidget(self.details, 1)
        self.structure = PlainLabel()
        self.structure.setWordWrap(True)
        self.structure.setVisible(summary)
        layout.addWidget(self.structure)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        layout.addWidget(self.bar)
        self.status = PlainLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        explanation = QLabel(
            tr(
                "Results describe a scan interval, not an atomic snapshot. Cached totals expire after 30 seconds; Recalculate rereads every file. Links, reparse points and app recovery data are excluded."
            )
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        row = QHBoxLayout()
        self.refresh = QPushButton(tr("Recalculate"))
        self.refresh.clicked.connect(self.start)
        self.cancel = QPushButton(tr("Cancel"))
        self.cancel.clicked.connect(self.cancel_task)
        close = QPushButton(tr("Close"))
        close.clicked.connect(self.reject)
        row.addWidget(self.refresh)
        row.addStretch()
        row.addWidget(self.cancel)
        row.addWidget(close)
        layout.addLayout(row)
        cached = cache.get(root)
        if cached is not None:
            self.bar.hide()
            self.cancel.setEnabled(False)
            self.display(cached, cached=True)
        else:
            self.start()

    def start(self):
        if self.thread is not None or self.closing:
            return
        self.cache.invalidate([self.root])
        self.updated.emit()
        self.revision = self.cache.revision
        self.outcome = None
        self.result = None
        self.cancel_requested = False
        self.cached_result = False
        self.details.clear()
        self.structure.clear()
        self.status.setText(tr("Calculating folder size…"))
        self.bar.show()
        self.refresh.setEnabled(False)
        self.cancel.setEnabled(True)
        self.worker = DiskUsageWorker(self.root, folder_only=True)
        self.worker.progress.connect(
            lambda phase, count: self.status.setText(
                tr("{phase}: {count:,} entries", phase=tr(phase), count=count)
            )
        )
        self.worker.finished.connect(self.receive)
        self.thread = prepare_worker_thread(
            self, self.worker, on_finished=self.finished_task
        )
        self.thread.start()

    def task_failed(self, error):
        self.outcome = None, error

    def receive(self, result, error):
        self.outcome = result, error

    def finished_task(self):
        self.thread = self.worker = None
        self.bar.hide()
        self.refresh.setEnabled(True)
        self.cancel.setEnabled(False)
        result, error = self.outcome or (None, tr("Folder inspection failed"))
        if self.cancel_requested:
            result, error = None, tr("Analysis cancelled")
        if result is None:
            self.status.setText(tr(error))
        else:
            if self.revision != self.cache.revision:
                result = replace(result, changed=True)
            self.cached_result = self.cache.put(result, revision=self.revision)
            if result.complete and not self.cached_result:
                result = replace(result, changed=True)
            self.display(result)
            self.updated.emit()
        if self.closing:
            self.done(QDialog.DialogCode.Rejected)

    def display(self, result, *, cached=False):
        self.result = result
        self.cached_result = cached or self.cached_result
        self.details.setText(
            tr(
                "Logical size: {logical}\nUnique size: {unique}\nAllocated size: {allocated}\nFiles: {files:,} · Folders: {folders:,}\nSkipped/errors: {errors:,} · Links/special items skipped: {skipped:,}\nScan interval: {start} — {end}",
                logical=format_size(result.logical),
                unique=format_size(result.unique)
                + (tr(" (estimated)") if result.unique_estimated else ""),
                allocated=format_size(result.allocated)
                if result.allocated is not None
                else tr("Unavailable on this file system"),
                files=result.files,
                folders=result.folders,
                errors=result.errors,
                skipped=result.skipped,
                start=datetime.fromisoformat(result.started)
                .astimezone()
                .strftime("%Y-%m-%d %H:%M:%S"),
                end=datetime.fromisoformat(result.completed)
                .astimezone()
                .strftime("%H:%M:%S"),
            )
        )
        if self.summary:
            groups = result.extensions[:8]
            rows = [
                tr(
                    "{extension}: {files:,} files · {size}",
                    extension=tr(group.extension),
                    files=group.file_count,
                    size=format_size(group.total_size),
                )
                for group in groups
            ]
            self.structure.setText(
                tr(
                    "Maximum folder depth: {folders:,} · Maximum entry depth: {entries:,}\nTop extensions ({shown:,} of {total:,}):",
                    folders=result.maximum_folder_depth,
                    entries=result.maximum_depth,
                    shown=len(groups),
                    total=result.extension_group_count,
                )
                + "\n"
                + "\n".join(rows)
            )
        self.status.setText(
            tr("Cached result — Recalculate for current totals")
            if cached
            else tr("Scan completed")
            if result.complete
            else tr(
                "Partial result: items were skipped, inaccessible or changed during the scan. Recalculate to retry."
            )
        )

    def check_cache(self):
        if (
            self.result is not None
            and self.cached_result
            and self.root not in self.cache.results()
        ):
            self.cached_result = False
            self.status.setText(
                tr(
                    "This result expired or a change was detected. Recalculate to refresh."
                )
            )

    def cancel_task(self):
        if self.worker is not None:
            self.cancel_requested = True
            self.worker.cancel()
            self.cancel.setEnabled(False)

    def done(self, result):
        if self.thread is not None:
            self.closing = True
            self.cancel_task()
            return
        super().done(result)
