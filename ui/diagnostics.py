from __future__ import annotations

import sqlite3
import threading

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from diagnostics import (
    DiagnosticCancelled,
    DiagnosticReport,
    scan_snapshot_problems,
)
from snapshot import FileSnapshot
from ui.analysis import format_size


class DiagnosticWorker(QObject):
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
            result = scan_snapshot_problems(
                self.snapshot,
                cancelled=self._cancelled,
                progress=self.progress.emit,
            )
        except DiagnosticCancelled:
            self.finished.emit(None, True, "")
        except (OSError, RuntimeError, ValueError, sqlite3.Error) as error:
            self.finished.emit(None, False, str(error))
        else:
            self.finished.emit(result, False, "")


class DiagnosticDialog(QDialog):
    def __init__(self, report: DiagnosticReport, parent=None) -> None:
        super().__init__(parent)
        self._staged_delete_paths = ()
        self.setWindowTitle("Duplicate and Problem Files")
        self.resize(1_050, 720)
        layout = QVBoxLayout(self)

        summary = QWidget()
        form = QFormLayout(summary)
        form.addRow("Duplicate groups", QLabel(f"{len(report.duplicate_groups):,}"))
        form.addRow("Duplicate files", QLabel(f"{report.duplicate_file_count:,}"))
        form.addRow("Potential savings", QLabel(format_size(report.reclaimable_size)))
        form.addRow("Files hashed", QLabel(f"{report.hashed_file_count:,}"))
        form.addRow("Bytes hashed", QLabel(format_size(report.hashed_bytes)))
        form.addRow("Other problems", QLabel(f"{report.problem_count:,}"))
        layout.addWidget(summary)

        tabs = QTabWidget()
        self.duplicates = duplicate_tree(report)
        tabs.addTab(
            self.duplicates,
            f"Duplicates ({len(report.duplicate_groups):,})",
        )
        tabs.addTab(
            path_table(report.empty_folders),
            f"Empty Folders ({len(report.empty_folders):,})",
        )
        tabs.addTab(
            collision_tree(report),
            f"Name Collisions ({len(report.name_collisions):,})",
        )
        tabs.addTab(
            problem_table(report.long_paths),
            f"Long Paths ({len(report.long_paths):,})",
        )
        tabs.addTab(
            problem_table(report.inaccessible_items),
            f"Inaccessible ({len(report.inaccessible_items):,})",
        )
        tabs.addTab(
            problem_table(report.broken_links),
            f"Broken Links ({len(report.broken_links):,})",
        )
        tabs.addTab(
            problem_table(report.changed_items),
            f"Changed / Missing ({len(report.changed_items):,})",
        )
        layout.addWidget(tabs, 1)

        note = QLabel(
            "Duplicate groups are confirmed with SHA-256 after size filtering. "
            "Select duplicate file rows to stage DELETE operations; nothing is "
            "deleted until Apply All. Changed files are excluded from duplicate "
            "results. Long paths use the 260-character compatibility threshold."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.stage_button = buttons.addButton(
            "Stage Selected Deletes",
            QDialogButtonBox.ButtonRole.ActionRole,
        )
        self.stage_button.setEnabled(False)
        self.stage_button.clicked.connect(self.accept_selected_deletes)
        self.duplicates.itemSelectionChanged.connect(self.update_stage_button)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def staged_delete_paths(self):
        return self._staged_delete_paths

    def update_stage_button(self) -> None:
        self.stage_button.setEnabled(bool(self.selected_duplicate_paths()))

    def selected_duplicate_paths(self):
        return tuple(
            path
            for item in self.duplicates.selectedItems()
            if (path := item.data(0, Qt.ItemDataRole.UserRole)) is not None
        )

    def accept_selected_deletes(self) -> None:
        paths = self.selected_duplicate_paths()
        if not paths:
            return
        self._staged_delete_paths = paths
        self.accept()


def duplicate_tree(report: DiagnosticReport) -> QTreeWidget:
    tree = QTreeWidget()
    tree.setHeaderLabels(
        ("Group / Path", "Size", "Copies", "Potential Savings", "SHA-256")
    )
    tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    tree.setUniformRowHeights(True)
    tree.setColumnWidth(0, 470)
    tree.setColumnWidth(1, 100)
    tree.setColumnWidth(2, 70)
    tree.setColumnWidth(3, 130)
    for number, group in enumerate(report.duplicate_groups, start=1):
        parent = QTreeWidgetItem(
            (
                f"Duplicate group {number:,}",
                format_size(group.size),
                f"{len(group.paths):,}",
                format_size(group.reclaimable_size),
                group.digest,
            )
        )
        parent.setToolTip(4, group.digest)
        tree.addTopLevelItem(parent)
        for path in group.paths:
            child = QTreeWidgetItem((path.as_posix(), "", "", "", ""))
            child.setToolTip(0, path.as_posix())
            child.setData(0, Qt.ItemDataRole.UserRole, path)
            parent.addChild(child)
    tree.header().setStretchLastSection(True)
    return tree


def collision_tree(report: DiagnosticReport) -> QTreeWidget:
    tree = QTreeWidget()
    tree.setHeaderLabels(("Collision / Path", "Items"))
    tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    tree.setUniformRowHeights(True)
    tree.setColumnWidth(0, 760)
    for number, collision in enumerate(report.name_collisions, start=1):
        parent = QTreeWidgetItem(
            (f"Case-insensitive name collision {number:,}", f"{len(collision.paths):,}")
        )
        tree.addTopLevelItem(parent)
        for path in collision.paths:
            child = QTreeWidgetItem((path.as_posix(), ""))
            child.setToolTip(0, path.as_posix())
            parent.addChild(child)
    tree.header().setStretchLastSection(True)
    return tree


def path_table(paths) -> QTableWidget:
    table = QTableWidget(len(paths), 1)
    table.setHorizontalHeaderLabels(("Path",))
    for row, path in enumerate(paths):
        set_item(table, row, 0, path.as_posix())
    configure_table(table)
    return table


def problem_table(problems) -> QTableWidget:
    table = QTableWidget(len(problems), 2)
    table.setHorizontalHeaderLabels(("Path", "Details"))
    for row, problem in enumerate(problems):
        path = (
            problem.path.as_posix() if problem.path is not None else "(Snapshot root)"
        )
        set_item(table, row, 0, path)
        set_item(table, row, 1, problem.details)
    configure_table(table)
    return table


def configure_table(table: QTableWidget) -> None:
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setColumnWidth(0, 620)
    table.horizontalHeader().setStretchLastSection(True)


def set_item(table: QTableWidget, row: int, column: int, value: str) -> None:
    item = QTableWidgetItem(value)
    item.setToolTip(value)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    table.setItem(row, column, item)
