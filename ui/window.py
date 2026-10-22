from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import (
    QItemSelectionModel,
    QModelIndex,
    QPersistentModelIndex,
    QSettings,
    QSortFilterProxyModel,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QCloseEvent,
    QDesktopServices,
    QStandardItemModel,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from file_metadata import format_timestamp, inspect_file_metadata
from fs_ops import file_signature
from real.navigation import NavigationHistory
from real.operations import (
    FileChange,
    FileOperationWorker,
    OperationRecord,
    create_item,
    rename_item,
)
from real.preview import FilePreviewWorker, PreviewResult, save_text_atomic
from real.search import SearchResultsModel, SearchWorker
from real.sorting import FileSortModel
from real.tree import FileTreeModel
from search_matching import NameMatcher, SearchPatternError
from search_options import SearchOptions
from ui.address import AddressBar
from ui.completion import PathCompletion
from ui.feedback import TaskMonitor, show_error
from ui.file_commands import FileCommands
from ui.file_compare import FileComparisonDialog
from ui.file_drop import FileDropController
from ui.file_groups import FileGroupDelegate
from ui.file_views import FolderViews
from ui.image_preview import ImagePreview
from ui.live_search import LiveSearch
from ui.locations import LocationsButton, LocationStore
from ui.navigation_tabs import FolderTabs
from ui.operation_results import OperationResultsDialog, OperationResultsModel
from ui.plan import PlanExplorerPage
from ui.preferences import (
    Preferences,
    PreferencesDialog,
    application_settings,
    restore_layout,
    save_layout,
)
from ui.quick_access import QuickAccess
from ui.reveal import FileRevealer
from ui.search_options import SearchOptionsDialog
from ui.staging import StagingController
from ui.tasks import prepare_worker_thread
from ui.widgets import FlowLayout
from ui.widgets import PlainLabel as QLabel
from virtual.workspace import (
    VirtualNode,
    VirtualTaskWorker,
    VirtualTreeModel,
    VirtualUndoRecord,
    file_type,
    format_size,
    node_size,
)


class ExplorerPage(QWidget):
    def __init__(self, *, virtual: bool) -> None:
        super().__init__()
        self.virtual = virtual

        layout = QVBoxLayout(self)
        layout.addLayout(self._create_location_row())
        layout.addLayout(self._create_search_row())
        layout.addWidget(self._create_content(), 1)
        layout.addLayout(self._create_action_row())

    def _create_location_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        if not self.virtual:
            self.back_button = QPushButton("Back")
            self.forward_button = QPushButton("Forward")
            self.up_button = QPushButton("Up")
            for button in (self.back_button, self.forward_button, self.up_button):
                button.setEnabled(False)
                row.addWidget(button)
        row.addWidget(QLabel("Workspace" if self.virtual else "Root"))

        self.location = QLineEdit()
        self.location.setReadOnly(self.virtual)
        if self.virtual:
            self.location.setText("VM:/")
        else:
            self.location.setPlaceholderText(
                "Enter a folder or drive path, then press Enter"
            )
        if self.virtual:
            row.addWidget(self.location, 1)
        else:
            self.address_bar = AddressBar(self.location)
            row.addWidget(self.address_bar, 1)

        self.location_buttons = []
        if self.virtual:
            self.save_button = QPushButton("Save")
            self.load_button = QPushButton("Load")
            self.export_button = QPushButton("Export")
            self.reset_button = QPushButton("Reset")
            self.location_buttons.extend(
                (
                    self.save_button,
                    self.load_button,
                    self.export_button,
                    self.reset_button,
                )
            )
        else:
            self.select_root_button = QPushButton("Select Root")
            self.refresh_button = QPushButton("Refresh")
            self.location_buttons.extend((self.select_root_button, self.refresh_button))

        for button in self.location_buttons:
            button.setEnabled(False)
            row.addWidget(button)

        self.show_hidden = QCheckBox("Show hidden")
        self.show_hidden.setVisible(not self.virtual)
        self.show_hidden.setEnabled(False)
        row.addWidget(self.show_hidden)
        return row

    def _create_search_row(self) -> QHBoxLayout:
        row = QHBoxLayout()

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search files and folders")
        self.search_input.setEnabled(False)

        self.search_button = QPushButton("Search")
        self.clear_search_button = QPushButton("Clear")
        self.search_button.setEnabled(False)
        self.clear_search_button.setEnabled(False)

        row.addWidget(self.search_input, 1)
        row.addWidget(self.search_button)
        row.addWidget(self.clear_search_button)
        if not self.virtual:
            self.search_filters_button = QPushButton("Filters…")
            row.addWidget(self.search_filters_button)
            self.name_input = QLineEdit()
            self.name_input.setPlaceholderText("Jump to file or folder…")
            self.name_input.setMaximumWidth(280)
            self.name_input.setEnabled(False)
            self.jump_button = QPushButton("Jump")
            self.jump_button.setEnabled(False)
            row.addWidget(self.name_input)
            row.addWidget(self.jump_button)
        return row

    def _create_content(self) -> QSplitter:
        splitter = self.content_splitter = QSplitter(Qt.Orientation.Horizontal)

        self.tree = QTreeView()
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tree.setUniformRowHeights(True)

        empty_model = QStandardItemModel(self.tree)
        empty_model.setHorizontalHeaderLabels(("Name", "Type", "Size", "Modified"))
        self.tree.setModel(empty_model)
        self.tree.header().resizeSection(0, 360)
        if self.virtual:
            splitter.addWidget(self.tree)
        else:
            self.navigation_splitter = QSplitter(Qt.Orientation.Horizontal)
            self.navigation_splitter.addWidget(self.tree)
            self.folder_view = QTreeView()
            self.folder_view.setRootIsDecorated(False)
            self.folder_view.setItemsExpandable(False)
            self.folder_view.setUniformRowHeights(True)
            self.folder_view.setAlternatingRowColors(True)
            self.folder_view.setSelectionMode(
                QAbstractItemView.SelectionMode.ExtendedSelection
            )
            self.folder_view.setSelectionBehavior(
                QAbstractItemView.SelectionBehavior.SelectRows
            )
            self.folder_panel = FolderViews(self.folder_view)
            self.folder_icons = self.folder_panel.icons
            self.navigation_splitter.addWidget(self.folder_panel)
            self.navigation_splitter.setStretchFactor(0, 1)
            self.navigation_splitter.setStretchFactor(1, 3)
            splitter.addWidget(self.navigation_splitter)

        details = QWidget()
        details_layout = QVBoxLayout(details)
        details_layout.setContentsMargins(0, 0, 0, 0)

        details_group = QGroupBox("Details")
        details_form = QFormLayout(details_group)
        self.detail_values = {}
        for label in (
            "Name",
            "Path",
            "Type",
            "Size",
            "Modified",
            "Permissions",
            "Link target",
        ):
            value = QLabel("-")
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setWordWrap(label in ("Path", "Link target"))
            self.detail_values[label.lower()] = value
            details_form.addRow(f"{label}:", value)
        self.metadata_button = QPushButton("Inspect Metadata")
        self.metadata_button.setEnabled(False)
        self.metadata_button.setVisible(not self.virtual)
        details_form.addRow(self.metadata_button)
        details_layout.addWidget(details_group)

        preview_group = QGroupBox("Preview")
        preview_layout = QVBoxLayout(preview_group)

        preview_toolbar = QHBoxLayout()
        self.preview_status = QLabel("Select a file to preview it")
        self.preview_status.setWordWrap(True)
        preview_toolbar.addWidget(self.preview_status, 1)

        self.save_file_button = QPushButton("Save")
        self.reload_file_button = QPushButton("Reload")
        self.open_external_button = QPushButton("Open Externally")
        for button in (
            self.save_file_button,
            self.reload_file_button,
            self.open_external_button,
        ):
            button.setEnabled(False)
            preview_toolbar.addWidget(button)
        preview_layout.addLayout(preview_toolbar)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("Select a file to preview it")
        preview_layout.addWidget(self.preview)
        self.image_preview = ImagePreview()
        self.image_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_preview.setMinimumSize(1, 1)
        self.image_preview.hide()
        preview_layout.addWidget(self.image_preview)
        details_layout.addWidget(preview_group, 1)

        splitter.addWidget(details)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        return splitter

    def _create_action_row(self) -> FlowLayout:
        row = FlowLayout()
        actions = (
            ("new_file_button", "New File"),
            ("new_folder_button", "New Folder"),
            ("rename_button", "Rename"),
            ("copy_button", "Copy"),
            ("move_button", "Move"),
            ("delete_button", "Delete"),
            ("undo_button", "Undo"),
        )

        self.action_buttons = []
        for attribute, name in actions:
            button = QPushButton(name)
            button.setEnabled(False)
            setattr(self, attribute, button)
            self.action_buttons.append(button)
            row.addWidget(button)

        if not self.virtual:
            self.transfer_policy = QComboBox()
            for label, value in (
                ("Rename conflicts", "rename"),
                ("Skip conflicts", "skip"),
                ("Stop on conflict", "stop"),
            ):
                self.transfer_policy.addItem(label, value)
            self.transfer_policy.setToolTip(
                "Conflict policy for Real copy/move; existing files are preserved"
            )
            self.operation_results_button = QPushButton("Last Operation")
            self.operation_results_button.setEnabled(False)
            row.addWidget(self.transfer_policy)
            row.addWidget(self.operation_results_button)
            self.compare_files_button = QPushButton("Compare Files")
            row.addWidget(self.compare_files_button)
        return row


class RealExplorerPage(ExplorerPage):
    stage_requested = Signal(str)
    status_changed = Signal(str)
    task_failed = Signal(str)
    task_progress = Signal(str, int, int)
    ready_to_close = Signal()
    transfer_completed = Signal(str, object)
    location_changed = Signal(object)
    locations_changed = Signal()

    def __init__(self) -> None:
        super().__init__(virtual=False)

        self.locations = LocationStore()
        self.quick_access = QuickAccess(self.locations)
        self.quick_access.navigate.connect(self.set_root)
        self.quick_access.status_changed.connect(self.status_changed)
        self.quick_access.idle.connect(
            lambda: self._emit_ready_to_close() if self.close_requested else None
        )
        self.location_changed.connect(self.quick_access.select_path)
        self.locations_changed.connect(self.quick_access.refresh)
        sidebar = QWidget()
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.addWidget(self.quick_access)
        self.tree.setParent(sidebar)
        side_layout.addWidget(self.tree, 1)
        self.navigation_splitter.insertWidget(0, sidebar)
        QTimer.singleShot(0, self.quick_access.refresh)
        self.locations_button = LocationsButton(self)
        self.layout().itemAt(0).layout().addWidget(self.locations_button)
        self.history = NavigationHistory()
        self.history_navigation = False
        self.model = FileTreeModel(self)
        self.browse_model = FileSortModel(self.model, self)
        self.search_results = SearchResultsModel(self)
        self.search_thread: QThread | None = None
        self.search_worker: SearchWorker | None = None
        self.search_view_active = False
        self.close_requested = False
        self.search_outcome: tuple[int, int, int, bool] | None = None
        self.operation_thread: QThread | None = None
        self.operation_worker: FileOperationWorker | None = None
        self.operation_record: OperationRecord | None = None
        self.operation_outcome: tuple[str, list[FileChange], list[str]] | None = None
        self.undo_stack: list[OperationRecord] = []
        self.preview_thread: QThread | None = None
        self.preview_worker: FilePreviewWorker | None = None
        self.preview_outcome: PreviewResult | None = None
        self.pending_preview_path: Path | None = None
        self.current_preview_path: Path | None = None
        self.current_preview_result: PreviewResult | None = None
        self.preview_index = QPersistentModelIndex()
        self.preview_loading = False
        self.selection_guard = False
        self.close_cancelled = False
        self.preferences = Preferences()
        self.pending_folder = None
        self.operation_results = OperationResultsModel(self)
        self.operation_results_button.clicked.connect(self.show_operation_results)
        self.current_metadata = None
        self.metadata_button.clicked.connect(self.show_file_metadata)

        self.compare_files_button.clicked.connect(self.compare_selected_files)
        self._set_tree_model(self.model)
        self._configure_tree_columns()
        self.sort_field = QComboBox()
        self.sort_field.addItems(("Name", "Type", "Size", "Modified"))
        self.sort_order = QComboBox()
        self.sort_order.addItems(("Ascending", "Descending"))
        self.group_field = QComboBox()
        self.group_field.addItems(("None", "Type", "Date", "Size"))
        self.group_field.setToolTip(
            "Group loaded items; scroll to load additional pages"
        )
        self.folders_first = QCheckBox("Folders first")
        self.folders_first.setChecked(True)
        for control in (
            self.sort_field,
            self.sort_order,
            self.group_field,
            self.folders_first,
        ):
            self.folder_panel.toolbar.insertWidget(
                self.folder_panel.toolbar.count() - 1, control
            )
        for control in (self.sort_field, self.sort_order, self.group_field):
            control.currentIndexChanged.connect(self.update_sorting)
        self.folders_first.toggled.connect(self.update_sorting)
        for view in (self.folder_view, self.folder_icons):
            view.setItemDelegate(FileGroupDelegate(view))
        self.folder_view.setUniformRowHeights(False)
        self.folder_view.header().sectionClicked.connect(self.sort_by_header)

        self.select_root_button.setEnabled(True)
        self.show_hidden.setEnabled(True)

        self.back_button.clicked.connect(lambda: self.navigate_history(-1))
        self.forward_button.clicked.connect(lambda: self.navigate_history(1))
        self.up_button.clicked.connect(self.navigate_up)
        for shortcut, callback in (
            ("Alt+Left", lambda: self.navigate_history(-1)),
            ("Alt+Right", lambda: self.navigate_history(1)),
            ("Alt+Up", self.navigate_up),
        ):
            action = QAction(self)
            action.setShortcut(shortcut)
            action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            action.triggered.connect(callback)
            self.addAction(action)
        self.address_bar.navigate.connect(self.set_root)
        for shortcut in ("Alt+D", "Ctrl+L"):
            action = QAction(self)
            action.setShortcut(shortcut)
            action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            action.triggered.connect(self.address_bar.edit_path)
            self.addAction(action)
        self.location.returnPressed.connect(self.navigate_address)
        self.select_root_button.clicked.connect(self.choose_root)
        self.refresh_button.clicked.connect(self.refresh_root)
        self.show_hidden.toggled.connect(self.toggle_hidden)
        self.search_input.textChanged.connect(self.update_search_button)
        self.search_input.returnPressed.connect(self.start_search)
        self.search_button.clicked.connect(self.start_search)
        self.clear_search_button.clicked.connect(self.clear_search)
        self.new_file_button.clicked.connect(self.create_new_file)
        self.new_folder_button.clicked.connect(self.create_new_folder)
        self.rename_button.clicked.connect(self.rename_selected)
        self.copy_button.clicked.connect(lambda: self.transfer_selected("copy"))
        self.move_button.clicked.connect(lambda: self.transfer_selected("move"))
        self.delete_button.clicked.connect(self.delete_selected)
        self.undo_button.clicked.connect(self.undo_last_operation)
        self.save_file_button.clicked.connect(self.save_current_file)
        self.reload_file_button.clicked.connect(self.reload_current_file)
        self.open_external_button.clicked.connect(self.open_current_external)
        self.preview.textChanged.connect(self.editor_text_changed)
        self.tree.doubleClicked.connect(self.open_folder_index)
        self.folder_view.doubleClicked.connect(self.open_folder_index)
        self.folder_icons.doubleClicked.connect(self.open_folder_index)
        self.tree.expanded.connect(self.load_expanded_folder)
        self.tree.collapsed.connect(self.release_collapsed_folder)
        self.model.directory_error.connect(self.status_changed)
        self.search_results.drag_error.connect(self.status_changed)
        self.path_completion = PathCompletion(
            self.location,
            lambda: self.model.root_path or Path.home(),
            self.show_hidden.isChecked,
        )
        self.path_completion.chosen.connect(lambda value: self.navigate_address())
        self.file_revealer = FileRevealer(self.model)
        self.file_revealer.found.connect(self.reveal_file_index)
        self.file_revealer.failed.connect(self.status_changed)
        self.name_completion = PathCompletion(
            self.name_input,
            lambda: self.model.root_path or Path.home(),
            self.show_hidden.isChecked,
            directories_only=False,
        )
        self.name_completion.chosen.connect(self.jump_to_item)
        self.name_completion.idle.connect(
            lambda: self._emit_ready_to_close() if self.close_requested else None
        )
        self.name_input.returnPressed.connect(
            lambda: self.jump_to_item(self.name_input.text())
        )
        self.jump_button.clicked.connect(
            lambda: self.jump_to_item(self.name_input.text())
        )
        self.path_completion.idle.connect(
            lambda: self._emit_ready_to_close() if self.close_requested else None
        )
        self.navigation_tabs = FolderTabs(self)
        self.layout().insertWidget(0, self.navigation_tabs)
        self.file_commands = FileCommands(self)
        self.file_drop = FileDropController(self, self.file_commands)
        self.search_options = SearchOptions()
        self.live_search = LiveSearch(self)
        self.search_filters_button.clicked.connect(self.edit_search_options)

    @property
    def search_is_running(self) -> bool:
        return self.search_thread is not None

    @property
    def operation_is_running(self) -> bool:
        return self.operation_thread is not None

    @property
    def preview_is_running(self) -> bool:
        return self.preview_thread is not None

    def choose_root(self) -> None:
        initial = str(self.model.root_path or Path.home())
        selected = QFileDialog.getExistingDirectory(
            self,
            "Select root folder or drive",
            initial,
            QFileDialog.Option.ShowDirsOnly,
        )
        if selected:
            self.set_root(Path(selected))

    def navigate_address(self) -> None:
        text = self.location.text().strip().strip('"')
        if not text:
            self.location.setText(str(self.model.root_path or ""))
            return
        path = Path(os.path.expandvars(text)).expanduser()
        if not path.is_absolute():
            path = (self.model.root_path or Path.home()) / path
        self.set_root(path)
        self.location.setText(str(self.model.root_path or ""))

    def set_root(self, path: Path | None) -> bool:
        if (
            self.operation_is_running
            or self.search_is_running
            or self.preview_is_running
        ):
            self.status_changed.emit(
                "Wait for Real tasks to finish before changing the root"
            )
            return False
        if self.model.root_path is not None and not self._confirm_editor_transition():
            return False
        try:
            if path is None:
                self.model.clear()
            else:
                self.model.set_root(path)
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, "Cannot open root", str(error))
            return False

        self._show_path(None)
        self.search_results.clear()
        self.search_view_active = False
        self._set_tree_model(self.model)
        self._configure_tree_columns()
        self.live_search.stop()
        self.address_bar.set_path(self.model.root_path)
        self.path_completion.stop()
        self.path_completion.closed = False
        self.name_completion.stop()
        self.name_completion.closed = False
        self.name_input.clear()
        self.refresh_button.setEnabled(self.model.root_path is not None)
        self.search_input.setEnabled(self.model.root_path is not None)
        self.clear_search_button.setEnabled(bool(self.search_input.text()))
        self.update_search_button()
        self._expand_root()
        if not self.history_navigation and self.model.root_path is not None:
            self.history.visit(self.model.root_path)
        self.update_navigation_buttons()
        saved = self.model.root_path is None or self.locations.visit(
            self.model.root_path
        )
        self.location_changed.emit(self.model.root_path)
        self.status_changed.emit(
            f"Root: {self.model.root_path}"
            + ("" if saved else " — recent locations could not be saved")
        )
        return True

    def jump_to_item(self, text):
        if self.operation_is_running or self.search_is_running or not text.strip():
            return
        path = Path(text).expanduser()
        if not path.is_absolute():
            path = (self.model.root_path or Path.home()) / path
        path = Path(os.path.abspath(path))
        if not path.exists() and not path.is_symlink():
            self.status_changed.emit(f"Item no longer exists: {path}")
            return
        if path.is_dir() and not path.is_symlink():
            self.set_root(path)
            return
        if path.parent != self.model.root_path or self.search_view_active:
            if not self.set_root(path.parent):
                return
        elif not self._confirm_editor_transition():
            return
        self.status_changed.emit(f"Locating {path.name}…")
        self.file_revealer.start(path)

    def sort_by_header(self, column):
        if self.sort_field.currentIndex() == column:
            self.sort_order.setCurrentIndex(1 - self.sort_order.currentIndex())
        else:
            self.sort_field.setCurrentIndex(column)

    def update_sorting(self):
        self.browse_model.grouping = self.group_field.currentText()
        self.browse_model.folders_first = self.folders_first.isChecked()
        self.browse_model.invalidate()
        order = (
            Qt.SortOrder.DescendingOrder
            if self.sort_order.currentIndex()
            else Qt.SortOrder.AscendingOrder
        )
        self.browse_model.sort(self.sort_field.currentIndex(), order)
        self.folder_view.header().setSortIndicator(
            self.sort_field.currentIndex(), order
        )
        self.folder_view.header().setSortIndicatorShown(True)
        self.folder_icons.setUniformItemSizes(self.group_field.currentText() == "None")
        self.folder_view.viewport().update()
        self.folder_icons.viewport().update()

    def open_folder_index(self, index):
        path = self._path_from_index(index)
        if path is None or not path.is_dir() or path.is_symlink():
            return
        if self.preview_is_running:
            self.pending_folder = path
            self.pending_preview_path = None
            self.preview_worker.cancel()
        else:
            self.set_root(path)

    def reveal_file_index(self, index):
        index = self.browse_model.mapFromSource(index)
        self.tree.setCurrentIndex(index)
        self.tree.scrollTo(index)
        self.folder_panel.active_view.scrollTo(index)
        self.folder_panel.active_view.setFocus()
        self.status_changed.emit(f"Selected: {self._path_from_index(index)}")

    def toggle_favorite(self, path):
        if path is None:
            return
        try:
            saved = self.locations.toggle_favorite(path)
        except ValueError as error:
            self.status_changed.emit(str(error))
            return
        self.locations_changed.emit()
        self.status_changed.emit(
            "Favorites updated" if saved else "Favorites could not be saved"
        )

    def update_navigation_buttons(self):
        busy = (
            self.operation_is_running
            or self.search_is_running
            or self.preview_is_running
        )
        self.back_button.setEnabled(not busy and self.history.candidate(-1) is not None)
        self.forward_button.setEnabled(
            not busy and self.history.candidate(1) is not None
        )
        root = self.model.root_path
        self.up_button.setEnabled(not busy and root is not None and root.parent != root)

    def navigate_up(self):
        root = self.model.root_path
        if root is not None and root.parent != root:
            self.set_root(root.parent)

    def navigate_history(self, direction):
        if (
            self.operation_is_running
            or self.search_is_running
            or self.preview_is_running
        ):
            return
        candidate = self.history.candidate(direction)
        while candidate is not None:
            index, path = candidate
            if not path.is_dir():
                self.history.discard(index)
                self.status_changed.emit(f"Skipped unavailable location: {path}")
                candidate = self.history.candidate(direction)
                continue
            self.history_navigation = True
            try:
                if self.set_root(path):
                    self.history.position = index
            finally:
                self.history_navigation = False
            break
        self.update_navigation_buttons()

    def refresh_root(self) -> None:
        if self.model.root_path is None:
            return
        if not self._confirm_editor_transition():
            return
        self.clear_search()
        try:
            self.model.refresh()
        except (OSError, ValueError) as error:
            self._show_operation_error("Cannot refresh root", error)
            return
        self._expand_root()
        self.status_changed.emit("Tree refreshed")

    def toggle_hidden(self, enabled: bool) -> None:
        if not self._confirm_editor_transition():
            self.show_hidden.blockSignals(True)
            self.show_hidden.setChecked(not enabled)
            self.show_hidden.blockSignals(False)
            return
        try:
            self.model.set_show_hidden(enabled)
        except (OSError, ValueError) as error:
            self.model.show_hidden = not enabled
            self.show_hidden.blockSignals(True)
            self.show_hidden.setChecked(not enabled)
            self.show_hidden.blockSignals(False)
            self._show_operation_error("Cannot update hidden files", error)
            return
        if self.search_view_active:
            self.start_search()
        else:
            self._expand_root()

    def update_search_button(self) -> None:
        if self.operation_is_running:
            self.search_button.setEnabled(False)
            return
        if self.search_is_running:
            self.search_button.setEnabled(True)
            return
        can_search = self.model.root_path is not None and self.has_search_request()
        self.search_button.setEnabled(can_search)
        self.clear_search_button.setEnabled(
            self.search_view_active or bool(self.search_input.text())
        )

    def has_search_request(self):
        return bool(self.search_input.text().strip()) or self.search_options.active

    def edit_search_options(self):
        dialog = SearchOptionsDialog(self.search_options, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.search_options = dialog.options
            self.search_filters_button.setText(
                "Filters ●" if self.search_options.active else "Filters…"
            )
            self.update_search_button()
            self.live_search.changed()
        dialog.deleteLater()

    def start_search(self) -> None:
        if self.operation_is_running:
            return
        if self.search_is_running:
            self.live_search.stop()
            self.cancel_search()
            return

        root = self.model.root_path
        query = self.search_input.text().strip()
        if root is None or not self.has_search_request():
            return
        try:
            NameMatcher(
                query, self.search_options.name_mode, self.search_options.case_sensitive
            )
        except SearchPatternError as error:
            self.status_changed.emit(str(error))
            return
        if self.search_options.scope == "filesystem":
            root = Path(root.anchor)
        if not self._confirm_editor_transition():
            return

        self._show_path(None)
        self.search_results.clear()
        self.search_view_active = True
        self.search_outcome = None
        self.search_error = ""
        self._set_tree_model(self.search_results)
        self._configure_search_columns()
        self.tree.expand(self.search_results.index(0, 0))
        self.tree.expand(self.search_results.index(1, 0))

        worker = SearchWorker(
            root, query, self.show_hidden.isChecked(), self.search_options
        )
        worker.batch_found.connect(self.receive_search_batch)
        worker.progress.connect(self.show_search_progress)
        worker.failed.connect(self.show_search_error)
        worker.finished.connect(self.finish_search)
        thread = prepare_worker_thread(
            self, worker, on_finished=self.search_thread_finished
        )

        self.search_thread = thread
        self.search_worker = worker
        self.live_search.started(thread)
        self._set_search_running(True)
        self.status_changed.emit(f'Searching for "{query}"...')
        thread.start()

    def receive_search_batch(self, batch: list) -> None:
        worker = self.search_worker
        try:
            if self.search_view_active and self.live_search.accepts:
                self.search_results.append_results(batch)
        except (OSError, sqlite3.Error) as error:
            if worker is not None:
                worker.cancel()
            self.status_changed.emit(f"Cannot store search results: {error}")
        finally:
            if worker is not None:
                worker.batch_processed()

    def cancel_search(self) -> None:
        if self.search_worker is None or not self.search_is_running:
            return
        self.search_worker.cancel()
        self.search_button.setEnabled(False)
        self.status_changed.emit("Cancelling search...")

    def clear_search(self) -> None:
        if not self._confirm_editor_transition():
            return
        self.live_search.stop()
        self.cancel_search()
        self._show_path(None)
        self.search_view_active = False
        self.search_input.clear()
        self.search_results.clear()
        self._set_tree_model(self.model)
        self._configure_tree_columns()
        self._expand_root()
        if self.model.root_path is not None:
            self.status_changed.emit(f"Root: {self.model.root_path}")

    def show_search_progress(self, scanned: int, matched: int) -> None:
        self.task_progress.emit(f"{scanned:,} checked, {matched:,} found", scanned, 0)
        if self.search_view_active and self.live_search.accepts:
            self.status_changed.emit(
                f"Searching: {scanned:,} checked, {matched:,} found"
            )

    def show_search_error(self, message: str) -> None:
        if self.search_view_active and self.live_search.accepts:
            self.search_error = message
            self.status_changed.emit(f"Search error: {message}")

    def finish_search(
        self,
        scanned: int,
        matched: int,
        skipped: int,
        cancelled: bool,
    ) -> None:
        self.search_outcome = (scanned, matched, skipped, cancelled)

    def search_thread_finished(self) -> None:
        thread = self.sender()
        if thread is not self.search_thread:
            return

        self.search_thread = None
        self.search_worker = None
        self._set_search_running(False)

        if (
            self.search_view_active
            and self.search_outcome is not None
            and self.live_search.accepts
        ):
            scanned, matched, skipped, cancelled = self.search_outcome
            if self.search_error:
                message = f"Search stopped: {self.search_error}; {matched:,} partial result(s)"
            elif cancelled:
                message = f"Search cancelled: {matched:,} found"
            else:
                message = f"Search complete: {matched:,} found in {scanned:,} items"
            if skipped:
                message += f", {skipped:,} locations skipped"
            self.status_changed.emit(message)

        if self.close_requested:
            self._emit_ready_to_close()

    def preview_selection_changed(
        self,
        current: QModelIndex,
        previous: QModelIndex,
    ) -> None:
        if self.selection_guard:
            return

        path = self._path_from_index(current)
        if path == self.current_preview_path:
            return
        if not self._confirm_editor_transition():
            self._restore_preview_selection()
            return

        self.preview_index = QPersistentModelIndex(current)
        self._show_path(path)

    def _show_path(self, path: Path | None) -> None:
        self.current_preview_path = path
        self.current_preview_result = None
        self.pending_preview_path = None
        if self.preview_worker is not None:
            self.preview_worker.cancel()

        if path is None:
            self.preview_index = QPersistentModelIndex()
            self._clear_details()
            self._set_preview_text("")
            self.preview.setReadOnly(True)
            self.preview_status.setText("Select a file to preview it")
            self.save_file_button.setEnabled(False)
            self.reload_file_button.setEnabled(False)
            self.open_external_button.setEnabled(False)
            return

        self._update_details(path)
        self.open_external_button.setEnabled(path.exists() or path.is_symlink())
        if path.is_dir():
            self._set_preview_text("")
            self.preview.setReadOnly(True)
            self.preview_status.setText("Folder selected")
            self.save_file_button.setEnabled(False)
            self.reload_file_button.setEnabled(False)
            return

        self._request_preview(path)

    def _request_preview(self, path: Path) -> None:
        self._set_preview_text("")
        self.preview.setReadOnly(True)
        self.preview_status.setText("Loading preview...")
        self.save_file_button.setEnabled(False)
        self.reload_file_button.setEnabled(False)

        if self.preview_is_running:
            self.pending_preview_path = path
            if self.preview_worker is not None:
                self.preview_worker.cancel()
            return
        self._start_preview_worker(path)

    def _start_preview_worker(self, path: Path) -> None:
        worker = FilePreviewWorker(
            path,
            max_edit_bytes=self.preferences.edit_mib * 1024 * 1024,
            preview_bytes=self.preferences.preview_kib * 1024,
        )
        worker.finished.connect(self.receive_preview)
        thread = prepare_worker_thread(
            self, worker, on_finished=self.preview_thread_finished
        )

        self.preview_thread = thread
        self.preview_worker = worker
        self.preview_outcome = None
        thread.start()

    def receive_preview(self, result: PreviewResult) -> None:
        self.preview_outcome = result

    def preview_thread_finished(self) -> None:
        thread = self.sender()
        if thread is not self.preview_thread:
            return

        result = self.preview_outcome
        pending = self.pending_preview_path
        self.preview_thread = None
        self.preview_worker = None
        self.preview_outcome = None
        self.pending_preview_path = None

        if pending is not None and pending == self.current_preview_path:
            self._start_preview_worker(pending)
        elif (
            result is not None
            and not result.cancelled
            and result.path == self.current_preview_path
        ):
            self._display_preview(result)

        self.update_navigation_buttons()
        if self.close_requested:
            self._emit_ready_to_close()
        elif self.pending_folder is not None:
            target, self.pending_folder = self.pending_folder, None
            self.set_root(target)

    def _display_preview(self, result: PreviewResult) -> None:
        self.image_preview.hide()
        self.image_preview.clear()
        self.preview.show()
        self.current_preview_result = result
        self.save_file_button.setEnabled(False)
        self.reload_file_button.setEnabled(True)
        self.open_external_button.setEnabled(True)

        if result.error:
            self._set_preview_text("")
            self.preview.setReadOnly(True)
            self.preview_status.setText(f"Cannot preview file: {result.error}")
            return
        if result.image is not None:
            self._set_preview_text("")
            self.preview.hide()
            self.image_preview.set_image(result.image)
            self.image_preview.show()
            self.preview_status.setText(
                f"Read-only image · {result.image.width()} × {result.image.height()} preview"
            )
            return
        if result.binary:
            self._set_preview_text(result.text)
            self.preview.setReadOnly(True)
            self.preview_status.setText(
                "Read-only binary preview · first 4 KB in hexadecimal"
            )
            return

        self._set_preview_text(result.text)
        self.preview.setReadOnly(not result.editable)
        if result.truncated:
            self.preview_status.setText(
                f"Large file - showing the first 1 MB of {self._format_size(result.size)}"
            )
        elif result.is_link:
            self.preview_status.setText(
                f"Symbolic link - read-only text - {result.encoding}"
            )
        else:
            self.preview_status.setText(f"Editable text - {result.encoding}")
        self.editor_text_changed()

    def editor_text_changed(self) -> None:
        if self.preview_loading:
            return
        result = self.current_preview_result
        editable = bool(result and result.editable)
        self.save_file_button.setEnabled(
            editable and self.preview.document().isModified()
        )

    def save_current_file(self) -> bool:
        result = self.current_preview_result
        path = self.current_preview_path
        if result is None or path is None or not result.editable or not result.encoding:
            return False
        if path.is_symlink():
            QMessageBox.warning(
                self,
                "Cannot save file",
                "Symbolic links are read-only in the built-in editor.",
            )
            return False

        try:
            current = path.stat(follow_symlinks=False)
        except OSError as error:
            self._show_operation_error("Cannot save file", error)
            return False

        expected_signature = file_signature(path)
        if (
            current.st_size != result.size
            or current.st_mtime_ns != result.modified_ns
            or (result.signature is not None and expected_signature != result.signature)
        ):
            answer = QMessageBox.warning(
                self,
                "File changed",
                "The file changed outside the editor. Overwrite it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False

        try:
            size, modified_ns = save_text_atomic(
                path,
                self.preview.toPlainText(),
                encoding=result.encoding,
                newline=result.newline,
                expected_signature=expected_signature,
            )
        except (OSError, UnicodeError, ValueError) as error:
            self._show_operation_error("Cannot save file", error)
            return False

        self.current_preview_result = replace(
            result,
            text=self.preview.toPlainText(),
            size=size,
            modified_ns=modified_ns,
            signature=file_signature(path),
        )
        self.preview.document().setModified(False)
        self.save_file_button.setEnabled(False)
        self.preview_status.setText(f"Saved - {result.encoding}")
        self._update_details(path)
        self.status_changed.emit(f"Saved {path}")
        return True

    def reload_current_file(self) -> None:
        path = self.current_preview_path
        if path is None or path.is_dir():
            return
        if not self._confirm_editor_transition():
            return
        self.current_preview_result = None
        self._request_preview(path)

    def open_current_external(self) -> None:
        path = self.current_preview_path
        if path is None:
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            QMessageBox.warning(self, "Cannot open item", str(path))

    def _confirm_editor_transition(self) -> bool:
        if not self.preview.document().isModified():
            return True

        answer = QMessageBox.warning(
            self,
            "Unsaved changes",
            "Save changes before continuing?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save_current_file()
        if answer == QMessageBox.StandardButton.Discard:
            self.preview.document().setModified(False)
            self.save_file_button.setEnabled(False)
            return True
        return False

    def _restore_preview_selection(self) -> None:
        if not self.preview_index.isValid():
            return
        selection = self.tree.selectionModel()
        if selection is None:
            return
        self.selection_guard = True
        selection.setCurrentIndex(
            QModelIndex(self.preview_index),
            QItemSelectionModel.SelectionFlag.ClearAndSelect
            | QItemSelectionModel.SelectionFlag.Rows,
        )
        self.selection_guard = False

    def _path_from_index(self, index: QModelIndex) -> Path | None:
        current_model = index.model()
        path_from_index = getattr(current_model, "path_from_index", None)
        if not index.isValid() or not callable(path_from_index):
            return None
        return path_from_index(index)

    def _update_details(self, path: Path) -> None:
        try:
            metadata = inspect_file_metadata(path)
        except OSError:
            self._clear_details()
            return
        self.current_metadata = metadata
        self.metadata_button.setEnabled(True)
        values = {
            "name": metadata.name,
            "path": metadata.path,
            "type": metadata.kind,
            "size": "—"
            if metadata.kind == "Folder"
            else self._format_size(metadata.size),
            "modified": format_timestamp(metadata.modified_ns),
            "permissions": metadata.permissions,
            "link target": metadata.link_target or "—",
        }
        for name, value in values.items():
            self.detail_values[name].setText(value)

    def show_file_metadata(self) -> None:
        if self.current_metadata is None:
            return
        try:
            metadata = inspect_file_metadata(self.current_metadata.path)
        except OSError as error:
            self._show_operation_error("Cannot inspect metadata", error)
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("File Metadata")
        dialog.resize(900, 650)
        layout = QVBoxLayout(dialog)
        values = metadata.display_values()
        table = QTableWidget(len(values), 2)
        table.setHorizontalHeaderLabels(("Property", "Value"))
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setColumnWidth(0, 250)
        table.horizontalHeader().setStretchLastSection(True)
        for row, (key, value) in enumerate(values.items()):
            table.setItem(row, 0, QTableWidgetItem(key))
            table.setItem(row, 1, QTableWidgetItem(value))
        layout.addWidget(table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        copy = buttons.addButton("Copy JSON", QDialogButtonBox.ButtonRole.ActionRole)
        copy.clicked.connect(
            lambda: QApplication.clipboard().setText(
                json.dumps(metadata.as_dict(), ensure_ascii=False, indent=2)
            )
        )
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()
        dialog.deleteLater()

    def _clear_details(self) -> None:
        self.current_metadata = None
        self.metadata_button.setEnabled(False)
        for value in self.detail_values.values():
            value.setText("-")

    def _set_preview_text(self, text: str) -> None:
        self.image_preview.hide()
        self.image_preview.clear()
        self.preview.show()
        self.preview_loading = True
        self.preview.setPlainText(text)
        self.preview.document().setModified(False)
        self.preview_loading = False

    @staticmethod
    def _format_size(size: int) -> str:
        value = float(size)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if value < 1_024 or unit == "TB":
                return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
            value /= 1_024
        return "-"

    def selected_paths(self) -> list[Path]:
        selection = self.tree.selectionModel()
        current_model = self.tree.model()
        path_from_index = getattr(current_model, "path_from_index", None)
        if selection is None or not callable(path_from_index):
            return []

        paths = []
        seen = set()
        for index in selection.selectedRows(0):
            path = path_from_index(index)
            if path is None:
                continue
            key = str(path.absolute())
            if key not in seen:
                paths.append(path)
                seen.add(key)
        return paths

    def update_action_buttons(self, *args: object) -> None:
        self.update_navigation_buttons()
        blocked = self.search_is_running or self.operation_is_running
        self.compare_files_button.setEnabled(not blocked)
        self.name_input.setEnabled(not blocked and self.model.root_path is not None)
        self.jump_button.setEnabled(not blocked and self.model.root_path is not None)
        has_root = self.model.root_path is not None
        selected = self._operable_selected_paths()

        self.new_file_button.setEnabled(has_root and not blocked)
        self.new_folder_button.setEnabled(has_root and not blocked)
        self.rename_button.setEnabled(len(selected) == 1 and not blocked)
        self.copy_button.setEnabled(bool(selected) and not blocked)
        self.move_button.setEnabled(bool(selected) and not blocked)
        self.delete_button.setEnabled(bool(selected) and not blocked)
        self.undo_button.setEnabled(bool(self.undo_stack) and not blocked)
        self.transfer_policy.setEnabled(not blocked)
        if hasattr(self, "file_commands"):
            self.file_commands.update_actions()

    def compare_selected_files(self) -> None:
        if not self._confirm_editor_transition():
            return
        paths = [path for path in self.selected_paths() if path.is_file()]
        while len(paths) < 2:
            path, _ = QFileDialog.getOpenFileName(self, "Select File to Compare")
            if not path:
                return
            paths.append(Path(path))
        dialog = FileComparisonDialog(paths[0], paths[1], self)
        dialog.exec()
        dialog.deleteLater()

    def create_new_file(self) -> None:
        self._create_item(directory=False)

    def create_new_folder(self) -> None:
        self._create_item(directory=True)

    def _create_item(self, *, directory: bool) -> None:
        if self.search_is_running or self.operation_is_running:
            return
        if not self._confirm_editor_transition():
            return
        parent = self._selected_directory()
        if parent is None:
            return

        label = "folder" if directory else "file"
        name, accepted = QInputDialog.getText(
            self,
            f"New {label.title()}",
            f"{label.title()} name:",
        )
        if not accepted:
            return

        try:
            record = create_item(parent, name, directory=directory)
        except (OSError, ValueError) as error:
            self._show_operation_error(f"Cannot create {label}", error)
            return

        self.undo_stack.append(record)
        self._refresh_after_operation()
        self.status_changed.emit(f"Created {record.changes[0].target}")

    def rename_selected(self) -> None:
        selected = self._operable_selected_paths()
        if len(selected) != 1 or self.operation_is_running:
            return
        if not self._confirm_editor_transition():
            return
        source = selected[0]
        new_name, accepted = QInputDialog.getText(
            self,
            "Rename",
            "New name:",
            text=source.name,
        )
        if not accepted:
            return

        try:
            record = rename_item(source, new_name)
        except (OSError, ValueError) as error:
            self._show_operation_error("Cannot rename item", error)
            return

        self.undo_stack.append(record)
        self._refresh_after_operation()
        self.status_changed.emit(f"Renamed to {record.changes[0].target.name}")

    def transfer_selected(self, action: str) -> None:
        sources = self._operable_selected_paths()
        if not sources or self.search_is_running or self.operation_is_running:
            return
        if not self._confirm_editor_transition():
            return

        initial = self._selected_directory() or self.model.root_path or Path.home()
        selected = QFileDialog.getExistingDirectory(
            self,
            f"Choose {action} destination",
            str(initial),
            QFileDialog.Option.ShowDirsOnly,
        )
        if not selected:
            return
        self._start_file_operation(
            action,
            sources=sources,
            destination=Path(selected),
        )

    def delete_selected(self) -> None:
        sources = self._operable_selected_paths()
        if not sources or self.search_is_running or self.operation_is_running:
            return
        if not self._confirm_editor_transition():
            return

        if len(sources) == 1:
            question = f'Move "{sources[0].name}" to the Recycle Bin?'
        else:
            question = f"Move {len(sources)} selected items to the Recycle Bin?"
        answer = QMessageBox.question(
            self,
            "Delete",
            question,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._start_file_operation("delete", sources=sources)

    def undo_last_operation(self) -> None:
        if not self.undo_stack or self.search_is_running or self.operation_is_running:
            return
        if not self._confirm_editor_transition():
            return
        record = self.undo_stack[-1]
        self._start_file_operation("undo", record=record)

    def _start_file_operation(
        self,
        action: str,
        *,
        sources: list[Path] | None = None,
        destination: Path | None = None,
        record: OperationRecord | None = None,
    ) -> None:
        worker = FileOperationWorker(
            action,
            sources=sources,
            destination=destination,
            record=record,
            conflict_policy=self.transfer_policy.currentData(),
        )
        self.operation_results.reset_operation(
            action, len(sources or []) if record is None else len(record.changes)
        )
        self.operation_results_button.setEnabled(True)
        worker.items_ready.connect(self.receive_operation_items)
        worker.progress.connect(self.show_operation_progress)
        worker.finished.connect(self.finish_file_operation)
        thread = prepare_worker_thread(
            self, worker, on_finished=self.operation_thread_finished
        )

        self.operation_thread = thread
        self.operation_worker = worker
        self.operation_record = record
        self.operation_outcome = None
        self._set_operation_running(True)
        self.status_changed.emit(f"Starting {action}...")
        thread.start()

    def show_operation_progress(
        self,
        action: str,
        position: int,
        total: int,
        name: str,
    ) -> None:
        self.operation_results.total = total
        self.task_progress.emit(f"{action.title()}: {name}", position - 1, total)
        self.status_changed.emit(f"{action.title()}: {position}/{total} - {name}")

    def show_operation_results(self):
        dialog = OperationResultsDialog(self)
        dialog.exec()
        dialog.deleteLater()

    def receive_operation_items(self, items):
        self.operation_results.append_items(items)
        self.task_progress.emit(
            self.operation_results.action,
            len(self.operation_results.items),
            self.operation_results.total,
        )

    def finish_file_operation(
        self,
        action: str,
        changes: list[FileChange],
        errors: list[str],
    ) -> None:
        self.operation_outcome = (action, list(changes), list(errors))

    def operation_thread_finished(self) -> None:
        thread = self.sender()
        if thread is not self.operation_thread:
            return

        self.operation_thread = None
        self.operation_worker = None
        self.operation_results.finish()
        self._set_operation_running(False)

        if self.operation_outcome is not None:
            action, changes, errors = self.operation_outcome
            self._apply_operation_outcome(action, changes, errors)

        self.operation_record = None
        self.operation_outcome = None
        if self.close_requested:
            self._emit_ready_to_close()

    def _apply_operation_outcome(
        self,
        action: str,
        changes: list[FileChange],
        errors: list[str],
    ) -> None:
        if action in {"copy", "move"} and changes:
            self.undo_stack.append(OperationRecord(action, changes))
        elif action == "delete" and changes:
            self.undo_stack.clear()
        elif action == "undo" and self.operation_record is not None:
            successful = set(changes)
            self.operation_record.changes = [
                change
                for change in self.operation_record.changes
                if change not in successful
            ]
            if not self.operation_record.changes and self.undo_stack:
                self.undo_stack.pop()

        if changes:
            self._refresh_after_operation()
        self.transfer_completed.emit(action, changes)

        if errors and not self.close_requested:
            show_error(
                self,
                "File operation incomplete",
                f"{len(changes):,} completed; {len(errors):,} failed or cancelled. Completed copies and moves are available in Undo.",
                "\n".join(errors[:1000])
                + (
                    "\nFurther details are available in Last Operation."
                    if len(errors) > 1000
                    else ""
                ),
            )

        if self.operation_results.items:
            self.status_changed.emit(self.operation_results.summary())
        else:
            self.status_changed.emit(
                f"{action.title()} stopped: {len(changes):,} completed, {len(errors):,} error(s)"
            )
        self.update_action_buttons()

    def _refresh_after_operation(self) -> None:
        self.search_view_active = False
        self.search_input.clear()
        self.search_results.clear()
        self._show_path(None)
        try:
            self.model.refresh()
        except (OSError, ValueError) as error:
            self.status_changed.emit(f"Cannot refresh root: {error}")
            return
        self._set_tree_model(self.model)
        self._configure_tree_columns()
        self._expand_root()

    def _selected_directory(self) -> Path | None:
        selected = self.selected_paths()
        if len(selected) == 1:
            path = selected[0]
            if path.is_dir() and not path.is_symlink():
                return path
            return path.parent
        return self.model.root_path

    def _operable_selected_paths(self) -> list[Path]:
        root = self.model.root_path
        return [path for path in self.selected_paths() if root is None or path != root]

    def _set_tree_model(self, model: FileTreeModel | SearchResultsModel) -> None:
        actual = self.browse_model if model is self.model else model
        self.tree.setModel(actual)
        self.folder_panel.set_model(actual, self.tree.selectionModel())
        self.folder_panel.setVisible(model is self.model)
        self.folder_view.setColumnWidth(0, 260)
        self.folder_view.setColumnWidth(1, 110)
        selection = self.tree.selectionModel()
        if selection is not None:
            selection.selectionChanged.connect(self.update_action_buttons)
            selection.currentChanged.connect(self.preview_selection_changed)
        self.update_action_buttons()

    def _show_operation_error(self, title: str, error: Exception) -> None:
        QMessageBox.critical(self, title, str(error))
        self.status_changed.emit(str(error))

    def load_expanded_folder(self, index: QModelIndex) -> None:
        if self.tree.model() is self.browse_model:
            source = self.browse_model.mapToSource(index)
            if self.model.canFetchMore(source):
                self.model.fetchMore(source)

    def release_collapsed_folder(self, index: QModelIndex) -> None:
        if self.tree.model() is self.browse_model:
            self.model.release_children(self.browse_model.mapToSource(index))

    def prepare_close(self) -> bool:
        self.close_cancelled = False
        if not self._confirm_editor_transition():
            self.close_cancelled = True
            return False
        self.pending_folder = None
        self.live_search.stop()
        self.file_commands.stop()
        self.file_revealer.stop()
        waiting = not self.path_completion.stop()
        waiting = not self.name_completion.stop() or waiting
        waiting = not self.quick_access.stop() or waiting
        self.close_requested = True
        if self.search_is_running:
            self.cancel_search()
            waiting = True
        if self.operation_is_running:
            if self.operation_worker is not None:
                self.operation_worker.cancel()
            waiting = True
        if self.preview_is_running:
            self.pending_preview_path = None
            if self.preview_worker is not None:
                self.preview_worker.cancel()
            waiting = True
        return not waiting

    def cancel_close_request(self) -> None:
        self.close_requested = False
        self.path_completion.closed = False
        self.name_completion.closed = False
        self.quick_access.closed = False

    def finalize_close(self) -> None:
        self.file_commands.dispose()
        self.model.close()
        self.search_results.close()

    def _set_search_running(self, running: bool) -> None:
        self.select_root_button.setEnabled(not running)
        self.refresh_button.setEnabled(not running and self.model.root_path is not None)
        self.show_hidden.setEnabled(not running)
        self.search_input.setEnabled(
            self.model.root_path is not None and not self.operation_is_running
        )
        self.search_button.setText("Cancel" if running else "Search")
        self.clear_search_button.setEnabled(running or self.search_view_active)
        if not running:
            self.update_search_button()
        self.update_action_buttons()

    def _set_operation_running(self, running: bool) -> None:
        self.tree.setEnabled(not running)
        self.folder_panel.setEnabled(not running)
        self.select_root_button.setEnabled(not running)
        self.refresh_button.setEnabled(not running and self.model.root_path is not None)
        self.show_hidden.setEnabled(not running)
        self.search_input.setEnabled(not running and self.model.root_path is not None)
        self.search_button.setEnabled(not running)
        self.clear_search_button.setEnabled(not running and self.search_view_active)
        if not running:
            self.update_search_button()
        self.update_action_buttons()

    def _emit_ready_to_close(self) -> None:
        if (
            not self.search_is_running
            and not self.operation_is_running
            and not self.preview_is_running
            and self.path_completion.thread is None
            and self.name_completion.thread is None
            and self.quick_access.thread is None
        ):
            self.ready_to_close.emit()

    def _configure_tree_columns(self) -> None:
        self.tree.header().resizeSection(0, 360)
        self.tree.header().resizeSection(1, 120)
        self.tree.header().resizeSection(2, 100)
        self.tree.header().resizeSection(3, 160)

    def _configure_search_columns(self) -> None:
        self.tree.header().resizeSection(0, 300)
        self.tree.header().resizeSection(1, 520)
        self.tree.header().resizeSection(2, 100)

    def _expand_root(self) -> None:
        root_index = self.model.index(0, 0)
        if not root_index.isValid():
            return
        if self.model.canFetchMore(root_index):
            self.model.fetchMore(root_index)
        if self.tree.model() is self.browse_model:
            visible = self.browse_model.mapFromSource(root_index)
            self.tree.expand(visible)
            self.folder_panel.set_root(visible)


class VirtualExplorerPage(ExplorerPage):
    staged_workspace = Signal(object)
    status_changed = Signal(str)
    task_failed = Signal(str)
    task_progress = Signal(str, int, int)
    ready_to_close = Signal()
    close_aborted = Signal()

    def __init__(self) -> None:
        super().__init__(virtual=True)

        self.model = VirtualTreeModel(self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.proxy.setFilterKeyColumn(0)
        self.proxy.setRecursiveFilteringEnabled(True)
        self.tree.setModel(self.proxy)

        self.undo_stack: list[VirtualUndoRecord] = []
        self.current_node: VirtualNode | None = None
        self.preview_loading = False
        self.selection_guard = False
        self.workspace_dirty = False
        self.workspace_path: Path | None = None
        self.task_thread: QThread | None = None
        self.task_worker: VirtualTaskWorker | None = None
        self.task_outcome: tuple[str, object, str] | None = None
        self.task_path: Path | None = None
        self.close_requested = False
        self.close_cancelled = False

        for button in self.location_buttons:
            button.setEnabled(True)
        self.search_input.setEnabled(True)
        self.open_external_button.setVisible(False)
        self._configure_columns()
        self._connect_selection()
        self._expand_root()
        self.update_search_buttons()
        self.update_action_buttons()

        self.save_button.clicked.connect(self.save_workspace_dialog)
        self.load_button.clicked.connect(self.load_workspace_dialog)
        self.export_button.clicked.connect(self.export_workspace_dialog)
        self.reset_button.clicked.connect(self.reset_workspace)
        self.search_input.textChanged.connect(self.update_search_buttons)
        self.search_input.returnPressed.connect(self.start_search)
        self.search_button.clicked.connect(self.start_search)
        self.clear_search_button.clicked.connect(self.clear_search)
        self.new_file_button.clicked.connect(self.create_new_file)
        self.new_folder_button.clicked.connect(self.create_new_folder)
        self.rename_button.clicked.connect(self.rename_selected)
        self.copy_button.clicked.connect(lambda: self.transfer_selected("copy"))
        self.move_button.clicked.connect(lambda: self.transfer_selected("move"))
        self.delete_button.clicked.connect(self.delete_selected)
        self.undo_button.clicked.connect(self.undo_last_operation)
        self.save_file_button.clicked.connect(self.save_current_file)
        self.reload_file_button.clicked.connect(self.reload_current_file)
        self.preview.textChanged.connect(self.editor_text_changed)

    @property
    def task_is_running(self) -> bool:
        return self.task_thread is not None

    def _connect_selection(self) -> None:
        selection = self.tree.selectionModel()
        if selection is None:
            return
        selection.selectionChanged.connect(self.update_action_buttons)
        selection.currentChanged.connect(self.preview_selection_changed)

    def preview_selection_changed(
        self,
        current: QModelIndex,
        previous: QModelIndex,
    ) -> None:
        if self.selection_guard:
            return
        node = self.node_from_view_index(current)
        if node is self.current_node:
            return
        if not self._confirm_editor_transition():
            self._select_node(self.current_node)
            return
        self._show_node(node)

    def _show_node(self, node: VirtualNode | None) -> None:
        self.current_node = node
        if node is None:
            self._clear_details()
            self._set_preview_text("")
            self.preview.setReadOnly(True)
            self.preview_status.setText("Select a virtual file to edit it")
            self.save_file_button.setEnabled(False)
            self.reload_file_button.setEnabled(False)
            return

        values = {
            "name": node.name,
            "path": self.model.path_for(node),
            "type": file_type(node),
            "size": "-" if node.is_directory else format_size(node_size(node)),
            "modified": time.strftime(
                "%Y-%m-%d %H:%M:%S",
                time.localtime(node.modified),
            ),
        }
        for name, value in values.items():
            self.detail_values[name].setText(value)

        if node.is_directory:
            self._set_preview_text("")
            self.preview.setReadOnly(True)
            self.preview_status.setText("Virtual folder selected")
            self.save_file_button.setEnabled(False)
            self.reload_file_button.setEnabled(False)
            return

        self._set_preview_text(node.content)
        self.preview.setReadOnly(False)
        self.preview_status.setText("Editable virtual text file - UTF-8")
        self.save_file_button.setEnabled(False)
        self.reload_file_button.setEnabled(True)

    def editor_text_changed(self) -> None:
        if self.preview_loading:
            return
        self.save_file_button.setEnabled(
            bool(
                self.current_node
                and not self.current_node.is_directory
                and self.preview.document().isModified()
            )
        )

    def save_current_file(self) -> bool:
        node = self.current_node
        if node is None or node.is_directory:
            return False
        content = self.preview.toPlainText()
        if content == node.content:
            self.preview.document().setModified(False)
            self.save_file_button.setEnabled(False)
            return True

        self.selection_guard = True
        record = self.model.update_content(node, content)
        self.selection_guard = False
        self.undo_stack.append(record)
        self._set_workspace_dirty(True)
        self._set_preview_text(content)
        self._select_node(node)
        self._show_node(node)
        self.preview_status.setText("Saved to virtual workspace - UTF-8")
        self.status_changed.emit(f"Updated {self.model.path_for(node)}")
        self.update_action_buttons()
        return True

    def reload_current_file(self) -> None:
        if self.current_node is None or self.current_node.is_directory:
            return
        if not self._confirm_editor_transition():
            return
        self._show_node(self.current_node)

    def _confirm_editor_transition(self) -> bool:
        if not self.preview.document().isModified():
            return True
        answer = QMessageBox.warning(
            self,
            "Unsaved virtual file",
            "Save this virtual file before continuing?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save_current_file()
        if answer == QMessageBox.StandardButton.Discard:
            self.preview.document().setModified(False)
            self.save_file_button.setEnabled(False)
            return True
        return False

    def create_new_file(self) -> None:
        self._create_item(directory=False)

    def create_new_folder(self) -> None:
        self._create_item(directory=True)

    def _create_item(self, *, directory: bool) -> None:
        if self.task_is_running or not self._confirm_editor_transition():
            return
        parent = self._selected_directory()
        label = "folder" if directory else "file"
        name, accepted = QInputDialog.getText(
            self,
            f"New Virtual {label.title()}",
            f"{label.title()} name:",
        )
        if not accepted:
            return
        try:
            record = self._run_model_change(
                self.model.create_item,
                parent,
                name,
                directory=directory,
            )
        except (OSError, ValueError) as error:
            self._show_virtual_error(f"Cannot create virtual {label}", error)
            return
        self._finish_model_change(record, f"Created {name}")

    def rename_selected(self) -> None:
        selected = self._operable_selected_nodes()
        if len(selected) != 1 or not self._confirm_editor_transition():
            return
        node = selected[0]
        name, accepted = QInputDialog.getText(
            self,
            "Rename Virtual Item",
            "New name:",
            text=node.name,
        )
        if not accepted:
            return
        try:
            record = self._run_model_change(self.model.rename_item, node, name)
        except (OSError, ValueError) as error:
            self._show_virtual_error("Cannot rename virtual item", error)
            return
        self._finish_model_change(record, f"Renamed to {name.strip()}")

    def transfer_selected(self, action: str) -> None:
        selected = self._operable_selected_nodes()
        if not selected or self.task_is_running:
            return
        if not self._confirm_editor_transition():
            return
        destination = self._ask_destination(action)
        if destination is None:
            return
        operation = self.model.copy_items if action == "copy" else self.model.move_items
        try:
            record = self._run_model_change(operation, selected, destination)
        except (OSError, ValueError) as error:
            self._show_virtual_error(f"Cannot {action} virtual item", error)
            return
        self._finish_model_change(
            record,
            f"{action.title()} complete: {len(record.items)} item(s)",
        )

    def _ask_destination(self, action: str) -> VirtualNode | None:
        initial = self.model.path_for(self._selected_directory())
        path, accepted = QInputDialog.getText(
            self,
            f"Virtual {action.title()} Destination",
            "Destination path:",
            text=initial,
        )
        if not accepted:
            return None
        destination = self.model.resolve_path(path)
        if destination is None or not destination.is_directory:
            QMessageBox.warning(self, "Invalid destination", path)
            return None
        return destination

    def delete_selected(self) -> None:
        selected = self._operable_selected_nodes()
        if not selected or not self._confirm_editor_transition():
            return
        answer = QMessageBox.question(
            self,
            "Delete Virtual Items",
            f"Delete {len(selected)} selected virtual item(s)?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        record = self._run_model_change(self.model.delete_items, selected)
        self._finish_model_change(record, f"Deleted {len(record.items)} item(s)")

    def undo_last_operation(self) -> None:
        if not self.undo_stack or not self._confirm_editor_transition():
            return
        record = self.undo_stack.pop()
        self.selection_guard = True
        self.model.undo(record)
        self.selection_guard = False
        self._reset_view_after_change()
        self._set_workspace_dirty(True)
        self.status_changed.emit(f"Undid virtual {record.action}")
        self.update_action_buttons()

    def _run_model_change(self, operation, *args, **kwargs) -> VirtualUndoRecord:
        self.selection_guard = True
        try:
            return operation(*args, **kwargs)
        finally:
            self.selection_guard = False

    def _finish_model_change(
        self,
        record: VirtualUndoRecord,
        message: str,
    ) -> None:
        self.undo_stack.append(record)
        self._reset_view_after_change()
        self._set_workspace_dirty(True)
        self.status_changed.emit(message)
        self.update_action_buttons()

    def _reset_view_after_change(self) -> None:
        self.proxy.setFilterFixedString("")
        self.search_input.clear()
        self._show_node(None)
        self._expand_root()

    def start_search(self) -> None:
        query = self.search_input.text().strip()
        if not query or not self._confirm_editor_transition():
            return
        self._show_node(None)
        self.proxy.setFilterFixedString(query)
        self.tree.expandAll()
        matched = sum(
            1
            for node in self._all_nodes()
            if node is not self.model.root and query.casefold() in node.name.casefold()
        )
        self.status_changed.emit(f'Virtual search: {matched:,} result(s) for "{query}"')
        self.update_search_buttons()

    def clear_search(self) -> None:
        if not self._confirm_editor_transition():
            return
        self.proxy.setFilterFixedString("")
        self.search_input.clear()
        self._show_node(None)
        self._expand_root()
        self.status_changed.emit("Virtual search cleared")
        self.update_search_buttons()

    def update_search_buttons(self, *args: object) -> None:
        query = bool(self.search_input.text().strip())
        self.search_button.setEnabled(query and not self.task_is_running)
        self.clear_search_button.setEnabled(
            not self.task_is_running
            and (query or bool(self.proxy.filterRegularExpression().pattern()))
        )

    def update_action_buttons(self, *args: object) -> None:
        selected = self._operable_selected_nodes()
        blocked = self.task_is_running
        self.new_file_button.setEnabled(not blocked)
        self.new_folder_button.setEnabled(not blocked)
        self.rename_button.setEnabled(len(selected) == 1 and not blocked)
        self.copy_button.setEnabled(bool(selected) and not blocked)
        self.move_button.setEnabled(bool(selected) and not blocked)
        self.delete_button.setEnabled(bool(selected) and not blocked)
        self.undo_button.setEnabled(bool(self.undo_stack) and not blocked)

    def selected_nodes(self) -> list[VirtualNode]:
        selection = self.tree.selectionModel()
        if selection is None:
            return []
        result = []
        seen = set()
        for proxy_index in selection.selectedRows(0):
            node = self.node_from_view_index(proxy_index)
            if node is not None and id(node) not in seen:
                seen.add(id(node))
                result.append(node)
        return result

    def _operable_selected_nodes(self) -> list[VirtualNode]:
        return [node for node in self.selected_nodes() if node is not self.model.root]

    def _selected_directory(self) -> VirtualNode:
        selected = self.selected_nodes()
        if len(selected) == 1:
            node = selected[0]
            if node.is_directory:
                return node
            if node.parent is not None:
                return node.parent
        return self.model.root

    def node_from_view_index(self, index: QModelIndex) -> VirtualNode | None:
        if not index.isValid():
            return None
        return self.model.node_from_index(self.proxy.mapToSource(index))

    def _select_node(self, node: VirtualNode | None) -> None:
        selection = self.tree.selectionModel()
        if selection is None:
            return
        self.selection_guard = True
        if node is None:
            selection.clear()
        else:
            source = self.model.index_for_node(node)
            proxy = self.proxy.mapFromSource(source)
            if proxy.isValid():
                selection.setCurrentIndex(
                    proxy,
                    QItemSelectionModel.SelectionFlag.ClearAndSelect
                    | QItemSelectionModel.SelectionFlag.Rows,
                )
        self.selection_guard = False

    def _all_nodes(self):
        stack = [self.model.root]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(reversed(node.children))

    def save_workspace_dialog(self) -> bool:
        if self.task_is_running or not self._confirm_editor_transition():
            return False
        initial = self.workspace_path or Path.home() / "workspace.ftv.json"
        selected, _ = QFileDialog.getSaveFileName(
            self,
            "Save Virtual Workspace",
            str(initial),
            "Virtual Workspace (*.ftv.json *.json);;JSON Files (*.json)",
        )
        if not selected:
            return False
        self._start_task("save", Path(selected), self.model.root)
        return True

    def load_workspace_dialog(self) -> None:
        if self.task_is_running or not self._confirm_editor_transition():
            return
        if not self._confirm_workspace_replacement():
            return
        initial = self.workspace_path.parent if self.workspace_path else Path.home()
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Load Virtual Workspace",
            str(initial),
            "Virtual Workspace (*.ftv.json *.json);;JSON Files (*.json)",
        )
        if selected:
            self._start_task("load", Path(selected))

    def export_workspace_dialog(self) -> None:
        if self.task_is_running or not self._confirm_editor_transition():
            return
        selected = QFileDialog.getExistingDirectory(
            self,
            "Choose Export Parent Folder",
            str(Path.home()),
            QFileDialog.Option.ShowDirsOnly,
        )
        if selected:
            self._start_task("export", Path(selected), self.model.root)

    def reset_workspace(self) -> None:
        if self.task_is_running or not self._confirm_editor_transition():
            return
        if not self._confirm_workspace_replacement():
            return
        self.selection_guard = True
        self.model.reset_workspace()
        self.selection_guard = False
        self.undo_stack.clear()
        self.workspace_path = None
        self._reset_view_after_change()
        self._set_workspace_dirty(False)
        self.status_changed.emit("Virtual workspace reset")
        self.update_action_buttons()

    def _confirm_workspace_replacement(self) -> bool:
        if not self.workspace_dirty:
            return True
        answer = QMessageBox.warning(
            self,
            "Unsaved workspace",
            "Unsaved virtual workspace changes will be lost. Continue?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Discard

    def _start_task(
        self,
        action: str,
        path: Path,
        data: dict | VirtualNode | None = None,
    ) -> None:
        worker = VirtualTaskWorker(action, path, data)
        worker.finished.connect(self.receive_task_result)
        thread = prepare_worker_thread(
            self, worker, on_finished=self.task_thread_finished
        )
        self.task_thread = thread
        self.task_worker = worker
        self.task_outcome = None
        self.task_path = path
        self._set_task_running(True)
        self.status_changed.emit(f"Virtual {action} started...")
        thread.start()

    def receive_task_result(self, action: str, result: object, error: str) -> None:
        self.task_outcome = (action, result, error)

    def task_thread_finished(self) -> None:
        thread = self.sender()
        if thread is not self.task_thread:
            return
        outcome = self.task_outcome
        task_path = self.task_path
        self.task_thread = None
        self.task_worker = None
        self.task_outcome = None
        self.task_path = None
        self._set_task_running(False)

        failed = False
        if outcome is not None:
            action, result, error = outcome
            if error:
                failed = True
                QMessageBox.warning(self, f"Virtual {action} failed", error)
                self.status_changed.emit(error)
            elif action == "save" and isinstance(result, Path):
                self.workspace_path = result
                self._set_workspace_dirty(False)
                self.status_changed.emit(f"Virtual workspace saved: {result}")
            elif action == "load" and isinstance(result, VirtualNode):
                self.selection_guard = True
                self.model.replace_root(result)
                self.selection_guard = False
                self.undo_stack.clear()
                self.workspace_path = task_path
                self._reset_view_after_change()
                self._set_workspace_dirty(False)
                self.status_changed.emit("Virtual workspace loaded")
            elif action == "stage" and isinstance(result, tuple):
                self.staged_workspace.emit(result)
            elif action == "export" and isinstance(result, Path):
                self.status_changed.emit(f"Virtual workspace exported: {result}")

        if self.close_requested:
            if failed:
                self.close_requested = False
                self.close_aborted.emit()
            else:
                self.ready_to_close.emit()

    def _set_task_running(self, running: bool) -> None:
        self.tree.setEnabled(not running)
        for button in self.location_buttons:
            button.setEnabled(not running)
        self.search_input.setEnabled(not running)
        self.preview.setEnabled(not running)
        self.reload_file_button.setEnabled(
            not running
            and bool(self.current_node and not self.current_node.is_directory)
        )
        self.save_file_button.setEnabled(
            not running and self.preview.document().isModified()
        )
        self.update_search_buttons()
        self.update_action_buttons()

    def _set_workspace_dirty(self, dirty: bool) -> None:
        self.workspace_dirty = dirty
        self.location.setText("VM:/ *" if dirty else "VM:/")

    def _clear_details(self) -> None:
        for value in self.detail_values.values():
            value.setText("-")

    def _set_preview_text(self, text: str) -> None:
        self.preview_loading = True
        self.preview.setPlainText(text)
        self.preview.document().setModified(False)
        self.preview_loading = False

    def _show_virtual_error(self, title: str, error: Exception) -> None:
        QMessageBox.warning(self, title, str(error))
        self.status_changed.emit(str(error))

    def _configure_columns(self) -> None:
        self.tree.header().resizeSection(0, 360)
        self.tree.header().resizeSection(1, 120)
        self.tree.header().resizeSection(2, 100)
        self.tree.header().resizeSection(3, 160)

    def _expand_root(self) -> None:
        self.tree.expand(self.proxy.index(0, 0))

    def prepare_close(self) -> bool:
        self.close_cancelled = False
        if self.task_is_running:
            self.close_requested = True
            return False
        if not self._confirm_editor_transition():
            self.close_cancelled = True
            return False
        if self.workspace_dirty:
            answer = QMessageBox.warning(
                self,
                "Unsaved virtual workspace",
                "Save the virtual workspace before closing?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if answer == QMessageBox.StandardButton.Save:
                if self.save_workspace_dialog():
                    self.close_requested = True
                else:
                    self.close_cancelled = True
                return False
            if answer == QMessageBox.StandardButton.Cancel:
                self.close_cancelled = True
                return False
        self.close_requested = True
        return True

    def cancel_close_request(self) -> None:
        self.close_requested = False


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("File Tree Viewer")
        self.resize(1400, 850)
        self.setMinimumSize(1000, 650)

        self.tabs = QTabWidget()
        self.real_page = RealExplorerPage()
        self.virtual_page = VirtualExplorerPage()
        self.plan_page = PlanExplorerPage()
        self.real_page.status_changed.connect(self.statusBar().showMessage)
        self.real_page.ready_to_close.connect(self.close)
        self.virtual_page.status_changed.connect(self.statusBar().showMessage)
        self.virtual_page.ready_to_close.connect(self.close)
        self.virtual_page.close_aborted.connect(self.cancel_pending_close)
        self.plan_page.status_changed.connect(self.statusBar().showMessage)
        self.plan_page.ready_to_close.connect(self.close)
        self.tabs.addTab(self.real_page, "Real File System")
        self.tabs.addTab(self.virtual_page, "Virtual File System")
        self.tabs.addTab(self.plan_page, "Plan")

        self.setCentralWidget(self.tabs)
        workspace_menu = self.menuBar().addMenu("Workspace")
        self.workspace_actions = QActionGroup(self)
        for index, title in enumerate(
            ("Real File System", "Virtual File System", "Plan")
        ):
            action = QAction(title, self, checkable=True)
            action.setShortcut(f"Ctrl+{index + 1}")
            action.triggered.connect(
                lambda checked=False, i=index: self.tabs.setCurrentIndex(i)
            )
            self.workspace_actions.addAction(action)
            workspace_menu.addAction(action)
        self.workspace_actions.actions()[0].setChecked(True)
        self.tabs.currentChanged.connect(
            lambda i: self.workspace_actions.actions()[i].setChecked(True)
        )
        workspace_menu.addSeparator()
        workspace_menu.addAction("Import Real Root into Plan", self.import_real_root)
        workspace_menu.addAction("Open Snapshot Root in Real", self.open_snapshot_root)
        self.staging = StagingController(self)
        workspace_menu.addAction(
            "Add Virtual Workspace to Plan…", self.staging.request_virtual
        )
        self.real_page.stage_requested.connect(self.staging.request)
        for action in ("copy", "move", "rename", "delete"):
            workspace_menu.addAction(
                f"Stage Real {action.title()}…",
                lambda checked=False, a=action: self.staging.request(a),
            )
        self.tabs.setDocumentMode(True)
        self.settings = application_settings()
        self.preferences = Preferences.load(self.settings)
        self.apply_preferences(self.preferences)
        restore_layout(self, self.settings)
        settings_menu = self.menuBar().addMenu("Settings")
        settings_menu.addAction("Preferences...", self.edit_preferences)
        self.task_monitor = TaskMonitor(self)
        self.statusBar().addPermanentWidget(self.task_monitor)
        for page in (self.real_page, self.virtual_page, self.plan_page):
            page.task_failed.connect(self.show_task_error)
            page.task_progress.connect(
                lambda message, completed, total, p=page: self.task_monitor.advance(
                    p, message, completed, total
                )
            )
        self.statusBar().showMessage("Ready")

    def show_task_error(self, message: str) -> None:
        if self.isEnabled():
            show_error(
                self,
                "Background task failed",
                "The task stopped unexpectedly. Review its result before retrying.",
                message,
            )
        self.statusBar().showMessage("Background task failed: " + message)

    def apply_preferences(self, preferences: Preferences) -> None:
        self.preferences = preferences
        self.real_page.preferences = preferences
        self.real_page.show_hidden.setChecked(preferences.show_hidden)
        self.plan_page.default_conflict_policy = preferences.conflict_policy
        for page in (self.real_page, self.virtual_page):
            font = page.preview.font()
            font.setPointSize(preferences.preview_font_size)
            page.preview.setFont(font)
            page.preview.setLineWrapMode(
                QPlainTextEdit.LineWrapMode.WidgetWidth
                if preferences.wrap_preview
                else QPlainTextEdit.LineWrapMode.NoWrap
            )

    def edit_preferences(self) -> None:
        if self.real_page.operation_is_running or self.real_page.search_is_running:
            self.statusBar().showMessage(
                "Wait for Real operations or search to finish before changing preferences"
            )
            return
        dialog = PreferencesDialog(
            replace(
                self.preferences, show_hidden=self.real_page.show_hidden.isChecked()
            ),
            self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.apply_preferences(dialog.preferences())
            self.save_preferences()
        dialog.deleteLater()

    def save_preferences(self) -> None:
        self.preferences = replace(
            self.preferences, show_hidden=self.real_page.show_hidden.isChecked()
        )
        self.preferences.save(self.settings)
        save_layout(self, self.settings)
        self.settings.sync()
        if self.settings.status() != QSettings.Status.NoError:
            QMessageBox.warning(
                self,
                "Preferences could not be saved",
                "Check permissions and free space for the application settings location.",
            )

    def import_real_root(self) -> None:
        root = self.real_page.model.root_path
        if root is None:
            self.statusBar().showMessage("Select a Real root first")
            return
        if len(self.plan_page.plan) and not self.plan_page._confirm_plan_replacement():
            return
        self.tabs.setCurrentWidget(self.plan_page)
        self.plan_page.start_snapshot(root)

    def open_snapshot_root(self) -> None:
        snapshot = self.plan_page.snapshot
        if snapshot is None:
            self.statusBar().showMessage("Load a snapshot first")
            return
        self.tabs.setCurrentWidget(self.real_page)
        self.real_page.set_root(snapshot.root)

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self.real_page.prepare_close():
            if self.real_page.close_cancelled:
                event.ignore()
                return
            self.setEnabled(False)
            self.statusBar().showMessage("Waiting for file tasks to finish...")
            event.ignore()
            return
        if not self.virtual_page.prepare_close():
            if self.virtual_page.close_cancelled:
                self.real_page.cancel_close_request()
                event.ignore()
                return
            self.setEnabled(False)
            self.statusBar().showMessage("Waiting for virtual workspace task...")
            event.ignore()
            return
        if not self.plan_page.prepare_close():
            if self.plan_page.close_cancelled:
                self.real_page.cancel_close_request()
                self.virtual_page.cancel_close_request()
                event.ignore()
                return
            self.setEnabled(False)
            self.statusBar().showMessage("Waiting for snapshot task...")
            event.ignore()
            return
        self.save_preferences()
        self.real_page.finalize_close()
        self.plan_page.finalize_close()
        event.accept()

    def cancel_pending_close(self) -> None:
        self.real_page.cancel_close_request()
        self.virtual_page.cancel_close_request()
        self.plan_page.cancel_close_request()
        self.setEnabled(True)
        self.statusBar().showMessage("Close cancelled")
