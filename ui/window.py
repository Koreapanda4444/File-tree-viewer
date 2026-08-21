from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QModelIndex, Qt, QThread, Signal
from PySide6.QtGui import QCloseEvent, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from real.search import SearchResultsModel, SearchWorker
from real.tree import FileTreeModel


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
        row.addWidget(QLabel("Workspace" if self.virtual else "Root"))

        self.location = QLineEdit()
        self.location.setReadOnly(True)
        if self.virtual:
            self.location.setText("VM:/")
        else:
            self.location.setPlaceholderText("Select a folder or drive")
        row.addWidget(self.location, 1)

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
        return row

    def _create_content(self) -> QSplitter:
        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.tree = QTreeView()
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tree.setUniformRowHeights(True)

        empty_model = QStandardItemModel(self.tree)
        empty_model.setHorizontalHeaderLabels(("Name", "Type", "Size", "Modified"))
        self.tree.setModel(empty_model)
        self.tree.header().resizeSection(0, 360)
        splitter.addWidget(self.tree)

        details = QWidget()
        details_layout = QVBoxLayout(details)
        details_layout.setContentsMargins(0, 0, 0, 0)

        details_group = QGroupBox("Details")
        details_form = QFormLayout(details_group)
        self.detail_values = {}
        for label in ("Name", "Path", "Type", "Size", "Modified"):
            value = QLabel("-")
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setWordWrap(label == "Path")
            self.detail_values[label.lower()] = value
            details_form.addRow(f"{label}:", value)
        details_layout.addWidget(details_group)

        preview_group = QGroupBox("Preview")
        preview_layout = QVBoxLayout(preview_group)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("Select a file to preview it")
        preview_layout.addWidget(self.preview)
        details_layout.addWidget(preview_group, 1)

        splitter.addWidget(details)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        return splitter

    def _create_action_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        action_names = (
            "New File",
            "New Folder",
            "Rename",
            "Copy",
            "Move",
            "Delete",
            "Undo",
        )

        self.action_buttons = []
        for name in action_names:
            button = QPushButton(name)
            button.setEnabled(False)
            self.action_buttons.append(button)
            row.addWidget(button)

        row.addStretch(1)
        return row


