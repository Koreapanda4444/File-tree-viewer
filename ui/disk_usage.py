from __future__ import annotations

import sqlite3
from pathlib import PurePosixPath

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from disk_usage import scan_disk_usage
from snapshot import SnapshotCancelled
from ui.analysis import format_size
from ui.tasks import CancellableWorker, prepare_worker_thread
from ui.treemap import UsageTreemap
from ui.widgets import PlainLabel


class DiskUsageWorker(CancellableWorker):
    progress = Signal(str, int)
    finished = Signal(object, str)

    def __init__(self, root):
        super().__init__()
        self.root = root

    @Slot()
    def run(self):
        try:
            result = scan_disk_usage(
                self.root, cancelled=self._cancelled, progress=self.progress.emit
            )
        except SnapshotCancelled:
            self.finished.emit(None, "Analysis cancelled")
        except (OSError, ValueError, sqlite3.Error) as error:
            self.finished.emit(None, str(error))
        else:
            self.finished.emit(result, "")


class DiskUsageDialog(QDialog):
    status_changed = Signal(str)
    reveal_requested = Signal(object)
    stage_requested = Signal(str, object, object)

    def __init__(self, root, parent=None):
        super().__init__(parent)
        self.usage = self.outcome = None
        self.close_requested = False
        self.current_path = ""
        self.offset = 0
        self.selected_entry = None
        self.setWindowTitle("Disk Usage Analysis")
        self.resize(1000, 720)
        self.layout = QVBoxLayout(self)
        self.summary = PlainLabel(f"Scanning {root}…")
        self.summary.setWordWrap(True)
        self.summary.setMinimumHeight(58)
        self.layout.addWidget(self.summary)
        navigation = QHBoxLayout()
        self.up_button = QPushButton("Up")
        self.up_button.clicked.connect(self.go_up)
        self.breadcrumb = QComboBox()
        self.breadcrumb.setMinimumContentsLength(20)
        self.breadcrumb.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.breadcrumb.activated.connect(
            lambda index: self.navigate(self.breadcrumb.itemData(index))
        )
        self.previous_button = QPushButton("Previous 400")
        self.next_button = QPushButton("Next 400")
        self.previous_button.clicked.connect(
            lambda: self.navigate(self.current_path, max(0, self.offset - 400))
        )
        self.next_button.clicked.connect(
            lambda: self.navigate(self.current_path, self.offset + 400)
        )
        for control in (
            self.up_button,
            self.breadcrumb,
            self.previous_button,
            self.next_button,
        ):
            control.setEnabled(False)
            navigation.addWidget(control, 1 if control is self.breadcrumb else 0)
        self.layout.addLayout(navigation)
        self.treemap = UsageTreemap()
        self.layout.addWidget(self.treemap, 2)
        self.treemap.activated.connect(self.activate_entry)
        self.treemap.selected.connect(self.select_map_entry)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ("Path", "Logical size", "Unique bytes", "Status")
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setColumnWidth(0, 430)
        self.table.cellDoubleClicked.connect(
            lambda row, column: self.activate_entry(self.entries[row])
        )
        self.table.itemSelectionChanged.connect(self.table_selection)
        self.layout.addWidget(self.table, 1)
        self.details = PlainLabel(
            "Select an item for details. Double-click a folder to explore."
        )
        self.details.setWordWrap(True)
        self.details.setMinimumHeight(45)
        self.layout.addWidget(self.details)
        self.reveal_button = QPushButton("Show Selected in Real")
        self.reveal_button.setEnabled(False)
        self.reveal_button.clicked.connect(self.reveal_selected)
        self.layout.addWidget(self.reveal_button)
        actions = QHBoxLayout()
        self.largest_button = QPushButton("Largest 100 Files")
        self.largest_button.clicked.connect(self.show_largest)
        self.largest_button.setEnabled(False)
        self.stage_button = QPushButton("Add Selected to Plan…")
        self.stage_button.setEnabled(False)
        self.stage_button.clicked.connect(self.stage_selected)
        actions.addWidget(self.largest_button)
        actions.addWidget(self.stage_button)
        self.layout.addLayout(actions)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.buttons.rejected.connect(self.reject)
        self.layout.addWidget(self.buttons)
        self.worker = DiskUsageWorker(root)
        self.worker.progress.connect(self.progress)
        self.worker.finished.connect(self.receive)
        self.status_changed.connect(self.summary.setText)
        self.thread = prepare_worker_thread(
            self, self.worker, on_finished=self.thread_finished
        )
        self.thread.start()

    @Slot(str, int)
    def progress(self, phase, count):
        self.summary.setText(f"{phase}: {count:,} entries")

    def receive(self, result, error):
        self.outcome = result, error

    def thread_finished(self):
        self.thread.wait()
        self.thread = self.worker = None
        self.usage, error = self.outcome or (
            None,
            "Analysis worker stopped unexpectedly",
        )
        self.outcome = None
        if self.close_requested:
            self.done(QDialog.DialogCode.Rejected)
        elif self.usage is None:
            self.summary.setText(error)
        else:
            self.largest_button.setEnabled(True)
            root = self.usage.entry()
            self.summary.setText(
                f"{self.usage.root}\nLogical: {format_size(root.logical)} · Unique: {format_size(root.unique)} · Allocated estimate: {format_size(root.allocated)} · Skipped/errors: {self.usage.error_count:,}"
            )
            self.navigate("")

    def navigate(self, path, offset=0):
        if self.usage is None:
            return
        entry = self.usage.entry(path)
        if entry is None or not entry.directory:
            return
        self.current_path, self.offset = path, offset
        self.select_entry(None)
        self.breadcrumb.clear()
        ancestors = (
            [""]
            if not path
            else [
                "",
                *[
                    str(p)
                    for p in reversed(PurePosixPath(path).parents)
                    if str(p) != "."
                ],
                path,
            ]
        )
        for ancestor in ancestors:
            self.breadcrumb.addItem(str(self.usage.root / ancestor), ancestor)
        self.breadcrumb.setCurrentIndex(len(ancestors) - 1)
        self.breadcrumb.setEnabled(True)
        self.up_button.setEnabled(bool(path))
        self.previous_button.setEnabled(offset > 0)
        self.next_button.setEnabled(
            bool(self.usage.children(path, limit=1, offset=offset + 400))
        )
        self.show_entries(self.usage.children(path, offset=offset))
        self.treemap.set_entries(self.usage.children(path), entry.logical)

    def go_up(self):
        parent = str(PurePosixPath(self.current_path).parent)
        self.navigate("" if parent == "." else parent)

    def activate_entry(self, entry):
        if entry is not None:
            self.navigate(entry.path) if entry.directory else self.select_entry(entry)

    def table_selection(self):
        row = self.table.currentRow()
        self.select_entry(self.entries[row] if 0 <= row < len(self.entries) else None)

    def select_map_entry(self, entry):
        self.table.blockSignals(True)
        self.table.clearSelection()
        self.table.blockSignals(False)
        self.select_entry(entry)

    def select_entry(self, entry):
        self.selected_entry = entry
        self.reveal_button.setEnabled(entry is not None)
        self.stage_button.setEnabled(entry is not None)
        self.details.setText(
            f"{entry.path} · Logical {format_size(entry.logical)} · Unique {format_size(entry.unique)} · {entry.error or 'Scanned metadata; files may have changed'}"
            if entry is not None
            else "Select an item for details. Double-click a folder to explore."
        )

    def reveal_selected(self):
        if self.usage is not None and self.selected_entry is not None:
            path = self.usage.root / self.selected_entry.path
            self.accept()
            self.reveal_requested.emit(path)

    def show_largest(self):
        if self.usage is None:
            return
        self.navigate("")
        self.show_entries(self.usage.largest())
        self.details.setText(
            "Largest 100 files across the scanned root. Select items to prepare a reviewed Plan."
        )

    def stage_selected(self):
        if self.usage is None or self.selected_entry is None:
            return
        entries = [
            self.entries[index.row()]
            for index in self.table.selectionModel().selectedRows()
        ]
        if not entries:
            entries = [self.selected_entry]
        action, accepted = QInputDialog.getItem(
            self,
            "Prepare File Plan",
            "Stage operation (files remain unchanged until Plan Apply):",
            ("Move", "Copy", "Rename", "Delete"),
            0,
            False,
        )
        if accepted:
            root = self.usage.root
            paths = [root / entry.path for entry in entries]
            self.accept()
            self.stage_requested.emit(action.lower(), root, paths)

    def show_entries(self, entries):
        self.table.blockSignals(True)
        self.entries = entries
        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            for column, text in enumerate(
                (
                    entry.path,
                    format_size(entry.logical),
                    format_size(entry.unique),
                    entry.error,
                )
            ):
                self.table.setItem(row, column, QTableWidgetItem(text))
        self.table.clearSelection()
        self.table.blockSignals(False)

    def done(self, result):
        if self.thread is not None:
            self.close_requested = True
            self.worker.cancel()
            return
        if self.usage is not None:
            self.usage.close()
            self.usage = None
        super().done(result)

    def closeEvent(self, event):
        self.done(QDialog.DialogCode.Rejected)
        event.ignore() if self.thread is not None else event.accept()
