from __future__ import annotations

import re
import time
from pathlib import Path

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from file_hashing import hash_file
from snapshot import SnapshotCancelled
from ui.i18n import tr
from ui.tasks import CancellableWorker, prepare_worker_thread


class FileHashWorker(CancellableWorker):
    progress = Signal(str, object, object)
    finished = Signal(object, str)

    def __init__(self, paths):
        super().__init__()
        self.paths = paths
        self.last_progress = 0.0

    def report(self, path, done, total):
        now = time.monotonic()
        if done in (0, total) or now - self.last_progress >= 0.05:
            self.last_progress = now
            self.progress.emit(path.name, done, total)

    def run(self):
        try:
            results = []
            for path in self.paths:
                results.append(
                    hash_file(
                        path,
                        cancelled=self._cancelled,
                        progress=lambda done, total, p=path: self.report(
                            p, done, total
                        ),
                    )
                )
            for result in results:
                result.verify()
        except SnapshotCancelled:
            self.finished.emit(None, tr("Hash calculation cancelled"))
        except (OSError, ValueError, RuntimeError) as error:
            self.finished.emit(None, str(error))
        else:
            self.finished.emit(results, "")


class FileHashDialog(QDialog):
    def __init__(self, paths=(), parent=None):
        super().__init__(parent)
        self.thread = self.worker = None
        self.outcome = None
        self.results = []
        self.closing = False
        self.setWindowTitle(tr("File Hash — SHA-256"))
        self.resize(760, 480)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.paths, self.choose_buttons = [], []
        for index, label in enumerate(("File", "Compare with file (optional)")):
            row = QHBoxLayout()
            field = QLineEdit(str(paths[index]) if index < len(paths) else "")
            field.setReadOnly(True)
            button = QPushButton(tr("Choose…"))
            button.clicked.connect(lambda checked=False, i=index: self.choose(i))
            row.addWidget(field, 1)
            row.addWidget(button)
            form.addRow(tr(label), row)
            self.paths.append(field)
            self.choose_buttons.append(button)
        self.expected = QLineEdit()
        self.expected.setPlaceholderText(tr("64 hexadecimal characters (optional)"))
        self.expected.textChanged.connect(self.compare)
        form.addRow(tr("Expected SHA-256"), self.expected)
        layout.addLayout(form)
        row = QHBoxLayout()
        self.calculate = QPushButton(tr("Calculate SHA-256"))
        self.calculate.clicked.connect(self.start)
        self.copy = QPushButton(tr("Copy Hash"))
        self.copy.setEnabled(False)
        self.copy.clicked.connect(self.copy_hash)
        self.cancel = QPushButton(tr("Cancel"))
        self.cancel.setEnabled(False)
        self.cancel.clicked.connect(self.cancel_calculation)
        self.clear_other = QPushButton(tr("Clear comparison file"))
        self.clear_other.clicked.connect(self.clear_comparison)
        for button in (self.calculate, self.copy, self.cancel, self.clear_other):
            row.addWidget(button)
        layout.addLayout(row)
        self.status = QLabel(tr("Choose a file and calculate its SHA-256."))
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        layout.addWidget(self.bar)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        layout.addWidget(self.output, 1)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.validate_results)
        self.timer.start()

    def choose(self, index):
        if self.thread is not None:
            return
        name, _ = QFileDialog.getOpenFileName(self, tr("Choose file"))
        if name:
            self.paths[index].setText(name)
            self.invalidate()

    def clear_comparison(self):
        if self.thread is None:
            self.paths[1].clear()
            self.invalidate()

    def invalidate(self, message=None):
        self.results = []
        self.copy.setEnabled(False)
        self.output.clear()
        self.status.setText(message or tr("Choose a file and calculate its SHA-256."))

    def start(self):
        if self.thread is not None or not self.paths[0].text():
            return
        paths = [Path(field.text()) for field in self.paths if field.text()]
        self.invalidate(tr("Calculating SHA-256…"))
        self.outcome = None
        worker = self.worker = FileHashWorker(paths)
        worker.progress.connect(self.progress)
        worker.finished.connect(self.receive)
        self.thread = prepare_worker_thread(
            self, worker, on_finished=self.finished_calculation
        )
        self.calculate.setEnabled(False)
        self.cancel.setEnabled(True)
        for button in (*self.choose_buttons, self.clear_other):
            button.setEnabled(False)
        self.bar.setValue(0)
        self.thread.start()

    def progress(self, name, done, total):
        self.status.setText(
            tr(
                "Hashing {name}: {done:,} / {total:,} bytes",
                name=name,
                done=done,
                total=total,
            )
        )
        self.bar.setValue(int(done * 1000 / total) if total else 1000)

    def receive(self, results, error):
        self.outcome = results, error

    def finished_calculation(self):
        self.thread = self.worker = None
        self.calculate.setEnabled(True)
        self.cancel.setEnabled(False)
        for button in (*self.choose_buttons, self.clear_other):
            button.setEnabled(True)
        if self.outcome is not None:
            results, error = self.outcome
            self.results = results or []
            if self.results:
                self.output.setPlainText(
                    "\n\n".join(
                        str(r.path) + "\nSHA-256: " + r.digest for r in self.results
                    )
                )
                self.bar.setValue(1000)
                self.copy.setEnabled(True)
                self.compare()
            else:
                self.invalidate(error or tr("Hash calculation failed"))
        else:
            self.invalidate(tr("Hash calculation failed"))
        if self.closing:
            self.done(QDialog.DialogCode.Rejected)

    def validate_results(self):
        if not self.results:
            return False
        try:
            for result in self.results:
                result.verify()
        except OSError:
            self.invalidate(
                tr(
                    "A file changed after hashing. Recalculate before comparing or copying."
                )
            )
            return False
        return True

    def compare(self):
        if not self.validate_results():
            return
        messages = (
            [
                tr("File hashes match")
                if self.results[0].digest == self.results[1].digest
                else tr("File hashes do not match")
            ]
            if len(self.results) == 2
            else []
        )
        expected = self.expected.text().strip()
        if expected:
            if not re.fullmatch(r"[a-fA-F0-9]{64}", expected):
                messages.append(tr("Enter exactly 64 hexadecimal characters."))
            else:
                messages.append(
                    tr("Expected hash matches")
                    if self.results[0].digest == expected.lower()
                    else tr("Expected hash does not match")
                )
        self.status.setText(
            "\n".join(messages)
            if messages
            else tr("SHA-256 verified for the file state at calculation time.")
        )

    def copy_hash(self):
        if self.validate_results():
            QApplication.clipboard().setText(self.results[0].digest)

    def cancel_calculation(self):
        if self.worker is not None:
            self.worker.cancel()
            self.cancel.setEnabled(False)

    def done(self, result):
        if self.thread is not None:
            self.closing = True
            self.cancel_calculation()
            return
        self.timer.stop()
        self.results = []
        super().done(result)
