from __future__ import annotations

from collections import defaultdict

from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from conflicts import ResolutionChoice, resolution_options
from plan_diff import PlanSimulation
from planning import FilePlan


class ConflictResolutionDialog(QDialog):
    def __init__(
        self,
        plan: FilePlan,
        simulation: PlanSimulation,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Resolve Plan Problems")
        self.resize(1_100, 620)
        self.rows: list[tuple[str, QComboBox]] = []

        layout = QVBoxLayout(self)
        help_text = QLabel(
            "Choose how to handle every blocked operation. Auto Rename finds the "
            "next available numbered name; Skip keeps the operation in the Plan "
            "but excludes it from Apply. Overwrite is offered only for an existing "
            "target."
        )
        help_text.setWordWrap(True)
        layout.addWidget(help_text)

        grouped = defaultdict(list)
        for issue in simulation.issues:
            grouped[issue.operation_id].append(issue)
        operations = {
            operation.operation_id: operation for operation in plan.operations
        }

        self.table = QTableWidget(len(grouped), 5)
        self.table.setHorizontalHeaderLabels(
            ("Action", "Source", "Target", "Problem", "Resolution")
        )
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setColumnWidth(0, 100)
        self.table.setColumnWidth(1, 230)
        self.table.setColumnWidth(2, 230)
        self.table.setColumnWidth(3, 310)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)

        for row, (operation_id, issues) in enumerate(grouped.items()):
            operation = operations[operation_id]
            values = (
                operation.action.value.replace("_", " ").upper(),
                operation.source.as_posix()
                if operation.source is not None
                else "-",
                operation.target.as_posix()
                if operation.target is not None
                else "-",
                "; ".join(dict.fromkeys(issue.message for issue in issues)),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(row, column, item)

            combo = QComboBox()
            combo.addItem("Choose…", None)
            for option in resolution_options(issues):
                combo.addItem(resolution_label(option), option.value)
            combo.currentIndexChanged.connect(self.update_apply_button)
            self.table.setCellWidget(row, 4, combo)
            self.rows.append((operation_id, combo))

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.apply_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.apply_button.setText("Apply Resolutions")
        self.apply_button.setEnabled(False)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    @property
    def choices(self) -> dict[str, ResolutionChoice]:
        return {
            operation_id: ResolutionChoice(combo.currentData())
            for operation_id, combo in self.rows
            if combo.currentData() is not None
        }

    def update_apply_button(self, *_args) -> None:
        self.apply_button.setEnabled(
            bool(self.rows)
            and all(combo.currentData() is not None for _, combo in self.rows)
        )


def resolution_label(choice: ResolutionChoice) -> str:
    return {
        ResolutionChoice.OVERWRITE: "Overwrite target",
        ResolutionChoice.AUTO_RENAME: "Auto Rename",
        ResolutionChoice.SKIP: "Skip operation",
    }[choice]
