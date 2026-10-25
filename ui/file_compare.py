from __future__ import annotations

from PySide6.QtCore import QTimer, Signal, Slot
from PySide6.QtGui import QTextCursor, QTextFormat
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QTextEdit,
    QVBoxLayout,
)

from file_comparison import TextComparison, compare_text_files
from file_content import ContentCancelled
from ui.tasks import CancellableWorker, prepare_worker_thread
from ui.theme import difference_background


class TextComparisonWorker(CancellableWorker):
    finished = Signal(object, str)

    def __init__(self, left, right) -> None:
        super().__init__()
        self.left = left
        self.right = right

    @Slot()
    def run(self) -> None:
        try:
            result = compare_text_files(
                self.left, self.right, cancelled=self._cancelled
            )
        except ContentCancelled:
            self.finished.emit(None, "Comparison cancelled")
        except (OSError, ValueError, RuntimeError, UnicodeError) as error:
            self.finished.emit(None, str(error))
        else:
            self.finished.emit(result, "")


class FileComparisonDialog(QDialog):
    def __init__(self, left, right, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Side-by-side Text Comparison")
        self.resize(1200, 780)
        self.thread = None
        self.worker = TextComparisonWorker(left, right)
        self.outcome = None
        self.close_requested = False
        layout = QVBoxLayout(self)
        self.summary = QLabel("Comparing files…")
        layout.addWidget(self.summary)
        row = QHBoxLayout()
        self.editors = []
        for path in (left, right):
            column = QVBoxLayout()
            label = QLabel(str(path))
            label.setWordWrap(True)
            column.addWidget(label)
            editor = QPlainTextEdit()
            editor.setReadOnly(True)
            editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            self.editors.append(editor)
            column.addWidget(editor)
            row.addLayout(column)
        layout.addLayout(row, 1)
        for first, second in ((0, 1), (1, 0)):
            self.editors[first].verticalScrollBar().valueChanged.connect(
                self.editors[second].verticalScrollBar().setValue
            )
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        QTimer.singleShot(0, self.start)

    def start(self) -> None:
        if self.close_requested:
            return
        self.worker.finished.connect(self.receive)
        self.thread = prepare_worker_thread(
            self, self.worker, on_finished=self.completed
        )
        self.thread.start()

    def receive(self, result, error) -> None:
        self.outcome = result, error

    def completed(self) -> None:
        self.thread = None
        if self.close_requested:
            super().reject()
            return
        result, error = self.outcome or (None, "Comparison returned no result")
        if error or not isinstance(result, TextComparison):
            self.summary.setText(error)
            return
        rows = list(result.rows())
        for side, editor in enumerate(self.editors):
            editor.setPlainText(
                "\n".join(
                    f"{row[side * 2]:>6} | {row[side * 2 + 1]}"
                    if row[side * 2] is not None
                    else "       |"
                    for row in rows
                )
            )
            selections = []
            for index, row in enumerate(rows):
                if row[4] == "equal":
                    continue
                selection = QTextEdit.ExtraSelection()
                selection.cursor = QTextCursor(
                    editor.document().findBlockByNumber(index)
                )
                selection.format.setProperty(
                    QTextFormat.Property.FullWidthSelection, True
                )

                selection.format.setBackground(
                    difference_background(
                        "replace"
                        if row[4] == "replace"
                        else "delete"
                        if side == 0
                        else "insert"
                    )
                )
                selections.append(selection)
            editor.setExtraSelections(selections)
        changed = sum(tag != "equal" for tag, *_ in result.opcodes)
        partial = (
            " · Partial comparison (byte or line limit reached)"
            if result.left.truncated or result.right.truncated
            else ""
        )
        self.summary.setText(
            f"{changed:,} changed region(s) · {result.left.encoding} / {result.right.encoding}{partial}"
        )

    def reject(self) -> None:
        self.close_requested = True
        if self.thread is not None:
            self.worker.cancel()
            self.summary.setText("Cancelling comparison…")
            return
        super().reject()

    def closeEvent(self, event) -> None:
        if self.thread is not None:
            self.reject()
            event.ignore()
        else:
            event.accept()
