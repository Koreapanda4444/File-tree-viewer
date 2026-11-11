from __future__ import annotations

import json
import sqlite3
from itertools import islice

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from snapshot import SnapshotCancelled
from tree_export import TreeExportOptions, build_tree, tree_lines, write_tree
from ui.i18n import add_choice, tr
from ui.tasks import CancellableWorker, prepare_worker_thread
from ui.widgets import PlainLabel


class TreeExportWorker(CancellableWorker):
    finished = Signal(object, str)
    progress = Signal(int)

    def __init__(
        self, source, kind, options, *, document=None, destination=None, format="txt"
    ):
        super().__init__()
        self.source, self.kind, self.options = source, kind, options
        self.document, self.destination, self.format = document, destination, format
        self.snapshot = source if kind == "snapshot" else None

    def run(self):
        try:
            result = (
                write_tree(
                    self.document,
                    self.destination,
                    self.format,
                    cancelled=self._cancelled,
                )
                if self.destination is not None
                else build_tree(
                    self.source,
                    self.options,
                    kind=self.kind,
                    cancelled=self._cancelled,
                    progress=self.progress.emit,
                )
            )
        except SnapshotCancelled:
            self.finished.emit(None, "Export cancelled")
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
            self.finished.emit(None, str(error))
        else:
            self.finished.emit(result, "")


class TreeExportDialog(QDialog):
    def __init__(self, source, kind, parent=None):
        super().__init__(parent)
        self.source, self.kind = source, kind
        self.thread = self.worker = self.document = self.outcome = None
        self.closing = False
        self.setWindowTitle(tr("Export Folder Tree"))
        self.resize(820, 700)
        layout = QVBoxLayout(self)
        self.summary = PlainLabel(
            tr("Choose options and generate a preview. Existing files are preserved.")
        )
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        form = QFormLayout()
        self.format = QComboBox()
        for label, value in (
            ("Text (.txt)", "txt"),
            ("Markdown (.md)", "md"),
            ("JSON (.json)", "json"),
        ):
            add_choice(self.format, label, value)
        self.depth = QSpinBox()
        self.depth.setRange(0, 256)
        self.depth.setValue(8)
        self.depth.setSpecialValueText("All (up to 256 levels)")
        self.limit = QSpinBox()
        self.limit.setRange(1, 1_000_000)
        self.limit.setValue(100_000)
        self.hidden = QCheckBox(tr("Include hidden entries"))
        self.sizes = QCheckBox(tr("Include file sizes"))
        self.extensions, self.folders = QLineEdit(), QLineEdit()
        self.extensions.setPlaceholderText(tr(".pyc, .log"))
        self.folders.setPlaceholderText(tr("node_modules, __pycache__"))
        for label, widget in (
            ("Format", self.format),
            ("Maximum depth", self.depth),
            ("Entry limit", self.limit),
            ("Exclude extensions", self.extensions),
            ("Exclude folders", self.folders),
            ("", self.hidden),
            ("", self.sizes),
        ):
            form.addRow(tr(label), widget)
        layout.addLayout(form)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self.preview, 1)
        row = QHBoxLayout()
        self.generate = QPushButton(tr("Generate Preview"))
        self.copy = QPushButton(tr("Copy Tree"))
        self.save = QPushButton(tr("Save Export…"))
        self.cancel = QPushButton(tr("Cancel Task"))
        for button in (self.generate, self.copy, self.save, self.cancel):
            row.addWidget(button)
        layout.addLayout(row)
        self.generate.clicked.connect(self.start)
        self.copy.clicked.connect(self.copy_tree)
        self.save.clicked.connect(self.save_tree)
        self.cancel.clicked.connect(
            lambda: self.worker.cancel() if self.worker else None
        )
        self.format.currentIndexChanged.connect(self.render_preview)
        for widget in (self.depth, self.limit):
            widget.valueChanged.connect(self.invalidate)
        for widget in (self.extensions, self.folders):
            widget.textChanged.connect(self.invalidate)
        for widget in (self.hidden, self.sizes):
            widget.toggled.connect(self.invalidate)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.set_busy(False)

    def options(self):
        return TreeExportOptions(
            self.depth.value(),
            self.hidden.isChecked(),
            tuple(
                value.strip()
                for value in self.extensions.text().split(",")
                if value.strip()
            ),
            tuple(
                value.strip()
                for value in self.folders.text().split(",")
                if value.strip()
            ),
            self.sizes.isChecked(),
            self.limit.value(),
        )

    def invalidate(self, *args):
        self.document = None
        self.copy.setEnabled(False)
        self.save.setEnabled(False)
        self.preview.clear()

    def set_busy(self, busy):
        for widget in (
            self.generate,
            self.depth,
            self.limit,
            self.hidden,
            self.sizes,
            self.extensions,
            self.folders,
            self.format,
        ):
            widget.setEnabled(not busy)
        self.copy.setEnabled(not busy and self.document is not None)
        self.save.setEnabled(not busy and self.document is not None)
        self.cancel.setEnabled(busy)

    def start(self, checked=False, *, destination=None):
        if self.thread is not None:
            return
        self.outcome = None
        self.worker = TreeExportWorker(
            self.source,
            self.kind,
            self.options(),
            document=self.document,
            destination=destination,
            format=self.format.currentData(),
        )
        self.worker.finished.connect(self.receive)
        self.worker.progress.connect(
            lambda count: self.summary.setText(
                tr("Reading {value0:,} entries…", value0=count)
            )
        )
        self.thread = prepare_worker_thread(
            self, self.worker, on_finished=self.thread_finished
        )
        self.set_busy(True)
        self.thread.start()

    def receive(self, result, error):
        self.outcome = result, error

    def thread_finished(self):
        self.thread = self.worker = None
        result, error = self.outcome or (None, "Export stopped unexpectedly")
        if error:
            self.summary.setText(error)
        elif isinstance(result, dict):
            self.document = result
            self.summary.setText(
                tr(
                    "{value0:,} entries. Preview is limited to 1,000 lines / 40,000 characters.",
                    value0=result["entry_count"],
                )
            )
            self.render_preview()
        else:
            self.summary.setText(tr("Export saved: {value0}", value0=result))
        self.set_busy(False)
        if self.closing:
            super().reject()

    def render_preview(self, *args):
        if self.document is None:
            return
        if self.format.currentData() == "json":
            chunks, size = [], 0
            for chunk in json.JSONEncoder(ensure_ascii=False, indent=2).iterencode(
                self.document
            ):
                chunks.append(chunk)
                size += len(chunk)
                if size > 40_000:
                    break
            text = "".join(chunks)[:40_000]
        else:
            text = "\n".join(islice(tree_lines(self.document), 1000))[:40_000]
        self.preview.setPlainText(text)

    def copy_tree(self):
        if self.document is not None:
            text = (
                json.dumps(self.document, ensure_ascii=False, indent=2)
                if self.format.currentData() == "json"
                else "\n".join(tree_lines(self.document)) + "\n"
            )
            if len(text) > 16 * 1024 * 1024:
                self.summary.setText(
                    tr("Tree is too large for the clipboard; save an export instead")
                )
                return
            QApplication.clipboard().setText(text)
            self.summary.setText(tr("Tree copied to clipboard"))

    def save_tree(self):
        format = self.format.currentData()
        path, _ = QFileDialog.getSaveFileName(
            self, tr("Save Tree Export"), f"tree.{format}", f"Tree export (*.{format})"
        )
        if path:
            self.start(destination=path)

    def reject(self):
        if self.thread is not None:
            self.closing = True
            self.worker.cancel()
        else:
            super().reject()
