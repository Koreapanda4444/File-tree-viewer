from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ui.appearance import install_appearance
from ui.chrome import WorkspaceHeader
from ui.command_palette import install_command_palette
from ui.commands import identify, identify_page, menu_command
from ui.data_migration import import_previous_data
from ui.explorer_base import ExplorerPage
from ui.explorer_panes import RealWorkspace
from ui.feedback import TaskMonitor, show_error
from ui.folder_compare import FolderComparisonDialog
from ui.i18n import set_language, tr
from ui.panels import PanelManager
from ui.plan import PlanExplorerPage
from ui.preferences import (
    Preferences,
    PreferencesDialog,
    application_settings,
    restore_layout,
    save_layout,
)
from ui.real_explorer import RealExplorerPage
from ui.staging import StagingController
from ui.theme import ThemeManager
from ui.tree_export import TreeExportDialog
from ui.virtual_explorer import VirtualExplorerPage
from version import __version__
from write_policy import policy, writes_allowed

__all__ = ["ExplorerPage", "MainWindow", "RealExplorerPage", "VirtualExplorerPage"]


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        set_language(Preferences.load(application_settings()).language)
        self.write_policy = policy
        self.appearance = install_appearance()
        self.setWindowTitle(tr("File Tree Viewer") + " " + __version__)
        self.auxiliary_close_pending = False
        self.command_palette_dialog = None
        self.recovery_history_dialog = None
        self.history_dialog = None
        self.disk_dialog = None
        self.folder_comparison_dialog = None
        self.comparison_close_pending = False
        self.resize(1400, 850)
        self.setMinimumSize(960, 640)

        self.tabs = QTabWidget()
        self.real_workspace = RealWorkspace()
        self.virtual_page = VirtualExplorerPage()
        self.plan_page = PlanExplorerPage()
        for page in self.real_workspace.panes:
            page.status_changed.connect(self.statusBar().showMessage)
            page.ready_to_close.connect(self.close)
        self.virtual_page.status_changed.connect(self.statusBar().showMessage)
        self.virtual_page.ready_to_close.connect(self.close)
        self.virtual_page.close_aborted.connect(self.cancel_pending_close)
        self.plan_page.status_changed.connect(self.statusBar().showMessage)
        self.plan_page.ready_to_close.connect(self.close)
        self.tabs.addTab(self.real_workspace, tr("Real File System"))
        self.tabs.addTab(self.virtual_page, tr("Virtual File System"))
        self.tabs.addTab(self.plan_page, tr("Plan"))

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.workspace_header = WorkspaceHeader(self.tabs)
        layout.addWidget(self.workspace_header)
        layout.addWidget(self.tabs, 1)
        self.setCentralWidget(central)
        workspace_menu = self.menuBar().addMenu(tr("Workspace"))
        self.workspace_actions = QActionGroup(self)
        for index, title in enumerate(
            ("Real File System", "Virtual File System", "Plan")
        ):
            action = QAction(tr(title), self, checkable=True)
            identify(
                action, ("workspace.real", "workspace.virtual", "workspace.plan")[index]
            )
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
        menu_command(
            workspace_menu,
            "workspace.import_real",
            tr("Import Real Root into Plan"),
            self.import_real_root,
        )
        menu_command(
            workspace_menu,
            "workspace.open_snapshot",
            tr("Open Snapshot Root in Real"),
            self.open_snapshot_root,
        )
        menu_command(
            workspace_menu,
            "workspace.export_tree",
            tr("Export Folder Tree…"),
            self.export_folder_tree,
        )
        for page in self.real_workspace.panes:
            page.tree_export_button.clicked.connect(self.export_folder_tree)
        self.virtual_page.tree_export_button.clicked.connect(self.export_folder_tree)
        menu_command(
            workspace_menu,
            "workspace.compare_folders",
            tr("Compare Folders…"),
            self.compare_folders_dialog,
        )
        menu_command(
            workspace_menu,
            "workspace.disk_usage",
            tr("Analyze Disk Usage…"),
            self.analyze_disk_usage,
        )
        menu_command(
            workspace_menu,
            "workspace.history",
            tr("Operation History…"),
            self.show_history,
        )
        menu_command(
            workspace_menu,
            "workspace.recovery_history",
            tr("Recovery History…"),
            self.show_recovery_history,
        )
        self.staging = StagingController(self)
        self.plan_page.sync_workspace.filesystem_changed.connect(
            self.sync_filesystem_changed
        )
        menu_command(
            workspace_menu,
            "workspace.stage_virtual",
            tr("Add Virtual Workspace to Plan…"),
            self.staging.request_virtual,
        )
        for page in self.real_workspace.panes:
            page.stage_requested.connect(
                lambda action, p=page: self.stage_from_pane(p, action)
            )
        for action in ("copy", "move", "rename", "delete"):
            menu_command(
                workspace_menu,
                "workspace.stage_" + action,
                tr("Stage Real {value0}…", value0=tr(action.title())),
                lambda checked=False, a=action: self.staging.request(a),
            )
        workspace_menu.addSeparator()
        for name, title in (
            ("copy", "Copy to other pane"),
            ("move", "Move to other pane"),
            ("swap", "Swap paths"),
            ("same", "Open same path"),
        ):
            button = self.real_workspace.controls[name]
            action = menu_command(
                workspace_menu,
                "workspace.panes." + name,
                tr(title),
                button.click,
                mutates_files=name in ("copy", "move"),
            )
            action.command_enabled = button.isEnabled
        self.tabs.setDocumentMode(True)
        self.theme_manager = ThemeManager(self)
        self.settings = application_settings()
        self.preferences = Preferences.load(self.settings)
        self.apply_preferences(self.preferences)
        restore_layout(self, self.settings)
        self.panel_manager = PanelManager(self, self.settings)
        dual_action = identify(
            QAction(tr("Dual pane"), self, checkable=True), "view.dual_pane"
        )
        dual_action.setShortcut("Ctrl+Shift+D")
        dual_action.triggered.connect(self.real_workspace.set_dual)
        self.real_workspace.toggle.toggled.connect(dual_action.setChecked)
        self.menuBar().actions()[-1].menu().addAction(dual_action)
        other_action = identify(
            QAction(tr("Focus other pane"), self), "view.focus_other"
        )
        other_action.setShortcut("F6")
        other_action.triggered.connect(self.real_workspace.focus_other)
        self.addAction(other_action)
        self.menuBar().actions()[-1].menu().addAction(other_action)
        settings_menu = self.menuBar().addMenu(tr("Settings"))
        menu_command(
            settings_menu,
            "settings.preferences",
            tr("Preferences..."),
            self.edit_preferences,
        )
        menu_command(
            settings_menu,
            "settings.import_previous",
            tr("Import Previous Data…"),
            lambda: import_previous_data(self),
        )
        self.command_action = install_command_palette(self)
        settings_menu.addAction(self.command_action)
        self.read_only_action = identify(
            QAction(tr("Read-only mode"), self, checkable=True), "settings.read_only"
        )
        self.read_only_action.setToolTip(
            tr(
                "Restricts this application's file changes; external programs can still write."
            )
        )
        self.read_only_action.setChecked(policy.read_only)
        self.read_only_action.triggered.connect(self.set_read_only)
        settings_menu.addAction(self.read_only_action)
        self.menuBar().setCornerWidget(self.make_read_only_button())
        self.task_monitor = TaskMonitor(self)
        self.statusBar().addPermanentWidget(self.task_monitor)
        for page in (*self.real_workspace.panes, self.virtual_page, self.plan_page):
            page.task_failed.connect(self.show_task_error)
            page.task_progress.connect(
                lambda message, completed, total, p=page: self.task_monitor.advance(
                    p, message, completed, total
                )
            )
        help_menu = self.menuBar().addMenu(tr("Help"))
        menu_command(
            help_menu, "help.about", tr("About File Tree Viewer"), self.show_about
        )
        self.statusBar().showMessage(tr("Ready"))
        self.refresh_write_state()
        QTimer.singleShot(0, lambda: self.real_workspace.restore(self.settings))

    def make_read_only_button(self):
        from PySide6.QtWidgets import QToolButton

        button = QToolButton()
        button.setDefaultAction(self.read_only_action)
        return button

    def set_read_only(self, enabled):
        busy = (
            any(p.operation_is_running for p in self.real_workspace.panes)
            or self.plan_page.apply_is_running
            or self.plan_page.undo_is_running
            or self.virtual_page.task_is_running
        )
        busy = busy or any(
            getattr(dialog, "thread", None) is not None
            for dialog in self.findChildren(QDialog)
            if type(dialog).__name__ == "SyncReviewDialog"
        )
        if (
            busy
            or (
                enabled
                and any(
                    not p._confirm_editor_transition()
                    for p in self.real_workspace.panes
                )
            )
            or not policy.set_read_only(enabled)
        ):
            self.read_only_action.setChecked(policy.read_only)
            self.statusBar().showMessage(
                tr("Wait for file-changing tasks before switching read-only mode")
            )
            return False
        self.read_only_action.setChecked(policy.read_only)
        self.refresh_write_state()
        self.statusBar().showMessage(
            tr("Read-only mode enabled")
            if enabled
            else tr("Normal file operations enabled")
        )
        return True

    def refresh_write_state(self):
        for page in self.real_workspace.panes:
            page.update_action_buttons()
            page.editor_text_changed()
            result = page.current_preview_result
            page.preview.setReadOnly(
                policy.read_only or not (result and result.editable)
            )
            identify_page(page, "real")
        self.virtual_page.update_action_buttons()
        self.plan_page.update_buttons()
        for scope, page in (("virtual", self.virtual_page), ("plan", self.plan_page)):
            identify_page(page, scope)
        for page in (*self.real_workspace.panes, self.virtual_page, self.plan_page):
            if policy.read_only:
                for value in vars(page).values():
                    spec = getattr(value, "command_spec", None)
                    if spec is not None and spec.mutates_files:
                        value.setEnabled(False)
        self.real_workspace.update_controls()

    @property
    def real_page(self):
        return self.real_workspace.active

    def stage_from_pane(self, page, action):
        self.real_workspace.activate(page)
        self.staging.request(action)

    def show_about(self):
        QMessageBox.about(
            self,
            tr("About File Tree Viewer"),
            tr(
                "File Tree Viewer {version}\n\nExplore actual files, design virtual folders and review file changes before applying them.\nWindows portable edition.",
                version=__version__,
            ),
        )

    def export_folder_tree(self):
        page = self.tabs.currentWidget()
        if page is self.real_workspace:
            page = self.real_page
            if page.operation_is_running:
                return
            source, kind = page._selected_directory(), "real"
        elif page is self.virtual_page:
            if page.task_is_running:
                return
            source, kind = page._selected_directory(), "virtual"
        else:
            if any(
                getattr(page, name)
                for name in (
                    "scan_is_running",
                    "apply_is_running",
                    "undo_is_running",
                    "plan_io_is_running",
                )
            ):
                return
            source, kind = page.snapshot, "snapshot"
        if source is None:
            self.statusBar().showMessage(tr("Select a folder or load a snapshot first"))
            return
        dialog = TreeExportDialog(source, kind, self)
        dialog.exec()
        dialog.deleteLater()

    def show_task_error(self, message: str) -> None:
        if self.isEnabled():
            show_error(
                self,
                tr("Background task failed"),
                tr("The task stopped unexpectedly. Review its result before retrying."),
                message,
            )
        self.statusBar().showMessage("Background task failed: " + message)

    def apply_preferences(self, preferences: Preferences) -> None:
        self.preferences = preferences
        self.theme_manager.apply(preferences.theme)
        for pane in self.real_workspace.panes:
            pane.preferences = preferences
            pane.show_hidden.setChecked(preferences.show_hidden)
        self.plan_page.default_conflict_policy = preferences.conflict_policy
        for page in (*self.real_workspace.panes, self.virtual_page):
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
                tr(
                    "Wait for Real operations or search to finish before changing preferences"
                )
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
        self.real_workspace.left.explorer_state.save_session(self.settings)
        self.real_workspace.save(self.settings)
        self.preferences = replace(
            self.preferences, show_hidden=self.real_page.show_hidden.isChecked()
        )
        self.preferences.save(self.settings)
        save_layout(self, self.settings)
        self.settings.sync()
        if self.settings.status() != QSettings.Status.NoError:
            QMessageBox.warning(
                self,
                tr("Preferences could not be saved"),
                tr(
                    "Check permissions and free space for the application settings location."
                ),
            )

    def compare_folders_dialog(self):
        if self.folder_comparison_dialog is not None:
            self.folder_comparison_dialog.raise_()
            return
        dialog = FolderComparisonDialog(
            self.real_page.model.root_path, self, stage_sync=self.stage_sync_plan
        )
        self.folder_comparison_dialog = dialog
        dialog.filesystem_changed.connect(self.sync_filesystem_changed)
        dialog.sync_requested.connect(
            lambda plan: self.tabs.setCurrentWidget(self.plan_page)
        )
        dialog.ready_to_close.connect(
            lambda: self.close() if self.comparison_close_pending else None
        )
        try:
            dialog.exec()
        finally:
            self.folder_comparison_dialog = None
            self.comparison_close_pending = False
            dialog.deleteLater()

    def show_recovery_history(self):
        from ui.recovery_history import RecoveryHistoryDialog

        if self.recovery_history_dialog is not None:
            self.recovery_history_dialog.raise_()
            return
        root = self.real_page.model.root_path
        if root is None:
            name = QFileDialog.getExistingDirectory(self, tr("Choose recovery root"))
            if not name:
                return
            root = Path(name)
        dialog = self.recovery_history_dialog = RecoveryHistoryDialog(root, self)
        dialog.undo_requested.connect(self.undo_history_transaction)
        dialog.recover_requested.connect(self.review_history_recovery)
        try:
            dialog.exec()
        finally:
            self.recovery_history_dialog = None
            dialog.deleteLater()
            self.finish_auxiliary_close()

    def undo_history_transaction(self, record):
        if not writes_allowed(self):
            return
        if self.staging.busy() or self.real_page.operation_is_running:
            self.statusBar().showMessage(tr("Wait for Real and Plan tasks before Undo"))
            return
        if len(self.plan_page.plan) and not self.plan_page._confirm_plan_replacement():
            return
        self.tabs.setCurrentWidget(self.plan_page)
        self.plan_page.start_plan_undo(record)

    def review_history_recovery(self, root):
        if not writes_allowed(self):
            return
        if self.staging.busy() or self.real_page.operation_is_running:
            self.statusBar().showMessage(
                tr("Wait for Real and Plan tasks before recovery")
            )
            return
        self.tabs.setCurrentWidget(self.plan_page)
        self.plan_page.check_recovery(root)
        self.plan_page.recover_interrupted()

    def show_history(self):
        from ui.history import HistoryDialog

        if self.history_dialog is not None:
            self.history_dialog.raise_()
            return
        dialog = self.history_dialog = HistoryDialog(self)
        dialog.reveal_requested.connect(self.reveal_usage_item)
        try:
            dialog.exec()
        finally:
            self.history_dialog = None
            dialog.deleteLater()
            self.finish_auxiliary_close()

    def analyze_disk_usage(self):
        from ui.disk_usage import DiskUsageDialog

        if self.disk_dialog is not None:
            self.disk_dialog.raise_()
            return
        root = self.real_page.model.root_path
        if root is None:
            root = QFileDialog.getExistingDirectory(self, tr("Choose analysis root"))
        if not root:
            return
        dialog = self.disk_dialog = DiskUsageDialog(root, self)
        dialog.reveal_requested.connect(self.reveal_usage_item)
        dialog.stage_requested.connect(self.stage_usage_items)
        try:
            dialog.exec()
        finally:
            self.disk_dialog = None
            dialog.deleteLater()
            self.finish_auxiliary_close()

    def reveal_usage_item(self, path):
        self.tabs.setCurrentWidget(self.real_workspace)
        if self.real_page.set_root(path.parent):
            self.real_page.jump_to_item(str(path))

    def stage_usage_items(self, action, root, paths):
        if self.staging.busy():
            self.statusBar().showMessage(
                tr("Wait for Plan tasks before staging analysis items")
            )
            return
        if self.real_page.set_root(root):
            self.staging.request(action, paths)

    def stage_sync_plan(self, plan):
        if self.staging.busy():
            self.statusBar().showMessage(
                tr("Wait for Plan tasks to finish before staging synchronization")
            )
            return False
        if not self.plan_page.sync_workspace.stage(plan):
            return False
        self.tabs.setCurrentWidget(self.plan_page)
        self.statusBar().showMessage(
            tr("Synchronization staged in Plan. Review both roots before applying.")
        )
        return True

    def sync_filesystem_changed(self, root):
        self.plan_page.filesystem_changed.emit(root)
        if self.plan_page.snapshot is not None and self.plan_page.snapshot.root == root:
            if not len(self.plan_page.plan) and not self.staging.busy():
                self.plan_page.start_snapshot(root)
            else:
                self.plan_page.status_changed.emit(
                    tr(
                        "Synchronization changed this Plan root. Review Diff against live files before applying staged changes."
                    )
                )

    def import_real_root(self) -> None:
        root = self.real_page.model.root_path
        if root is None:
            self.statusBar().showMessage(tr("Select a Real root first"))
            return
        if len(self.plan_page.plan) and not self.plan_page._confirm_plan_replacement():
            return
        self.tabs.setCurrentWidget(self.plan_page)
        self.plan_page.start_snapshot(root)

    def open_snapshot_root(self) -> None:
        snapshot = self.plan_page.snapshot
        if snapshot is None:
            self.statusBar().showMessage(tr("Load a snapshot first"))
            return
        self.tabs.setCurrentWidget(self.real_workspace)
        self.real_page.set_root(snapshot.root)

    def finish_auxiliary_close(self):
        if self.auxiliary_close_pending:
            self.auxiliary_close_pending = False
            QTimer.singleShot(0, self.close)

    def closeEvent(self, event: QCloseEvent) -> None:
        for dialog in (
            self.recovery_history_dialog,
            self.history_dialog,
            self.disk_dialog,
            self.command_palette_dialog,
        ):
            if dialog is not None:
                self.auxiliary_close_pending = True
                dialog.reject()
                event.ignore()
                return
        if (
            self.folder_comparison_dialog is not None
            and self.folder_comparison_dialog.is_busy
        ):
            self.comparison_close_pending = True
            self.folder_comparison_dialog.reject()
            event.ignore()
            return
        if not self.real_workspace.prepare_close():
            if self.real_workspace.close_cancelled:
                event.ignore()
                return
            self.setEnabled(False)
            self.statusBar().showMessage(tr("Waiting for file tasks to finish..."))
            event.ignore()
            return
        if not self.virtual_page.prepare_close():
            if self.virtual_page.close_cancelled:
                self.real_workspace.cancel_close_request()
                event.ignore()
                return
            self.setEnabled(False)
            self.statusBar().showMessage(tr("Waiting for virtual workspace task..."))
            event.ignore()
            return
        if not self.plan_page.prepare_close():
            if self.plan_page.close_cancelled:
                self.real_workspace.cancel_close_request()
                self.virtual_page.cancel_close_request()
                event.ignore()
                return
            self.setEnabled(False)
            self.statusBar().showMessage(tr("Waiting for snapshot task..."))
            event.ignore()
            return
        self.save_preferences()
        self.real_workspace.finalize_close()
        self.plan_page.finalize_close()
        event.accept()

    def cancel_pending_close(self) -> None:
        self.real_workspace.cancel_close_request()
        self.virtual_page.cancel_close_request()
        self.plan_page.cancel_close_request()
        self.setEnabled(True)
        self.statusBar().showMessage(tr("Close cancelled"))
