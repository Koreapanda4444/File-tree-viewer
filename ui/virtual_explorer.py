from __future__ import annotations
import time
from pathlib import Path
from PySide6.QtCore import (
    QItemSelectionModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
    QThread,
    Signal,
)
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox
from ui.i18n import tr
from ui.tasks import prepare_worker_thread
from virtual.workspace import (
    VirtualNode,
    VirtualTaskWorker,
    VirtualTreeModel,
    VirtualUndoRecord,
    file_type,
    format_size,
    node_size,
)
from ui.explorer_base import ExplorerPage


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
            self.preview_status.setText(tr("Select a virtual file to edit it"))
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
            self.preview_status.setText(tr("Virtual folder selected"))
            self.save_file_button.setEnabled(False)
            self.reload_file_button.setEnabled(False)
            return

        self._set_preview_text(node.content)
        self.preview.setReadOnly(False)
        self.preview_status.setText(tr("Editable virtual text file - UTF-8"))
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
        self.preview_status.setText(tr("Saved to virtual workspace - UTF-8"))
        self.status_changed.emit(
            tr("Updated {value0}", value0=self.model.path_for(node))
        )
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
            tr("Unsaved virtual file"),
            tr("Save this virtual file before continuing?"),
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
            tr("New Virtual {value0}", value0=tr(label).title()),
            tr("{value0} name:", value0=tr(label).title()),
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
            tr("Rename Virtual Item"),
            tr("New name:"),
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
            tr("Virtual {value0} Destination", value0=action.title()),
            tr("Destination path:"),
            text=initial,
        )
        if not accepted:
            return None
        destination = self.model.resolve_path(path)
        if destination is None or not destination.is_directory:
            QMessageBox.warning(self, tr("Invalid destination"), path)
            return None
        return destination

    def delete_selected(self) -> None:
        selected = self._operable_selected_nodes()
        if not selected or not self._confirm_editor_transition():
            return
        answer = QMessageBox.question(
            self,
            tr("Delete Virtual Items"),
            tr("Delete {value0} selected virtual item(s)?", value0=len(selected)),
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
        self.status_changed.emit(tr("Undid virtual {value0}", value0=record.action))
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
        self.status_changed.emit(
            tr(
                'Virtual search: {value0:,} result(s) for "{value1}"',
                value0=matched,
                value1=query,
            )
        )
        self.update_search_buttons()

    def clear_search(self) -> None:
        if not self._confirm_editor_transition():
            return
        self.proxy.setFilterFixedString("")
        self.search_input.clear()
        self._show_node(None)
        self._expand_root()
        self.status_changed.emit(tr("Virtual search cleared"))
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
            tr("Save Virtual Workspace"),
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
            tr("Load Virtual Workspace"),
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
            tr("Choose Export Parent Folder"),
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
        self.status_changed.emit(tr("Virtual workspace reset"))
        self.update_action_buttons()

    def _confirm_workspace_replacement(self) -> bool:
        if not self.workspace_dirty:
            return True
        answer = QMessageBox.warning(
            self,
            tr("Unsaved workspace"),
            tr("Unsaved virtual workspace changes will be lost. Continue?"),
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
        self.status_changed.emit(tr("Virtual {value0} started...", value0=action))
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
                QMessageBox.warning(
                    self, tr("Virtual {value0} failed", value0=action), error
                )
                self.status_changed.emit(error)
            elif action == "save" and isinstance(result, Path):
                self.workspace_path = result
                self._set_workspace_dirty(False)
                self.status_changed.emit(
                    tr("Virtual workspace saved: {value0}", value0=result)
                )
            elif action == "load" and isinstance(result, VirtualNode):
                self.selection_guard = True
                self.model.replace_root(result)
                self.selection_guard = False
                self.undo_stack.clear()
                self.workspace_path = task_path
                self._reset_view_after_change()
                self._set_workspace_dirty(False)
                self.status_changed.emit(tr("Virtual workspace loaded"))
            elif action == "stage" and isinstance(result, tuple):
                self.staged_workspace.emit(result)
            elif action == "export" and isinstance(result, Path):
                self.status_changed.emit(
                    tr("Virtual workspace exported: {value0}", value0=result)
                )

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
            value.setText(tr("-"))

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
                tr("Unsaved virtual workspace"),
                tr("Save the virtual workspace before closing?"),
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
