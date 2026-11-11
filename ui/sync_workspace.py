from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ui.i18n import tr
from ui.sync import SyncReviewDialog
from ui.widgets import PlainLabel


class SyncWorkspace(QGroupBox):
    filesystem_changed = Signal(object)

    def __init__(self, page):
        super().__init__("Multi-root Synchronization", page)
        self.page = page
        self.plan = self.result = self.dialog = None
        layout = QVBoxLayout(self)
        self.summary = PlainLabel(
            tr("Compare folders to stage a separate synchronization plan here.")
        )
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        controls = QHBoxLayout()
        self.review_button = QPushButton(tr("Review Synchronization…"))
        self.discard_button = QPushButton(tr("Discard Synchronization"))
        self.review_button.clicked.connect(self.review)
        self.discard_button.clicked.connect(self.discard)
        controls.addWidget(self.review_button)
        controls.addWidget(self.discard_button)
        controls.addStretch()
        layout.addLayout(controls)
        self.refresh()

    def refresh(self):
        self.review_button.setEnabled(self.plan is not None and self.dialog is None)
        self.discard_button.setEnabled(self.plan is not None and self.dialog is None)
        if self.plan is not None:
            status = (
                f"; execution: {self.result.status}" if self.result else "; not applied"
            )
            self.summary.setText(
                tr(
                    "{value0}: {value1:,} changes, {value2:,} blocking issues{value3}\n{value4}\n{value5}",
                    value0=self.plan.direction,
                    value1=self.plan.count,
                    value2=self.plan.issue_count,
                    value3=status,
                    value4=self.plan.roots[0],
                    value5=self.plan.roots[1],
                )
            )
        else:
            self.summary.setText(
                tr("Compare folders to stage a separate synchronization plan here.")
            )

    def confirm_discard(self):
        return (
            self.plan is None
            or QMessageBox.question(
                self,
                tr("Discard synchronization plan"),
                tr(
                    "Discard the staged synchronization plan? Transaction recovery records will remain available."
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        )

    def stage(self, plan):
        if self.dialog is not None or not self.confirm_discard():
            return False
        self.release()
        self.plan = plan
        self.refresh()
        return True

    def review(self):
        if self.plan is None or self.dialog is not None:
            return
        if any(
            getattr(self.page, name)
            for name in (
                "scan_is_running",
                "apply_is_running",
                "undo_is_running",
                "analysis_is_running",
                "diagnostic_is_running",
                "plan_io_is_running",
                "simulation_is_running",
            )
        ):
            self.page.status_changed.emit(
                tr("Wait for Plan tasks to finish before reviewing synchronization")
            )
            return
        dialog = self.dialog = SyncReviewDialog(
            self.plan, self, application_result=self.result
        )
        dialog.filesystem_changed.connect(self.filesystem_changed)
        dialog.status_changed.connect(self.page.status_changed)
        self.refresh()
        try:
            dialog.exec()
            self.result = dialog.application_result
        finally:
            self.dialog = None
            dialog.deleteLater()
            self.refresh()
            if self.page.close_requested:
                QTimer.singleShot(0, self.page.ready_to_close.emit)

    def discard(self):
        if self.dialog is None and self.confirm_discard():
            self.release()

    def release(self):
        if self.plan is not None:
            self.plan.close()
        self.plan = self.result = None
        self.refresh()
