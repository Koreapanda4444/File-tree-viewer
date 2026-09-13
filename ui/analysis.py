from __future__ import annotations

import sqlite3
import threading

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from analysis import AnalysisCancelled, StructureAnalysis, analyze_snapshot
from snapshot import FileSnapshot


class StructureAnalysisWorker(QObject):
    progress = Signal(str)
    finished = Signal(object, bool, str)

    def __init__(self, snapshot: FileSnapshot) -> None:
        super().__init__()
        self.snapshot = snapshot
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    @Slot()
    def run(self) -> None:
        try:
            result = analyze_snapshot(
                self.snapshot,
                cancelled=self._cancelled,
                progress=self.progress.emit,
            )
        except AnalysisCancelled:
            self.finished.emit(None, True, "")
        except (OSError, RuntimeError, ValueError, sqlite3.Error) as error:
            self.finished.emit(None, False, str(error))
        else:
            self.finished.emit(result, False, "")


class StructureAnalysisDialog(QDialog):
    def __init__(self, result: StructureAnalysis, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Folder Structure Analysis")
        self.resize(900, 700)
        layout = QVBoxLayout(self)

        summary = QWidget()
        form = QFormLayout(summary)
        form.addRow("Files", QLabel(f"{result.file_count:,}"))
        form.addRow("Folders", QLabel(f"{result.folder_count:,}"))
        form.addRow("Total size", QLabel(format_size(result.total_size)))
        form.addRow("Snapshot errors", QLabel(f"{result.error_count:,}"))
        form.addRow("Maximum depth", QLabel(f"{result.maximum_depth:,}"))
        deepest = QLabel(
            result.deepest_path.as_posix() if result.deepest_path is not None else "-"
        )
        deepest.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        deepest.setWordWrap(True)
        form.addRow("Deepest path", deepest)
        layout.addWidget(summary)

        tabs = QTabWidget()
        tabs.addTab(extension_table(result), "Extensions")
        tabs.addTab(path_table(result.largest_files), "Largest Files")
        tabs.addTab(path_table(result.largest_folders), "Largest Folders")
        layout.addWidget(tabs, 1)

        note = QLabel(
            f"Showing {len(result.extensions):,} of "
            f"{result.extension_group_count:,} extension groups and the top "
            "20 largest files and folders. Analysis uses Snapshot metadata only."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


def extension_table(result: StructureAnalysis) -> QTableWidget:
    table = QTableWidget(len(result.extensions), 3)
    table.setHorizontalHeaderLabels(("Extension", "Files", "Total Size"))
    for row, usage in enumerate(result.extensions):
        set_item(table, row, 0, usage.extension)
        set_item(table, row, 1, f"{usage.file_count:,}")
        set_item(table, row, 2, format_size(usage.total_size))
    configure_table(table)
    return table


def path_table(items) -> QTableWidget:
    table = QTableWidget(len(items), 2)
    table.setHorizontalHeaderLabels(("Path", "Size"))
    for row, item in enumerate(items):
        set_item(table, row, 0, item.path.as_posix())
        set_item(table, row, 1, format_size(item.size))
    configure_table(table)
    return table


def configure_table(table: QTableWidget) -> None:
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setColumnWidth(0, 620)
    table.horizontalHeader().setStretchLastSection(True)


def set_item(table: QTableWidget, row: int, column: int, value: str) -> None:
    item = QTableWidgetItem(value)
    item.setToolTip(value)
    table.setItem(row, column, item)


def format_size(size: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    value = float(size)
    for unit in units:
        if abs(value) < 1024 or unit == units[-1]:
            return f"{int(value):,} {unit}" if unit == "B" else f"{value:,.1f} {unit}"
        value /= 1024
    return f"{size:,} B"
