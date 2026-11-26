"""Read-only Quick Look with the same bounded decoder as the Real preview."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from real.preview import localized_preview_text
from ui.i18n import tr
from ui.image_preview import ImagePreview
from ui.preview_session import PreviewSession
from ui.widgets import PlainLabel
from virtual.storage import format_size


class QuickLookDialog(QDialog):
    navigate_requested = Signal(int)

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.closing = False
        self.path = path
        self.setWindowTitle(tr("Quick Look"))
        self.resize(900, 650)
        layout = QVBoxLayout(self)
        self.title = PlainLabel()
        self.title.setWordWrap(True)
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.title)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        layout.addWidget(self.text, 1)
        self.image = ImagePreview()
        self.image.buttons["open"].hide()
        self.image.hide()
        layout.addWidget(self.image, 1)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        layout.addWidget(self.bar)
        self.status = PlainLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.previous = QPushButton(tr("Previous"))
        self.next = QPushButton(tr("Next"))
        self.previous.clicked.connect(lambda: self.navigate_requested.emit(-1))
        self.next.clicked.connect(lambda: self.navigate_requested.emit(1))
        self.refresh = QPushButton(tr("Refresh"))
        self.refresh.clicked.connect(lambda: self.set_path(self.path))
        self.external = QPushButton(tr("Open externally"))
        self.external.clicked.connect(self.open_external)
        close = QPushButton(tr("Close"))
        close.clicked.connect(self.reject)
        for button in (self.previous, self.next, self.refresh, self.external):
            button.setAutoDefault(False)
            row.addWidget(button)
        row.addStretch()
        row.addWidget(close)
        layout.addLayout(row)
        self.session = PreviewSession(self)
        self.session.ready.connect(self.display)
        self.session.invalidated.connect(self.clear_preview)
        self.session.status_changed.connect(self.show_status)
        self.session.stopped.connect(self.finish_close)
        QApplication.instance().installEventFilter(self)
        self.set_path(path)

    def set_path(self, path):
        if self.closing:
            return
        if path is None:
            self.clear_selection()
            return
        self.path = path
        self.refresh.setEnabled(True)
        self.previous.setEnabled(True)
        self.next.setEnabled(True)
        self.title.setText(str(path))
        self.setWindowTitle(tr("Quick Look") + " — " + path.name)
        self.bar.show()
        self.external.setEnabled(False)
        self.session.request(path)

    def clear_selection(self, path=None):
        self.path = None
        self.title.setText(
            str(path) if path is not None else tr("Select a file to preview it")
        )
        self.session.clear_selection()
        self.bar.hide()
        self.refresh.setEnabled(False)
        self.previous.setEnabled(False)
        self.next.setEnabled(False)

    def clear_preview(self):
        self.text.clear()
        self.text.show()
        self.image.clear()
        self.image.hide()
        self.external.setEnabled(False)

    def show_status(self, message):
        self.status.setText(message)
        if self.session.thread is None:
            self.bar.hide()

    def display(self, result):
        self.bar.hide()
        self.title.setText(
            tr(
                "{name} · {kind} · {size}\n{path}",
                name=result.path.name,
                kind=result.path.suffix or tr("File"),
                size=format_size(result.size),
                path=result.path,
            )
        )
        self.external.setEnabled(True)
        if result.image is not None:
            self.text.hide()
            self.image.set_image(result.image, result.image_original_size)
            self.image.show()
        else:
            self.text.setPlainText(localized_preview_text(result.text))
        self.status.setText(
            tr("PDF · first page only")
            if result.text == "PDF · first page only"
            else tr("Partial preview; the file exceeds the preview limit")
            if result.truncated
            else tr("Read-only preview")
        )

    def open_external(self):
        self.session.check_source()
        if self.session.result is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.path)))

    def eventFilter(self, watched, event):
        if (
            not self.isVisible()
            or not isinstance(watched, QWidget)
            or not (watched is self or self.isAncestorOf(watched))
        ):
            return False
        keys = {
            Qt.Key.Key_Space,
            Qt.Key.Key_Escape,
            Qt.Key.Key_Left,
            Qt.Key.Key_Right,
            Qt.Key.Key_Up,
            Qt.Key.Key_Down,
        }
        if (
            event.type() == QEvent.Type.ShortcutOverride
            and event.key() in keys
            and not event.modifiers()
        ):
            event.accept()
            return True
        if (
            event.type() == QEvent.Type.KeyPress
            and event.key() in keys
            and not event.modifiers()
        ):
            if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Escape):
                if not event.isAutoRepeat():
                    self.reject()
            else:
                self.navigate_requested.emit(
                    -1 if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Up) else 1
                )
            return True
        return False

    def done(self, result):
        if not self.closing:
            self.closing = True
            self.session.stop()
        elif self.session.thread is None:
            self.finish_close()

    def finish_close(self):
        if not self.closing or self.session.thread is not None:
            return
        QApplication.instance().removeEventFilter(self)
        self.clear_preview()
        super().done(QDialog.DialogCode.Rejected)
