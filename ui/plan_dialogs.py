from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QSplitter,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from plan_dependencies import analyze_dependencies
from planning import FilePlan, PlanAction, PlanOperation
from snapshot import FileSnapshot
from ui.i18n import tr
from ui.plan_models import SnapshotTreeModel
from ui.widgets import PlainLabel as QLabel


def create_dependencies_dialog(plan: FilePlan, parent: QWidget) -> QDialog:
    report = analyze_dependencies(plan)
    positions = {op.operation_id: index + 1 for index, op in enumerate(plan.operations)}
    lines = [
        "Move sources are staged first. Deletes, folders, moves, renames and files are then processed in execution groups.",
        "",
        "Execution order: "
        + ", ".join(str(positions[key]) for key in report.execution_order[:500]),
    ]
    for edge in report.edges[:500]:
        lines.append(
            f"Step {positions[edge.operation_id]} requires step {positions[edge.requires_id]}: {edge.reason}"
        )
    for cycle in report.cycles[:100]:
        lines.append(
            ("Staged move cycle: " if cycle.staged else "Blocked dependency cycle: ")
            + ", ".join(str(positions[key]) for key in cycle.operation_ids)
        )
    lines.extend(problem.message for problem in report.problems[:100])
    dialog = QDialog(parent)
    dialog.setWindowTitle(tr("Plan Dependencies and Execution Order"))
    dialog.resize(900, 600)
    layout = QVBoxLayout(dialog)
    from PySide6.QtWidgets import QPlainTextEdit

    text = QPlainTextEdit()
    text.setReadOnly(True)
    text.setPlainText("\n".join(lines))
    layout.addWidget(text)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    return dialog


def create_operation_dialog(
    plan: FilePlan, operation: PlanOperation, parent: QWidget
) -> QDialog:
    dialog = QDialog(parent)
    dialog.setWindowTitle(tr("Edit Planned Operation"))
    layout = QFormLayout(dialog)
    action = QComboBox()
    for item in PlanAction:
        action.addItem(item.value.replace("_", " ").title(), item)
    action.setCurrentIndex(action.findData(operation.action))
    source = QLineEdit(str(operation.source) if operation.source else "")
    target = QLineEdit(str(operation.target) if operation.target else "")
    layout.addRow(tr("Action"), action)
    layout.addRow(tr("Source relative path"), source)
    layout.addRow(tr("Target relative path"), target)

    def update_fields():
        source.setEnabled(
            action.currentData()
            in {
                PlanAction.COPY,
                PlanAction.MOVE,
                PlanAction.RENAME,
                PlanAction.DELETE,
            }
        )
        target.setEnabled(action.currentData() is not PlanAction.DELETE)

    action.currentIndexChanged.connect(update_fields)
    update_fields()
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
    )

    def save():
        try:
            plan.update_operation(
                operation.operation_id,
                action=action.currentData(),
                source=source.text() if source.isEnabled() else None,
                target=target.text() if target.isEnabled() else None,
            )
        except (ValueError, KeyError, TypeError) as error:
            QMessageBox.warning(dialog, tr("Invalid operation"), str(error))
        else:
            dialog.accept()

    buttons.accepted.connect(save)
    buttons.rejected.connect(dialog.reject)
    layout.addRow(buttons)
    return dialog


def create_structure_dialog(
    before: FileSnapshot, after: FileSnapshot, parent: QWidget
) -> QDialog:
    dialog = QDialog(parent)
    dialog.setWindowTitle(tr("Planned Folder Structure — original files are unchanged"))
    dialog.resize(1150, 720)
    layout = QVBoxLayout(dialog)
    layout.addWidget(
        QLabel(
            tr(
                "Before: {value0:,} items · After: {value1:,} items",
                value0=before.entry_count,
                value1=after.entry_count,
            )
        )
    )
    split = QSplitter(Qt.Orientation.Horizontal)
    models = []
    for title, snapshot in (("Before", before), ("After Plan", after)):
        panel = QWidget()
        column = QVBoxLayout(panel)
        column.addWidget(QLabel(title))
        tree = QTreeView()
        tree.setUniformRowHeights(True)
        model = SnapshotTreeModel(snapshot, tree)
        models.append(model)
        tree.setModel(model)
        tree.setDragEnabled(False)
        tree.setAcceptDrops(False)
        tree.setColumnWidth(0, 300)
        index = model.index(0, 0)
        model.fetchMore(index)
        tree.expand(index)
        column.addWidget(tree)
        split.addWidget(panel)
    layout.addWidget(split)
    dialog._snapshot_models = models
    return dialog