class RealExplorerPage(ExplorerPage):
    status_changed = Signal(str)
    ready_to_close = Signal()

    def __init__(self) -> None:
        super().__init__(virtual=False)

        self.model = FileTreeModel(self)
        self.search_results = SearchResultsModel(self)
        self.search_thread: QThread | None = None
        self.search_worker: SearchWorker | None = None
        self.search_view_active = False
        self.close_requested = False
        self.search_outcome: tuple[int, int, int, bool] | None = None

        self.tree.setModel(self.model)
        self._configure_tree_columns()

        self.select_root_button.setEnabled(True)
        self.show_hidden.setEnabled(True)

        self.select_root_button.clicked.connect(self.choose_root)
        self.refresh_button.clicked.connect(self.refresh_root)
        self.show_hidden.toggled.connect(self.toggle_hidden)
        self.search_input.textChanged.connect(self.update_search_button)
        self.search_input.returnPressed.connect(self.start_search)
        self.search_button.clicked.connect(self.start_search)
        self.clear_search_button.clicked.connect(self.clear_search)
        self.tree.expanded.connect(self.load_expanded_folder)
        self.tree.collapsed.connect(self.release_collapsed_folder)
        self.model.directory_error.connect(self.status_changed)

    @property
    def search_is_running(self) -> bool:
        return bool(self.search_thread and self.search_thread.isRunning())

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

    def set_root(self, path: Path) -> None:
        try:
            self.model.set_root(path)
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, "Cannot open root", str(error))
            return

        self.search_results.clear()
        self.search_view_active = False
        self.tree.setModel(self.model)
        self._configure_tree_columns()
        self.location.setText(str(self.model.root_path))
        self.refresh_button.setEnabled(True)
        self.search_input.setEnabled(True)
        self.clear_search_button.setEnabled(bool(self.search_input.text()))
        self.update_search_button()
        self._expand_root()
        self.status_changed.emit(f"Root: {self.model.root_path}")

    def refresh_root(self) -> None:
        if self.model.root_path is None:
            return
        self.clear_search()
        self.model.refresh()
        self._expand_root()
        self.status_changed.emit("Tree refreshed")

    def toggle_hidden(self, enabled: bool) -> None:
        self.model.set_show_hidden(enabled)
        if self.search_view_active:
            self.start_search()
        else:
            self._expand_root()

    def update_search_button(self) -> None:
        if self.search_is_running:
            self.search_button.setEnabled(True)
            return
        can_search = self.model.root_path is not None and bool(
            self.search_input.text().strip()
        )
        self.search_button.setEnabled(can_search)
        self.clear_search_button.setEnabled(
            self.search_view_active or bool(self.search_input.text())
        )

    def start_search(self) -> None:
        if self.search_is_running:
            self.cancel_search()
            return

        root = self.model.root_path
        query = self.search_input.text().strip()
        if root is None or not query:
            return

        self.search_results.clear()
        self.search_view_active = True
        self.search_outcome = None
        self.tree.setModel(self.search_results)
        self._configure_search_columns()
        self.tree.expand(self.search_results.index(0, 0))
        self.tree.expand(self.search_results.index(1, 0))

        thread = QThread(self)
        worker = SearchWorker(root, query, self.show_hidden.isChecked())
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.batch_found.connect(self.search_results.append_results)
        worker.progress.connect(self.show_search_progress)
        worker.failed.connect(self.show_search_error)
        worker.finished.connect(self.finish_search)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self.search_thread_finished)
        thread.finished.connect(thread.deleteLater)

        self.search_thread = thread
        self.search_worker = worker
        self._set_search_running(True)
        self.status_changed.emit(f'Searching for "{query}"...')
        thread.start()

    def cancel_search(self) -> None:
        if self.search_worker is None or not self.search_is_running:
            return
        self.search_worker.cancel()
        self.search_button.setEnabled(False)
        self.status_changed.emit("Cancelling search...")

    def clear_search(self) -> None:
        self.cancel_search()
        self.search_view_active = False
        self.search_input.clear()
        self.search_results.clear()
        self.tree.setModel(self.model)
        self._configure_tree_columns()
        self._expand_root()
        if self.model.root_path is not None:
            self.status_changed.emit(f"Root: {self.model.root_path}")

    def show_search_progress(self, scanned: int, matched: int) -> None:
        if self.search_view_active:
            self.status_changed.emit(
                f"Searching: {scanned:,} checked, {matched:,} found"
            )

    def show_search_error(self, message: str) -> None:
        if self.search_view_active:
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

        if self.search_view_active and self.search_outcome is not None:
            scanned, matched, skipped, cancelled = self.search_outcome
            if cancelled:
                message = f"Search cancelled: {matched:,} found"
            else:
                message = f"Search complete: {matched:,} found in {scanned:,} items"
            if skipped:
                message += f", {skipped:,} locations skipped"
            self.status_changed.emit(message)

        if self.close_requested:
            self.ready_to_close.emit()

    def load_expanded_folder(self, index: QModelIndex) -> None:
        if self.tree.model() is self.model and self.model.canFetchMore(index):
            self.model.fetchMore(index)

    def release_collapsed_folder(self, index: QModelIndex) -> None:
        if self.tree.model() is self.model:
            self.model.release_children(index)

    def prepare_close(self) -> bool:
        if self.search_is_running:
            self.close_requested = True
            self.cancel_search()
            return False
        self.search_results.close()
        return True

    def _set_search_running(self, running: bool) -> None:
        self.select_root_button.setEnabled(not running)
        self.refresh_button.setEnabled(not running and self.model.root_path is not None)
        self.show_hidden.setEnabled(not running)
        self.search_input.setEnabled(not running and self.model.root_path is not None)
        self.search_button.setText("Cancel" if running else "Search")
        self.clear_search_button.setEnabled(running or self.search_view_active)
        if not running:
            self.update_search_button()

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
        self.tree.expand(root_index)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("File Tree Viewer")
        self.resize(1400, 850)
        self.setMinimumSize(1000, 650)

        self.tabs = QTabWidget()
        self.real_page = RealExplorerPage()
        self.virtual_page = ExplorerPage(virtual=True)
        self.real_page.status_changed.connect(self.statusBar().showMessage)
        self.real_page.ready_to_close.connect(self.close)
        self.tabs.addTab(self.real_page, "Real File System")
        self.tabs.addTab(self.virtual_page, "Virtual File System")

        self.setCentralWidget(self.tabs)
        self.statusBar().showMessage("Ready")

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self.real_page.prepare_close():
            self.setEnabled(False)
            self.statusBar().showMessage("Stopping search...")
            event.ignore()
            return
        event.accept()
