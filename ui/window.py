from __future__ import annotations

import sqlite3
import time
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import (
    QItemSelectionModel,
    QModelIndex,
    QPersistentModelIndex,
    QSortFilterProxyModel,
    Qt,
    QThread,
    QUrl,
    Signal,
)
from PySide6.QtGui import QCloseEvent, QDesktopServices, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
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

from real.operations import (
    FileChange,
    FileOperationWorker,
    OperationRecord,
    create_item,
    rename_item,
)
from real.preview import FilePreviewWorker, PreviewResult, save_text_atomic
from real.search import SearchResultsModel, SearchWorker
from real.tree import FileTreeModel
from ui.plan import PlanExplorerPage
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
        details_layout.addWidget(preview_group, 1)

        splitter.addWidget(details)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        return splitter

    def _create_action_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
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

        self._set_tree_model(self.model)
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
        self.tree.expanded.connect(self.load_expanded_folder)
        self.tree.collapsed.connect(self.release_collapsed_folder)
        self.model.directory_error.connect(self.status_changed)

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

    def set_root(self, path: Path) -> None:
        if self.model.root_path is not None and not self._confirm_editor_transition():
            return
        try:
            self.model.set_root(path)
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, "Cannot open root", str(error))
            return

        self._show_path(None)
        self.search_results.clear()
        self.search_view_active = False
        self._set_tree_model(self.model)
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
        can_search = self.model.root_path is not None and bool(
            self.search_input.text().strip()
        )
        self.search_button.setEnabled(can_search)
        self.clear_search_button.setEnabled(
            self.search_view_active or bool(self.search_input.text())
        )

    def start_search(self) -> None:
        if self.operation_is_running:
            return
        if self.search_is_running:
            self.cancel_search()
            return

        root = self.model.root_path
        query = self.search_input.text().strip()
        if root is None or not query:
            return
        if not self._confirm_editor_transition():
            return

        self._show_path(None)
        self.search_results.clear()
        self.search_view_active = True
        self.search_outcome = None
        self._set_tree_model(self.search_results)
        self._configure_search_columns()
        self.tree.expand(self.search_results.index(0, 0))
        self.tree.expand(self.search_results.index(1, 0))

        thread = QThread(self)
        worker = SearchWorker(root, query, self.show_hidden.isChecked())
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.batch_found.connect(self.receive_search_batch)
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

    def receive_search_batch(self, batch: list) -> None:
        worker = self.search_worker
        try:
            if self.search_view_active:
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
        thread = QThread(self)
        worker = FilePreviewWorker(path)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self.receive_preview)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self.preview_thread_finished)
        thread.finished.connect(thread.deleteLater)

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

        if self.close_requested:
            self._emit_ready_to_close()

    def _display_preview(self, result: PreviewResult) -> None:
        self.current_preview_result = result
        self.reload_file_button.setEnabled(True)
        self.open_external_button.setEnabled(True)

        if result.error:
            self._set_preview_text("")
            self.preview.setReadOnly(True)
            self.preview_status.setText(f"Cannot preview file: {result.error}")
            return
        if result.binary:
            self._set_preview_text("Binary file preview is unavailable.")
            self.preview.setReadOnly(True)
            self.preview_status.setText("Binary file - open externally to view or edit")
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

        if current.st_size != result.size or current.st_mtime_ns != result.modified_ns:
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
            )
        except (OSError, UnicodeError) as error:
            self._show_operation_error("Cannot save file", error)
            return False

        self.current_preview_result = replace(
            result,
            text=self.preview.toPlainText(),
            size=size,
            modified_ns=modified_ns,
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
        current_model = self.tree.model()
        path_from_index = getattr(current_model, "path_from_index", None)
        if not index.isValid() or not callable(path_from_index):
            return None
        return path_from_index(index)

    def _update_details(self, path: Path) -> None:
        try:
            details = path.stat(follow_symlinks=False)
        except OSError:
            size = "-"
            modified = "-"
        else:
            size = "-" if path.is_dir() else self._format_size(details.st_size)
            modified = time.strftime(
                "%Y-%m-%d %H:%M:%S",
                time.localtime(details.st_mtime),
            )

        if path.is_symlink():
            file_type = "Symbolic Link"
        elif path.is_dir():
            file_type = "Folder"
        elif path.suffix:
            file_type = f"{path.suffix[1:].upper()} File"
        else:
            file_type = "File"

        values = {
            "name": path.name or str(path),
            "path": str(path),
            "type": file_type,
            "size": size,
            "modified": modified,
        }
        for name, value in values.items():
            self.detail_values[name].setText(value)

    def _clear_details(self) -> None:
        for value in self.detail_values.values():
            value.setText("-")

    def _set_preview_text(self, text: str) -> None:
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
        blocked = self.search_is_running or self.operation_is_running
        has_root = self.model.root_path is not None
        selected = self._operable_selected_paths()

        self.new_file_button.setEnabled(has_root and not blocked)
        self.new_folder_button.setEnabled(has_root and not blocked)
        self.rename_button.setEnabled(len(selected) == 1 and not blocked)
        self.copy_button.setEnabled(bool(selected) and not blocked)
        self.move_button.setEnabled(bool(selected) and not blocked)
        self.delete_button.setEnabled(bool(selected) and not blocked)
        self.undo_button.setEnabled(bool(self.undo_stack) and not blocked)

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
        thread = QThread(self)
        worker = FileOperationWorker(
            action,
            sources=sources,
            destination=destination,
            record=record,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self.show_operation_progress)
        worker.finished.connect(self.finish_file_operation)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self.operation_thread_finished)
        thread.finished.connect(thread.deleteLater)

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
        self.status_changed.emit(f"{action.title()}: {position}/{total} - {name}")

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

        if errors and not self.close_requested:
            shown = "\n".join(errors[:8])
            if len(errors) > 8:
                shown += f"\n...and {len(errors) - 8} more"
            QMessageBox.warning(self, "File operation incomplete", shown)

        if action == "delete":
            message = f"Moved {len(changes)} item(s) to the Recycle Bin"
        elif action == "undo":
            message = f"Undid {len(changes)} item(s)"
        else:
            message = f"{action.title()} complete: {len(changes)} item(s)"
        if errors:
            message += f", {len(errors)} failed"
        self.status_changed.emit(message)
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
        self.tree.setModel(model)
        selection = self.tree.selectionModel()
        if selection is not None:
            selection.selectionChanged.connect(self.update_action_buttons)
            selection.currentChanged.connect(self.preview_selection_changed)
        self.update_action_buttons()

    def _show_operation_error(self, title: str, error: Exception) -> None:
        QMessageBox.critical(self, title, str(error))
        self.status_changed.emit(str(error))

    def load_expanded_folder(self, index: QModelIndex) -> None:
        if self.tree.model() is self.model and self.model.canFetchMore(index):
            self.model.fetchMore(index)

    def release_collapsed_folder(self, index: QModelIndex) -> None:
        if self.tree.model() is self.model:
            self.model.release_children(index)

    def prepare_close(self) -> bool:
        self.close_cancelled = False
        if not self._confirm_editor_transition():
            self.close_cancelled = True
            return False
        waiting = False
        self.close_requested = True
        if self.search_is_running:
            self.cancel_search()
            waiting = True
        if self.operation_is_running:
            waiting = True
        if self.preview_is_running:
            self.pending_preview_path = None
            if self.preview_worker is not None:
                self.preview_worker.cancel()
            waiting = True
        return not waiting

    def cancel_close_request(self) -> None:
        self.close_requested = False

    def finalize_close(self) -> None:
        self.search_results.close()

    def _set_search_running(self, running: bool) -> None:
        self.select_root_button.setEnabled(not running)
        self.refresh_button.setEnabled(not running and self.model.root_path is not None)
        self.show_hidden.setEnabled(not running)
        self.search_input.setEnabled(not running and self.model.root_path is not None)
        self.search_button.setText("Cancel" if running else "Search")
        self.clear_search_button.setEnabled(running or self.search_view_active)
        if not running:
            self.update_search_button()
        self.update_action_buttons()

    def _set_operation_running(self, running: bool) -> None:
        self.tree.setEnabled(not running)
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
        self.tree.expand(root_index)


class VirtualExplorerPage(ExplorerPage):
    status_changed = Signal(str)
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
        thread = QThread(self)
        worker = VirtualTaskWorker(action, path, data)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self.receive_task_result)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self.task_thread_finished)
        thread.finished.connect(thread.deleteLater)
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
        self.statusBar().showMessage("Ready")

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
        self.real_page.finalize_close()
        self.plan_page.finalize_close()
        event.accept()

    def cancel_pending_close(self) -> None:
        self.real_page.cancel_close_request()
        self.virtual_page.cancel_close_request()
        self.plan_page.cancel_close_request()
        self.setEnabled(True)
        self.statusBar().showMessage("Close cancelled")
