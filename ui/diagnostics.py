from __future__ import annotations

import sqlite3

from PySide6.QtCore import QSignalBlocker, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
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
    FileChangedError,
    scan_snapshot_problems,
)
from duplicate_management import (
    duplicate_selection_groups,
    prepare_duplicate_deletions,
    sorted_duplicate_groups,
)
from snapshot import FileSnapshot, SnapshotCancelled
from ui.analysis import format_size
from ui.tasks import CancellableWorker


class DiagnosticWorker(CancellableWorker):
    progress = Signal(str)
    finished = Signal(object, bool, str)

    def __init__(self, snapshot: FileSnapshot) -> None:
        super().__init__()
        self.snapshot = snapshot

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
    def __init__(
        self, report: DiagnosticReport, parent=None, *, already_deleted=()
    ) -> None:
        super().__init__(parent)
        self.report = report
        self.already_deleted = already_deleted
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

        tools = QHBoxLayout()
        self.order = QComboBox()
        for label, value in (
            ("Savings", "savings"),
            ("File size", "size"),
            ("Copy count", "copies"),
            ("Path", "path"),
        ):
            self.order.addItem(label, value)
        keep = QPushButton("Select All Except One per Group")
        clear = QPushButton("Clear Selection")
        self.selection_summary = QLabel("No duplicate candidates selected")
        tools.addWidget(self.order)
        tools.addWidget(keep)
        tools.addWidget(clear)
        layout.addLayout(tools)
        layout.addWidget(self.selection_summary)
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
        self.order.currentIndexChanged.connect(self.reorder_groups)
        keep.clicked.connect(self.select_redundant_copies)
        clear.clicked.connect(self.duplicates.clearSelection)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def staged_delete_paths(self):
        return self._staged_delete_paths

    def update_stage_button(self) -> None:
        paths = self.selected_duplicate_paths()
        try:
            duplicate_selection_groups(
                self.report, paths, already_deleted=self.already_deleted
            )
        except ValueError as error:
            self.stage_button.setEnabled(False)
            self.selection_summary.setText(str(error))
        else:
            self.stage_button.setEnabled(bool(paths))
            self.selection_summary.setText(
                f"{len(paths):,} candidate(s) selected; contents and retained copies will be rechecked before staging"
            )

    def selected_duplicate_paths(self):
        return tuple(
            path
            for item in self.duplicates.selectedItems()
            if (path := item.data(0, Qt.ItemDataRole.UserRole)) is not None
        )

    def reorder_groups(self) -> None:
        selected = set(self.selected_duplicate_paths())
        blocker = QSignalBlocker(self.duplicates)
        temporary = duplicate_tree(self.report, order=self.order.currentData())
        self.duplicates.clear()
        while temporary.topLevelItemCount():
            self.duplicates.addTopLevelItem(temporary.takeTopLevelItem(0))
        temporary.deleteLater()
        for index in range(self.duplicates.topLevelItemCount()):
            group = self.duplicates.topLevelItem(index)
            if selected.intersection(group.data(0, Qt.ItemDataRole.UserRole + 1) or ()):
                populate_duplicate_group(group)
            for row in range(group.childCount()):
                child = group.child(row)
                child.setSelected(child.data(0, Qt.ItemDataRole.UserRole) in selected)
        del blocker
        self.update_stage_button()

    def select_redundant_copies(self) -> None:
        blocker = QSignalBlocker(self.duplicates)
        self.duplicates.clearSelection()
        for index in range(self.duplicates.topLevelItemCount()):
            group = self.duplicates.topLevelItem(index)
            populate_duplicate_group(group)
            group.setExpanded(True)
            for row in range(1, group.childCount()):
                group.child(row).setSelected(True)
        del blocker
        self.update_stage_button()

    def accept_selected_deletes(self) -> None:
        paths = self.selected_duplicate_paths()
        if not paths:
            return
        try:
            duplicate_selection_groups(
                self.report, paths, already_deleted=self.already_deleted
            )
        except ValueError as error:
            QMessageBox.warning(self, "Invalid duplicate selection", str(error))
            return
        answer = QMessageBox.question(
            self,
            "Review Duplicate Deletes",
            f"Recheck and stage {len(paths):,} DELETE operation(s)? At least one matching copy must remain in each group. Files change only when the Plan is applied.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._staged_delete_paths = paths
            self.accept()


def duplicate_tree(report: DiagnosticReport, *, order="savings") -> QTreeWidget:
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
    for number, group in enumerate(sorted_duplicate_groups(report, order), start=1):
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
        parent.setData(0, Qt.ItemDataRole.UserRole + 1, group.paths)
        parent.setChildIndicatorPolicy(
            QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator
        )
    tree.itemExpanded.connect(populate_duplicate_group)
    tree.header().setStretchLastSection(True)
    return tree


def populate_duplicate_group(parent) -> None:
    if parent.childCount():
        return
    for path in parent.data(0, Qt.ItemDataRole.UserRole + 1) or ():
        child = QTreeWidgetItem((path.as_posix(), "", "", "", ""))
        child.setToolTip(0, path.as_posix())
        child.setData(0, Qt.ItemDataRole.UserRole, path)
        parent.addChild(child)


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


class DuplicateReviewWorker(CancellableWorker):
    progress = Signal(str, int)
    finished = Signal(object, bool, str)

    def __init__(self, snapshot, plan, report, paths) -> None:
        super().__init__()
        self.snapshot, self.plan, self.report, self.paths = (
            snapshot,
            plan,
            report,
            paths,
        )

    @Slot()
    def run(self) -> None:
        try:
            result = prepare_duplicate_deletions(
                self.snapshot,
                self.plan,
                self.report,
                self.paths,
                cancelled=self._cancelled,
                progress=self.progress.emit,
            )
        except (SnapshotCancelled, DiagnosticCancelled):
            self.finished.emit(None, True, "")
        except (
            OSError,
            ValueError,
            RuntimeError,
            FileChangedError,
            sqlite3.Error,
        ) as error:
            self.finished.emit(None, False, str(error))
        else:
            self.finished.emit(result, False, "")
