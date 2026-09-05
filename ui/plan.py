from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from PySide6.QtCore import (
    QAbstractItemModel,
    QMimeData,
    QModelIndex,
    Qt,
    QThread,
    Signal,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStyle,
    QTreeView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from planning import FilePlan, PlanAction, PlanOperation, normalize_plan_path
from plan_diff import simulate_plan
from real.operations import validate_name
from snapshot import QUERY_PAGE_SIZE, FileSnapshot, SnapshotEntry, SnapshotWorker
from ui.batch import BatchDialog
from ui.plan_diff import PlanDiffDialog

INVALID_INDEX = QModelIndex()
PLAN_MIME_TYPE = "application/x-file-tree-viewer-plan-paths"


@dataclass(slots=True)
class SnapshotNode:
    entry: SnapshotEntry | None
    parent: SnapshotNode | None = None
    children: list[SnapshotNode] = field(default_factory=list)
    child_count: int | None = None

    @property
    def path(self) -> PurePosixPath | None:
        return self.entry.path if self.entry is not None else None

    @property
    def is_directory(self) -> bool:
        return self.entry is None or self.entry.is_directory


class SnapshotTreeModel(QAbstractItemModel):
    move_requested = Signal(object, object)
    headers = ("Name", "Type", "Size", "Modified")

    def __init__(self, snapshot: FileSnapshot, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.snapshot = snapshot
        self.root = SnapshotNode(None)

    def rowCount(self, parent: QModelIndex = INVALID_INDEX) -> int:
        if parent.column() > 0:
            return 0
        if not parent.isValid():
            return 1
        node = self.node_from_index(parent)
        return len(node.children) if node is not None and node.is_directory else 0

    def columnCount(self, parent: QModelIndex = INVALID_INDEX) -> int:
        return len(self.headers)

    def index(
        self,
        row: int,
        column: int,
        parent: QModelIndex = INVALID_INDEX,
    ) -> QModelIndex:
        if row < 0 or column < 0 or column >= len(self.headers):
            return QModelIndex()
        if not parent.isValid():
            return self.createIndex(0, column, self.root) if row == 0 else QModelIndex()
        parent_node = self.node_from_index(parent)
        if parent_node is None or row >= len(parent_node.children):
            return QModelIndex()
        return self.createIndex(row, column, parent_node.children[row])

    def parent(self, index: QModelIndex) -> QModelIndex:
        node = self.node_from_index(index)
        if node is None or node.parent is None:
            return QModelIndex()
        parent = node.parent
        if parent is self.root:
            return self.createIndex(0, 0, parent)
        if parent.parent is None:
            return QModelIndex()
        return self.createIndex(parent.parent.children.index(parent), 0, parent)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        node = self.node_from_index(index)
        if node is None:
            return None
        entry = node.entry

        if role == Qt.ItemDataRole.DisplayRole:
            if entry is None:
                return self._root_data(index.column())
            if index.column() == 0:
                return entry.name
            if index.column() == 1:
                return snapshot_type(entry)
            if index.column() == 2:
                return "-" if entry.is_directory else format_size(entry.size)
            if index.column() == 3:
                return format_modified(entry.modified_ns)

        if role == Qt.ItemDataRole.DecorationRole and index.column() == 0:
            icon = (
                QStyle.StandardPixmap.SP_DirIcon
                if node.is_directory
                else QStyle.StandardPixmap.SP_FileIcon
            )
            return QApplication.style().standardIcon(icon)
        if role == Qt.ItemDataRole.ToolTipRole:
            if entry is None:
                return str(self.snapshot.root)
            if entry.error:
                return f"{entry.path.as_posix()}\n{entry.error}"
            return entry.path.as_posix()
        if role == Qt.ItemDataRole.UserRole:
            return node
        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
            and 0 <= section < len(self.headers)
        ):
            return self.headers[section]
        return None

    def hasChildren(self, parent: QModelIndex = INVALID_INDEX) -> bool:
        if not parent.isValid():
            return True
        node = self.node_from_index(parent)
        return bool(
            node is not None and node.is_directory and self._child_count(node) > 0
        )

    def canFetchMore(self, parent: QModelIndex) -> bool:
        node = self.node_from_index(parent)
        return bool(
            node is not None
            and node.is_directory
            and len(node.children) < self._child_count(node)
        )

    def fetchMore(self, parent: QModelIndex) -> None:
        node = self.node_from_index(parent)
        if node is None or not node.is_directory:
            return
        first = len(node.children)
        remaining = self._child_count(node) - first
        if remaining <= 0:
            return
        entries = self.snapshot.children(
            node.path,
            offset=first,
            limit=min(QUERY_PAGE_SIZE, remaining),
        )
        if not entries:
            node.child_count = first
            return
        self.beginInsertRows(parent, first, first + len(entries) - 1)
        node.children.extend(SnapshotNode(entry, node) for entry in entries)
        self.endInsertRows()

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.ItemIsDropEnabled
        node = self.node_from_index(index)
        if node is None:
            return Qt.ItemFlag.NoItemFlags
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if node.entry is not None:
            flags |= Qt.ItemFlag.ItemIsDragEnabled
        if node.is_directory:
            flags |= Qt.ItemFlag.ItemIsDropEnabled
        return flags

    def mimeTypes(self) -> list[str]:
        return [PLAN_MIME_TYPE]

    def mimeData(self, indexes: list[QModelIndex]) -> QMimeData:
        paths = []
        seen = set()
        for index in indexes:
            if index.column() != 0:
                continue
            node = self.node_from_index(index)
            if node is None or node.path is None:
                continue
            path = node.path.as_posix()
            if path not in seen:
                paths.append(path)
                seen.add(path)
        data = QMimeData()
        data.setData(PLAN_MIME_TYPE, json.dumps(paths).encode("utf-8"))
        return data

    def dropMimeData(
        self,
        data: QMimeData,
        action: Qt.DropAction,
        row: int,
        column: int,
        parent: QModelIndex,
    ) -> bool:
        if action == Qt.DropAction.IgnoreAction:
            return True
        if action != Qt.DropAction.MoveAction or not data.hasFormat(PLAN_MIME_TYPE):
            return False
        target = self.node_from_index(parent) if parent.isValid() else self.root
        if target is None or not target.is_directory:
            return False
        try:
            raw_paths = json.loads(bytes(data.data(PLAN_MIME_TYPE)).decode("utf-8"))
            sources = tuple(PurePosixPath(path) for path in raw_paths)
        except (TypeError, ValueError, UnicodeError, json.JSONDecodeError):
            return False
        if not sources:
            return False
        self.move_requested.emit(sources, target.path)
        return True

    def supportedDropActions(self) -> Qt.DropAction:
        return Qt.DropAction.MoveAction

    def supportedDragActions(self) -> Qt.DropAction:
        return Qt.DropAction.MoveAction

    @staticmethod
    def node_from_index(index: QModelIndex) -> SnapshotNode | None:
        if not index.isValid():
            return None
        node = index.internalPointer()
        return node if isinstance(node, SnapshotNode) else None

    def _child_count(self, node: SnapshotNode) -> int:
        if node.child_count is None:
            node.child_count = self.snapshot.child_count(node.path)
        return node.child_count

    def _root_data(self, column: int) -> str:
        if column == 0:
            return self.snapshot.root.name or str(self.snapshot.root)
        if column == 1:
            return "Snapshot Root"
        return "-"


class PlanExplorerPage(QWidget):
    status_changed = Signal(str)
    ready_to_close = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.snapshot: FileSnapshot | None = None
        self.snapshot_model: SnapshotTreeModel | None = None
        self.plan = FilePlan()
        self.scan_thread: QThread | None = None
        self.scan_worker: SnapshotWorker | None = None
        self.scan_outcome: tuple[object, bool, str] | None = None
        self.close_requested = False
        self.close_cancelled = False

        layout = QVBoxLayout(self)
        layout.addLayout(self._create_root_row())
        layout.addWidget(self._create_content(), 1)
        layout.addLayout(self._create_action_row())
        self._set_scanning(False)
        self._refresh_plan_list()
        self.update_buttons()

    @property
    def scan_is_running(self) -> bool:
        return self.scan_thread is not None

    def _create_root_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(QLabel("Plan Root"))
        self.location = QLineEdit()
        self.location.setReadOnly(True)
        self.location.setPlaceholderText("Import a real folder or drive")
        row.addWidget(self.location, 1)
        self.import_button = QPushButton("Import Snapshot")
        self.cancel_button = QPushButton("Cancel")
        self.progress_label = QLabel("No snapshot loaded")
        row.addWidget(self.import_button)
        row.addWidget(self.cancel_button)
        row.addWidget(self.progress_label)
        self.import_button.clicked.connect(self.select_snapshot_root)
        self.cancel_button.clicked.connect(self.cancel_snapshot)
        return row

    def _create_content(self) -> QSplitter:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.tree = QTreeView()
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tree.setUniformRowHeights(True)
        self.tree.setDragEnabled(True)
        self.tree.setAcceptDrops(True)
        self.tree.setDropIndicatorShown(True)
        self.tree.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.tree.setDefaultDropAction(Qt.DropAction.MoveAction)
        splitter.addWidget(self.tree)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.plan_count = QLabel()
        right_layout.addWidget(self.plan_count)
        self.plan_list = QTreeWidget()
        self.plan_list.setHeaderLabels(("Action", "Source", "Target"))
        self.plan_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.plan_list.header().resizeSection(0, 110)
        self.plan_list.header().resizeSection(1, 260)
        right_layout.addWidget(self.plan_list, 1)
        plan_buttons = QHBoxLayout()
        self.remove_button = QPushButton("Remove Selected")
        self.clear_button = QPushButton("Clear Plan")
        plan_buttons.addWidget(self.remove_button)
        plan_buttons.addWidget(self.clear_button)
        plan_buttons.addStretch(1)
        right_layout.addLayout(plan_buttons)
        self.remove_button.clicked.connect(self.remove_selected_operations)
        self.clear_button.clicked.connect(self.clear_plan)
        self.plan_list.itemSelectionChanged.connect(self.update_buttons)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        return splitter

    def _create_action_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.new_file_button = QPushButton("New File")
        self.new_folder_button = QPushButton("New Folder")
        self.rename_button = QPushButton("Rename")
        self.move_button = QPushButton("Move")
        self.delete_button = QPushButton("Delete")
        self.batch_rename_button = QPushButton("Batch Rename")
        self.organize_button = QPushButton("Organize Files")
        self.diff_button = QPushButton("Diff / Simulate")
        for button in (
            self.new_file_button,
            self.new_folder_button,
            self.rename_button,
            self.move_button,
            self.delete_button,
            self.batch_rename_button,
            self.organize_button,
            self.diff_button,
        ):
            row.addWidget(button)
        row.addStretch(1)
        self.new_file_button.clicked.connect(lambda: self.stage_create(False))
        self.new_folder_button.clicked.connect(lambda: self.stage_create(True))
        self.rename_button.clicked.connect(self.stage_rename)
        self.move_button.clicked.connect(self.stage_move_dialog)
        self.delete_button.clicked.connect(self.stage_delete)
        self.batch_rename_button.clicked.connect(lambda: self.stage_batch(True))
        self.organize_button.clicked.connect(lambda: self.stage_batch(False))
        self.diff_button.clicked.connect(self.show_plan_diff)
        return row

    def select_snapshot_root(self) -> None:
        if self.scan_is_running:
            return
        directory = QFileDialog.getExistingDirectory(self, "Import Snapshot")
        if not directory:
            return
        if len(self.plan) and not self._confirm_plan_replacement():
            return
        self.start_snapshot(Path(directory))

    def start_snapshot(self, root: Path) -> None:
        if self.scan_is_running:
            return
        thread = QThread(self)
        worker = SnapshotWorker(root)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self.show_scan_progress)
        worker.finished.connect(self.receive_scan_result)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self.scan_thread_finished)
        thread.finished.connect(thread.deleteLater)
        self.scan_thread = thread
        self.scan_worker = worker
        self.scan_outcome = None
        self._set_scanning(True)
        self.progress_label.setText("Scanning...")
        self.status_changed.emit(f"Creating snapshot: {root}")
        thread.start()

    def cancel_snapshot(self) -> None:
        if self.scan_worker is not None:
            self.scan_worker.cancel()
            self.progress_label.setText("Cancelling...")

    def show_scan_progress(self, scanned: int, errors: int) -> None:
        self.progress_label.setText(f"{scanned:,} items, {errors:,} errors")

    def receive_scan_result(self, result: object, cancelled: bool, error: str) -> None:
        self.scan_outcome = (result, cancelled, error)

    def scan_thread_finished(self) -> None:
        thread = self.sender()
        if thread is not self.scan_thread:
            return
        outcome = self.scan_outcome
        self.scan_thread = None
        self.scan_worker = None
        self.scan_outcome = None
        self._set_scanning(False)

        if self.close_requested:
            if outcome is not None and isinstance(outcome[0], FileSnapshot):
                outcome[0].close()
            self.ready_to_close.emit()
            return
        if outcome is None:
            self.progress_label.setText("Snapshot failed")
            return

        result, cancelled, error = outcome
        if error:
            self.progress_label.setText("Snapshot failed")
            QMessageBox.warning(self, "Cannot import snapshot", error)
            self.status_changed.emit(error)
        elif cancelled:
            self.progress_label.setText("Snapshot cancelled")
            self.status_changed.emit("Snapshot cancelled")
        elif isinstance(result, FileSnapshot):
            self._replace_snapshot(result)

    def _replace_snapshot(self, snapshot: FileSnapshot) -> None:
        old_snapshot = self.snapshot
        old_model = self.snapshot_model
        self.snapshot = snapshot
        self.snapshot_model = SnapshotTreeModel(snapshot, self)
        self.snapshot_model.move_requested.connect(self.stage_moves)
        self.tree.setModel(self.snapshot_model)
        selection = self.tree.selectionModel()
        if selection is not None:
            selection.selectionChanged.connect(self.update_buttons)
        self.tree.header().resizeSection(0, 360)
        self.tree.header().resizeSection(1, 130)
        self.tree.header().resizeSection(2, 100)
        self.tree.header().resizeSection(3, 160)
        root_index = self.snapshot_model.index(0, 0)
        if self.snapshot_model.canFetchMore(root_index):
            self.snapshot_model.fetchMore(root_index)
        self.tree.expand(root_index)
        self.plan = FilePlan(snapshot.root)
        self.location.setText(str(snapshot.root))
        self.progress_label.setText(
            f"{snapshot.entry_count:,} items, {snapshot.error_count:,} errors"
        )
        self._refresh_plan_list()
        self.update_buttons()
        if old_model is not None:
            old_model.deleteLater()
        if old_snapshot is not None:
            old_snapshot.close()
        self.status_changed.emit(f"Snapshot ready: {snapshot.entry_count:,} items")

    def stage_create(self, directory: bool) -> None:
        if self.snapshot is None or self.scan_is_running:
            return
        parent = self._selected_directory_path()
        item_type = "Folder" if directory else "File"
        name, accepted = QInputDialog.getText(
            self,
            f"New Planned {item_type}",
            f"{item_type} name:",
        )
        if not accepted:
            return
        try:
            clean_name = validate_name(name)
            target = join_plan_path(parent, clean_name)
            operation = (
                self.plan.add_create_folder(target)
                if directory
                else self.plan.add_create_file(target)
            )
        except ValueError as error:
            self._show_plan_error(error)
            return
        self._finish_staging(operation)

    def stage_rename(self) -> None:
        entries = self._selected_entries()
        if len(entries) != 1 or self.scan_is_running:
            return
        entry = entries[0]
        name, accepted = QInputDialog.getText(
            self,
            "Plan Rename",
            "New name:",
            text=entry.name,
        )
        if not accepted:
            return
        try:
            clean_name = validate_name(name)
            target = entry.path.with_name(clean_name)
            operation = self.plan.add_rename(entry.path, target)
        except ValueError as error:
            self._show_plan_error(error)
            return
        self._finish_staging(operation)

    def stage_batch(self, rename: bool) -> None:
        if self.snapshot is None or self.scan_is_running:
            return
        selected = top_level_entries(self._selected_entries())
        if rename and not selected:
            return
        dialog = BatchDialog(
            self.snapshot, self.plan, selected, rename=rename, parent=self
        )
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            for operation in dialog.operations:
                self.plan.append(operation)
            self._refresh_plan_list()
            self.status_changed.emit(
                f"Staged {len(dialog.operations):,} batch operation(s)"
            )
        finally:
            dialog.deleteLater()

    def stage_delete(self) -> None:
        entries = self._selected_entries()
        if not entries or self.scan_is_running:
            return
        added = 0
        for entry in top_level_entries(entries):
            try:
                self.plan.add_delete(entry.path)
            except ValueError as error:
                self._show_plan_error(error)
                continue
            added += 1
        if added:
            self._refresh_plan_list()
            self.status_changed.emit(f"Staged {added} delete operation(s)")

    def stage_move_dialog(self) -> None:
        entries = self._selected_entries()
        if not entries or self.scan_is_running:
            return
        destination, accepted = QInputDialog.getText(
            self,
            "Plan Move",
            "Destination folder relative to the Plan root (empty for root):",
        )
        if not accepted:
            return
        try:
            target = normalize_plan_path(destination) if destination else None
            self.stage_moves(tuple(entry.path for entry in entries), target)
        except ValueError as error:
            self._show_plan_error(error)

    def stage_moves(
        self,
        sources: tuple[PurePosixPath, ...],
        destination: PurePosixPath | None,
    ) -> None:
        try:
            destination = (
                normalize_plan_path(destination) if destination is not None else None
            )
            sources = tuple(normalize_plan_path(source) for source in sources)
        except ValueError as error:
            self._show_plan_error(error)
            return
        if self.snapshot is None or not self._directory_exists(destination):
            self._show_plan_error(ValueError("The destination folder does not exist"))
            return
        added = 0
        for source in top_level_paths(sources):
            entry = self.snapshot.entry(source)
            if entry is None:
                continue
            target = join_plan_path(destination, entry.name)
            if source == target:
                continue
            if (
                entry.is_directory
                and destination is not None
                and (destination == source or destination.is_relative_to(source))
            ):
                self._show_plan_error(
                    ValueError("A folder cannot be moved inside itself")
                )
                continue
            try:
                self.plan.add_move(source, target)
            except ValueError as error:
                self._show_plan_error(error)
                continue
            added += 1
        if added:
            self._refresh_plan_list()
            self.status_changed.emit(f"Staged {added} move operation(s)")

    def remove_selected_operations(self) -> None:
        operation_ids = {
            item.data(0, Qt.ItemDataRole.UserRole)
            for item in self.plan_list.selectedItems()
        }
        removed = 0
        for operation_id in operation_ids:
            if not isinstance(operation_id, str):
                continue
            try:
                self.plan.remove(operation_id)
            except KeyError:
                continue
            removed += 1
        if removed:
            self._refresh_plan_list()
            self.status_changed.emit(f"Removed {removed} planned operation(s)")

    def clear_plan(self) -> None:
        if not len(self.plan):
            return
        self.plan.clear()
        self._refresh_plan_list()
        self.status_changed.emit("Plan cleared")

    def show_plan_diff(self) -> None:
        if self.snapshot is None or not len(self.plan) or self.scan_is_running:
            return
        try:
            simulation = simulate_plan(self.snapshot, self.plan)
        except (ValueError, RuntimeError, sqlite3.Error) as error:
            self._show_plan_error(error)
            return
        dialog = PlanDiffDialog(simulation, self)
        dialog.exec()
        dialog.deleteLater()
        if simulation.can_apply:
            self.status_changed.emit(
                f"Simulation complete: {len(simulation.changes):,} changes, no problems"
            )
        else:
            self.status_changed.emit(
                f"Simulation complete: {len(simulation.issues):,} problem(s)"
            )

    def _finish_staging(self, operation: PlanOperation) -> None:
        self._refresh_plan_list()
        self.status_changed.emit(f"Staged {operation.action.value}")

    def _refresh_plan_list(self) -> None:
        self.plan_list.clear()
        for operation in self.plan.operations:
            item = QTreeWidgetItem(
                (
                    operation.action.value.replace("_", " ").upper(),
                    operation.source.as_posix()
                    if operation.source is not None
                    else "-",
                    operation.target.as_posix()
                    if operation.target is not None
                    else "-",
                )
            )
            item.setData(0, Qt.ItemDataRole.UserRole, operation.operation_id)
            self.plan_list.addTopLevelItem(item)
        self.plan_count.setText(f"PLAN — {len(self.plan):,} staged operation(s)")
        self.update_buttons()

    def _selected_nodes(self) -> list[SnapshotNode]:
        nodes = []
        seen = set()
        selection = self.tree.selectionModel()
        if selection is None:
            return nodes
        for index in selection.selectedRows(0):
            node = SnapshotTreeModel.node_from_index(index)
            if node is not None and id(node) not in seen:
                nodes.append(node)
                seen.add(id(node))
        return nodes

    def _selected_entries(self) -> list[SnapshotEntry]:
        return [node.entry for node in self._selected_nodes() if node.entry is not None]

    def _selected_directory_path(self) -> PurePosixPath | None:
        nodes = self._selected_nodes()
        if len(nodes) != 1:
            return None
        node = nodes[0]
        if node.is_directory:
            return node.path
        return node.entry.parent if node.entry is not None else None

    def _directory_exists(self, path: PurePosixPath | None) -> bool:
        if path is None:
            return True
        if self.snapshot is not None:
            entry = self.snapshot.entry(path)
            if entry is not None and entry.is_directory:
                return True
        return any(
            operation.action is PlanAction.CREATE_FOLDER and operation.target == path
            for operation in self.plan.operations
        )

    def _confirm_plan_replacement(self) -> bool:
        answer = QMessageBox.question(
            self,
            "Discard current plan",
            "Importing another snapshot will discard all staged operations. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _show_plan_error(self, error: Exception) -> None:
        QMessageBox.warning(self, "Cannot stage operation", str(error))
        self.status_changed.emit(str(error))

    def _set_scanning(self, scanning: bool) -> None:
        self.import_button.setEnabled(not scanning)
        self.cancel_button.setEnabled(scanning)
        self.tree.setEnabled(not scanning)
        self.plan_list.setEnabled(not scanning)
        self.update_buttons()

    def update_buttons(self) -> None:
        ready = self.snapshot is not None and not self.scan_is_running
        entries = self._selected_entries() if ready else []
        self.new_file_button.setEnabled(ready)
        self.new_folder_button.setEnabled(ready)
        self.rename_button.setEnabled(len(entries) == 1)
        self.move_button.setEnabled(bool(entries))
        self.delete_button.setEnabled(bool(entries))
        self.batch_rename_button.setEnabled(bool(entries))
        self.organize_button.setEnabled(ready)
        self.diff_button.setEnabled(ready and bool(len(self.plan)))
        self.remove_button.setEnabled(bool(self.plan_list.selectedItems()))
        self.clear_button.setEnabled(bool(len(self.plan)) and not self.scan_is_running)

    def prepare_close(self) -> bool:
        self.close_cancelled = False
        if self.scan_is_running:
            self.close_requested = True
            self.cancel_snapshot()
            return False
        if len(self.plan):
            answer = QMessageBox.question(
                self,
                "Discard current plan",
                "Discard all staged operations and close?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.close_cancelled = True
                return False
            self.plan.clear()
        self.close_requested = True
        return True

    def cancel_close_request(self) -> None:
        self.close_requested = False

    def finalize_close(self) -> None:
        if self.snapshot is not None:
            self.snapshot.close()
            self.snapshot = None


def join_plan_path(parent: PurePosixPath | None, name: str) -> PurePosixPath:
    return parent / name if parent is not None else PurePosixPath(name)


def top_level_paths(paths: tuple[PurePosixPath, ...]) -> tuple[PurePosixPath, ...]:
    unique = tuple(dict.fromkeys(paths))
    selected = set(unique)
    return tuple(
        path
        for path in unique
        if not any(parent in selected for parent in path.parents)
    )


def top_level_entries(entries: list[SnapshotEntry]) -> tuple[SnapshotEntry, ...]:
    paths = top_level_paths(tuple(entry.path for entry in entries))
    by_path = {entry.path: entry for entry in entries}
    return tuple(by_path[path] for path in paths)


def snapshot_type(entry: SnapshotEntry) -> str:
    if entry.is_symlink:
        return "Symbolic Link"
    if entry.is_directory:
        return "Folder"
    suffix = PurePosixPath(entry.name).suffix
    return f"{suffix[1:].upper()} File" if suffix else "File"


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1_024 or unit == "TB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1_024
    return "-"


def format_modified(modified_ns: int) -> str:
    if modified_ns <= 0:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(modified_ns / 1_000_000_000))
