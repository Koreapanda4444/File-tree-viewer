from __future__ import annotations

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QInputDialog

from fs_paths import normalized_path, validate_name
from plan_staging import stage_real_items
from planning import PlanAction
from ui.feedback import show_error


class StagingController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.pending = None
        window.plan_page.snapshot_ready.connect(self.snapshot_ready)

    def busy(self):
        page = self.window.plan_page
        return any(
            getattr(page, name)
            for name in (
                "scan_is_running",
                "apply_is_running",
                "undo_is_running",
                "analysis_is_running",
                "diagnostic_is_running",
                "plan_io_is_running",
                "simulation_is_running",
            )
        )

    def request(self, action, paths=None):
        window = self.window
        real, page = window.real_page, window.plan_page
        if self.busy() or real.operation_is_running or real.search_is_running:
            window.statusBar().showMessage(
                "Wait for the current task before staging files"
            )
            return
        if not real._confirm_editor_transition():
            return
        paths = list(paths) if paths is not None else real._operable_selected_paths()
        root = real.model.root_path
        if not paths or root is None:
            return
        action = PlanAction(action)
        target = None
        if action in {PlanAction.MOVE, PlanAction.COPY}:
            target, accepted = QInputDialog.getText(
                window,
                f"Stage {action.value.title()}",
                "Destination folder relative to the Plan root (blank = root):",
            )
            if not accepted:
                return
            target = target or None
        elif action is PlanAction.RENAME:
            if len(paths) != 1:
                return
            name, accepted = QInputDialog.getText(
                window, "Stage Rename", "New name:", text=paths[0].name
            )
            if not accepted:
                return
            try:
                target = (
                    paths[0].relative_to(root).with_name(validate_name(name)).as_posix()
                )
            except ValueError as error:
                self.show_error(error)
                return
        self.pending = (root, paths, action, target)
        if page.snapshot is not None and normalized_path(
            page.snapshot.root
        ) == normalized_path(root):
            self.snapshot_ready(page.snapshot)
            return
        if len(page.plan) and not page._confirm_plan_replacement():
            self.pending = None
            return
        window.tabs.setCurrentWidget(page)
        page.start_snapshot(root)
        if page.scan_thread is not None:
            page.scan_thread.finished.connect(self.clear_pending)

    def clear_pending(self):
        self.pending = None

    def snapshot_ready(self, snapshot):
        pending, self.pending = self.pending, None
        if pending is None or normalized_path(pending[0]) != normalized_path(
            snapshot.root
        ):
            return
        page = self.window.plan_page
        try:
            additions = stage_real_items(
                snapshot, page.plan, pending[1], pending[2], pending[3]
            )
            page.plan.set_conflict_policy(
                [op.operation_id for op in additions], page.default_conflict_policy
            )
        except (OSError, ValueError) as error:
            self.show_error(error)
            return
        page._refresh_plan_list()
        self.window.tabs.setCurrentWidget(page)
        page.status_changed.emit(
            f"Staged {len(additions):,} {pending[2].value} operation(s); review Diff before applying"
        )

    def show_error(self, error):
        show_error(
            self.window,
            "Cannot stage files",
            "No operations were added. Check paths and refresh the snapshot if files changed.",
            str(error),
        )
