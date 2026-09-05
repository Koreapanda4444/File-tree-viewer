from __future__ import annotations

from collections import defaultdict

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QLabel,
    QTableView,
    QVBoxLayout,
)

from plan_diff import ChangeKind, IssueKind, PlanSimulation

INVALID_INDEX = QModelIndex()


class PlanDiffModel(QAbstractTableModel):
    headers = ("Change", "Source", "Target", "Result")

    def __init__(self, simulation: PlanSimulation, parent=None) -> None:
        super().__init__(parent)
        self.changes = simulation.changes
        grouped = defaultdict(list)
        for issue in simulation.issues:
            grouped[issue.operation_id].append(issue)
        self.issues = grouped

    def rowCount(self, parent=INVALID_INDEX):
        return 0 if parent.isValid() else len(self.changes)

    def columnCount(self, parent=INVALID_INDEX):
        return 4

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        change = self.changes[index.row()]
        issues = self.issues.get(change.operation_id, ())
        if role == Qt.ItemDataRole.DisplayRole:
            return (
                change.kind.value.upper(),
                change.source.as_posix() if change.source is not None else "-",
                change.target.as_posix() if change.target is not None else "-",
                "Ready" if not issues else issue_result(issues),
            )[index.column()]
        if role == Qt.ItemDataRole.ToolTipRole and issues:
            return "\n".join(issue.message for issue in issues)
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
            and 0 <= section < len(self.headers)
        ):
            return self.headers[section]
        return None


class IssueModel(QAbstractTableModel):
    headers = ("Type", "Path", "Problem")

    def __init__(self, simulation: PlanSimulation, parent=None) -> None:
        super().__init__(parent)
        self.issues = simulation.issues

    def rowCount(self, parent=INVALID_INDEX):
        return 0 if parent.isValid() else len(self.issues)

    def columnCount(self, parent=INVALID_INDEX):
        return 3

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role not in (
            Qt.ItemDataRole.DisplayRole,
            Qt.ItemDataRole.ToolTipRole,
        ):
            return None
        issue = self.issues[index.row()]
        return (
            issue.kind.value.upper(),
            issue.path.as_posix() if issue.path is not None else "-",
            issue.message,
        )[index.column()]

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
            and 0 <= section < len(self.headers)
        ):
            return self.headers[section]
        return None


class PlanDiffDialog(QDialog):
    def __init__(self, simulation: PlanSimulation, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Plan Diff and Simulation")
        self.resize(980, 760)
        layout = QVBoxLayout(self)

        summary = QLabel(summary_text(simulation))
        summary.setWordWrap(True)
        layout.addWidget(summary)

        changes_group = QGroupBox("Before / After Diff")
        changes_layout = QVBoxLayout(changes_group)
        self.diff_model = PlanDiffModel(simulation, self)
        changes = QTableView()
        changes.setModel(self.diff_model)
        changes.setColumnWidth(0, 110)
        changes.setColumnWidth(1, 300)
        changes.setColumnWidth(2, 300)
        changes.horizontalHeader().setStretchLastSection(True)
        changes_layout.addWidget(changes)
        layout.addWidget(changes_group, 2)

        issues_group = QGroupBox(f"Problems ({len(simulation.issues):,})")
        issues_layout = QVBoxLayout(issues_group)
        self.issue_model = IssueModel(simulation, self)
        issues = QTableView()
        issues.setModel(self.issue_model)
        issues.setColumnWidth(0, 110)
        issues.setColumnWidth(1, 300)
        issues.horizontalHeader().setStretchLastSection(True)
        issues_layout.addWidget(issues)
        layout.addWidget(issues_group, 1)

        note = QLabel(
            "Simulation only: no files were changed. "
            + (
                "No problems were detected."
                if simulation.can_apply
                else "Resolve every listed problem before applying this Plan."
            )
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


def issue_result(issues) -> str:
    kinds = {issue.kind for issue in issues}
    if IssueKind.INVALID in kinds:
        return "Invalid"
    return "Conflict"


def summary_text(simulation: PlanSimulation) -> str:
    counts = simulation.change_counts
    issue_counts = simulation.issue_counts
    parts = [
        f"{len(simulation.changes):,} changes",
        f"{counts[ChangeKind.MOVED]:,} moved",
        f"{counts[ChangeKind.RENAMED]:,} renamed",
        f"{counts[ChangeKind.CREATED]:,} created",
        f"{counts[ChangeKind.DELETED]:,} deleted",
    ]
    status = (
        "Ready to apply"
        if simulation.can_apply
        else (
            f"Blocked: {issue_counts[IssueKind.CONFLICT]:,} conflicts, "
            f"{issue_counts[IssueKind.INVALID]:,} invalid operations"
        )
    )
    return " · ".join(parts) + f"\n{status}"
