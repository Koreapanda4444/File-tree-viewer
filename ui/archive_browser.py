from __future__ import annotations

import time
import zipfile
from pathlib import Path, PurePosixPath

from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTreeView,
    QVBoxLayout,
)

from archive import extract_members, index_archive, read_member
from file_preview import preview_bytes
from fs_ops import require_unchanged
from fs_paths import validate_windows_name
from operation_history import record_events
from real.preview import decode_preview_image
from snapshot import SnapshotCancelled
from snapshot_store import check_cancelled
from ui.i18n import tr
from ui.image_preview import ImagePreview
from ui.tasks import CancellableWorker, prepare_worker_thread
from virtual.storage import format_size
from write_policy import policy, writes_allowed

INVALID = QModelIndex()
METHODS = {0: "Stored", 8: "Deflate", 12: "BZIP2", 14: "LZMA", 93: "Zstandard"}


class ArchiveModel(QAbstractItemModel):
    def __init__(self, archive, parent=None, *, folders_only=False):
        super().__init__(parent)
        self.archive = archive
        self.folders_only = folders_only
        self.children = {
            key: tuple(
                child
                for child in values
                if not folders_only or archive.entries[child].directory
            )
            for key, values in archive.children.items()
        }
        self.rows = {
            child: row
            for values in self.children.values()
            for row, child in enumerate(values)
        }

    def key(self, index):
        return index.internalPointer().path if index.isValid() else None

    def index_for(self, key):
        if key == "":
            return self.createIndex(0, 0, self.archive.entries[""])
        if key not in self.rows:
            return INVALID
        return self.createIndex(self.rows[key], 0, self.archive.entries[key])

    def rowCount(self, parent=INVALID):
        if parent.column() > 0:
            return 0
        return (
            1 if not parent.isValid() else len(self.children.get(self.key(parent), ()))
        )

    def columnCount(self, parent=INVALID):
        return 1 if self.folders_only else 5

    def index(self, row, column, parent=INVALID):
        if row < 0 or not 0 <= column < self.columnCount() or parent.column() > 0:
            return INVALID
        if not parent.isValid():
            return (
                self.createIndex(0, column, self.archive.entries[""])
                if row == 0
                else INVALID
            )
        values = self.children.get(self.key(parent), ())
        return (
            self.createIndex(row, column, self.archive.entries[values[row]])
            if row < len(values)
            else INVALID
        )

    def parent(self, index):
        key = self.key(index)
        return INVALID if not key else self.index_for(self.archive.entries[key].parent)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        key = self.key(index)
        if key is None:
            return None
        entry = self.archive.entries[key]
        if role == Qt.ItemDataRole.DisplayRole:
            values = (
                entry.name or self.archive.path.name,
                tr("Folder") if entry.directory else PurePosixPath(entry.path).suffix,
                "—" if entry.directory else format_size(entry.size),
                "—" if entry.directory else format_size(entry.compressed),
                tr(METHODS.get(entry.method, "Unknown"))
                if entry.method is not None
                else "—",
            )
            return values[index.column()]
        if role == Qt.ItemDataRole.ToolTipRole:
            return key + ("\n" + tr(entry.blocked) if entry.blocked else "")
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
        ):
            return tr(
                ("Name", "Type", "Original size", "Compressed size", "Compression")[
                    section
                ]
            )
        return None


class ArchiveIndexWorker(CancellableWorker):
    progress = Signal(int, int)
    finished = Signal(object, str)

    def __init__(self, path):
        super().__init__()
        self.path = path

    def run(self):
        try:
            result = index_archive(
                self.path, cancelled=self._cancelled, progress=self.progress.emit
            )
        except SnapshotCancelled:
            self.finished.emit(None, tr("ZIP indexing cancelled"))
        except (
            OSError,
            ValueError,
            RuntimeError,
            zipfile.BadZipFile,
            UnicodeError,
        ) as error:
            self.finished.emit(None, str(error))
        else:
            self.finished.emit(result, "")


class ArchiveDialog(QDialog):
    filesystem_changed = Signal(object)

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = path
        self.archive = None
        self.thread = self.worker = None
        self.outcome = None
        self.closing = False
        self.current_folder = ""
        self.operation = "index"
        self.preview_thread = self.preview_worker = None
        self.preview_outcome = None
        self.preview_generation = 0
        self.pending_preview_key = None
        self.stale = False
        self.setWindowTitle(tr("ZIP Explorer") + " — " + path.name)
        self.resize(1120, 760)
        layout = QVBoxLayout(self)
        self.summary = QLabel(tr("Reading ZIP directory…"))
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        row = QHBoxLayout()
        self.up = QPushButton(tr("Up"))
        self.up.clicked.connect(self.go_up)
        self.location = QLineEdit("/")
        self.location.setPlaceholderText(tr("Path inside ZIP"))
        self.location.returnPressed.connect(
            lambda: self.navigate(self.location.text().strip("/"))
        )
        row.addWidget(self.up)
        row.addWidget(self.location, 1)
        layout.addLayout(row)
        splitter = QSplitter()
        self.tree = QTreeView()
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumWidth(180)
        self.files = QTreeView()
        self.files.setRootIsDecorated(False)
        self.files.setItemsExpandable(False)
        self.files.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.files.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.files.setAlternatingRowColors(True)
        self.files.doubleClicked.connect(self.open_entry)
        splitter.addWidget(self.tree)
        splitter.addWidget(self.files)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([230, 850])
        layout.addWidget(splitter, 2)
        self.details = QLabel(tr("Choose an item to inspect ZIP metadata."))
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.details)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText(
            tr("Choose a file inside the ZIP to preview it.")
        )
        layout.addWidget(self.preview, 1)
        self.image = ImagePreview()
        self.image.buttons["open"].hide()
        self.image.hide()
        layout.addWidget(self.image, 1)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        layout.addWidget(self.bar)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.controls = QHBoxLayout()
        self.cancel = QPushButton(tr("Cancel"))
        self.cancel.clicked.connect(self.cancel_task)
        close = QPushButton(tr("Close"))
        close.clicked.connect(self.reject)
        self.extract_selection = QPushButton(tr("Extract selection…"))
        self.extract_all = QPushButton(tr("Extract all…"))
        self.external = QPushButton(tr("Open ZIP externally"))
        self.extract_selection.clicked.connect(lambda: self.choose_extraction(False))
        self.extract_all.clicked.connect(lambda: self.choose_extraction(True))
        self.external.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.path)))
        )
        for button in (self.extract_selection, self.extract_all, self.external):
            self.controls.addWidget(button)
        self.controls.addStretch()
        self.controls.addWidget(self.cancel)
        self.controls.addWidget(close)
        layout.addLayout(self.controls)
        self.source_timer = QTimer(self)
        self.source_timer.setInterval(1000)
        self.source_timer.timeout.connect(self.check_source)
        self.source_timer.start()
        self.refresh_write_access()
        self.start_index()

    @property
    def mutation_running(self):
        return self.operation == "extract" and self.thread is not None

    def start_index(self):
        self.worker = ArchiveIndexWorker(self.path)
        self.worker.progress.connect(
            lambda done, total: self.status.setText(
                tr("Reading ZIP entries: {done:,} / {total:,}", done=done, total=total)
            )
        )
        self.worker.finished.connect(self.receive)
        self.thread = prepare_worker_thread(
            self, self.worker, on_finished=self.task_finished
        )
        self.thread.start()

    def receive(self, result, error):
        self.outcome = result, error

    def task_finished(self):
        self.thread = self.worker = None
        self.cancel.setEnabled(False)
        self.bar.hide()
        result, error = self.outcome or (None, tr("ZIP task failed"))
        if self.operation == "extract":
            self.status.setText(
                error if result is None else tr("ZIP extracted to {path}", path=result)
            )
            if result is not None:
                self.filesystem_changed.emit(result.parent)
            self.refresh_write_access()
            if self.closing:
                self.done(QDialog.DialogCode.Rejected)
            return
        if result is None:
            self.status.setText(error)
        else:
            self.archive = result
            self.folder_model = ArchiveModel(result, self, folders_only=True)
            self.file_model = ArchiveModel(result, self)
            self.tree.setModel(self.folder_model)
            self.files.setModel(self.file_model)
            self.tree.selectionModel().currentChanged.connect(
                lambda index, previous: self.navigate(self.folder_model.key(index))
            )
            self.files.selectionModel().currentChanged.connect(self.selection_changed)
            self.files.selectionModel().selectionChanged.connect(
                lambda *_: self.refresh_write_access()
            )
            self.files.setColumnWidth(0, 300)
            self.files.setColumnWidth(1, 100)
            self.files.setColumnWidth(2, 120)
            self.files.setColumnWidth(3, 120)
            self.tree.expand(self.folder_model.index_for(""))
            self.summary.setText(
                tr(
                    "{name} · ZIP {size} · {count:,} entries · original {total}",
                    name=result.path.name,
                    size=format_size(result.compressed_size),
                    count=result.members,
                    total=format_size(result.total_size),
                )
            )
            self.status.setText(
                tr("ZIP browsing is read-only. The original archive is never modified.")
            )
            self.navigate("")
        self.refresh_write_access()
        if self.closing:
            self.done(QDialog.DialogCode.Rejected)

    def navigate(self, key):
        if (
            self.archive is None
            or key not in self.archive.entries
            or not self.archive.entries[key].directory
        ):
            self.status.setText(tr("Folder not found inside ZIP"))
            return
        self.current_folder = key
        self.location.setText("/" + key)
        self.files.setRootIndex(self.file_model.index_for(key))
        self.up.setEnabled(bool(key))
        self.preview_generation += 1
        self.pending_preview_key = None
        if self.preview_worker is not None:
            self.preview_worker.cancel()
        self.preview.clear()
        self.image.clear()
        self.image.hide()
        self.preview.show()
        self.refresh_write_access()

    def go_up(self):
        self.navigate(self.current_folder.rpartition("/")[0])

    def open_entry(self, index):
        key = self.file_model.key(index)
        if key is not None and self.archive.entries[key].directory:
            self.navigate(key)

    def selected_keys(self):
        selection = self.files.selectionModel()
        return (
            [self.file_model.key(index) for index in selection.selectedRows(0)]
            if self.archive is not None and selection is not None
            else []
        )

    def selection_changed(self, index, previous=INVALID):
        key = self.file_model.key(index)
        if key is None:
            return
        entry = self.archive.entries[key]
        self.details.setText(
            tr(
                "{path}\nOriginal: {size} · Compressed: {compressed} · CRC: {crc}",
                path=entry.path,
                size=format_size(entry.size),
                compressed=format_size(entry.compressed),
                crc=f"{entry.crc:08X}" if entry.crc is not None else "—",
            )
            + ("\n" + tr(entry.blocked) if entry.blocked else "")
        )

        self.refresh_write_access()
        if not entry.directory:
            self.request_preview(key)
        else:
            self.preview_generation += 1
            self.pending_preview_key = None
            if self.preview_worker is not None:
                self.preview_worker.cancel()
            self.preview.clear()
            self.image.clear()
            self.image.hide()
            self.preview.show()

    def request_preview(self, key):
        self.preview_generation += 1
        self.pending_preview_key = key
        self.preview.clear()
        self.preview.show()
        self.image.clear()
        self.image.hide()
        if self.preview_worker is not None:
            self.preview_worker.cancel()
        self.status.setText(tr("Loading ZIP preview…"))
        if self.preview_thread is None:
            self.start_preview()

    def start_preview(self):
        key = self.pending_preview_key
        if key is None or self.closing or self.stale:
            return
        self.preview_outcome = None
        self.preview_worker = ArchivePreviewWorker(
            self.archive, key, self.preview_generation
        )
        self.preview_worker.finished.connect(
            lambda result, error: setattr(self, "preview_outcome", (result, error))
        )
        self.preview_thread = prepare_worker_thread(
            self, self.preview_worker, on_finished=self.preview_finished
        )
        self.preview_thread.start()

    def preview_finished(self):
        self.preview_thread = self.preview_worker = None
        outcome = self.preview_outcome
        if outcome is not None:
            (generation, _key, result, crc_checked), error = outcome
            if (
                generation == self.preview_generation
                and not self.closing
                and not self.stale
            ):
                self.pending_preview_key = None
                if result is None:
                    self.status.setText(error or tr("ZIP preview cancelled"))
                elif result.image is not None:
                    self.preview.hide()
                    self.image.set_image(result.image, result.image_original_size)
                    self.image.show()
                    self.status.setText(
                        tr("ZIP preview CRC verified")
                        if crc_checked
                        else tr("Truncated ZIP preview; CRC not verified")
                    )
                else:
                    self.preview.setPlainText(result.text)
                    self.status.setText(
                        tr("ZIP preview CRC verified")
                        if crc_checked
                        else tr("Truncated ZIP preview; CRC not verified")
                    )
        if self.closing:
            self.done(QDialog.DialogCode.Rejected)
        elif self.pending_preview_key is not None:
            self.start_preview()

    def check_source(self):
        if self.archive is None or self.stale:
            return
        try:
            require_unchanged(self.path, self.archive.signature)
        except OSError:
            self.stale = True
            self.preview_generation += 1
            self.pending_preview_key = None
            if self.preview_worker is not None:
                self.preview_worker.cancel()
            self.preview.clear()
            self.image.clear()
            self.image.hide()
            self.preview.show()
            self.status.setText(tr("ZIP changed or was removed. Close and reopen it."))
            self.refresh_write_access()

    def refresh_write_access(self):
        ready = self.archive is not None and self.thread is None and not self.stale
        self.extract_selection.setEnabled(
            ready and not policy.read_only and bool(self.selected_keys())
        )
        self.extract_all.setEnabled(ready and not policy.read_only)

    def choose_extraction(self, all_entries):
        if (
            not writes_allowed(self)
            or self.thread is not None
            or self.archive is None
            or self.stale
        ):
            return
        keys = [""] if all_entries else self.selected_keys()
        if not keys:
            return
        parent = QFileDialog.getExistingDirectory(
            self, tr("Choose extraction parent folder")
        )
        if not parent:
            return
        name, accepted = QInputDialog.getText(
            self,
            tr("Extract ZIP"),
            tr("New output folder name (existing folders are preserved):"),
            text=self.path.stem or "extracted",
        )
        if accepted and name:
            try:
                validate_windows_name(name)
                if name in {".", ".."} or "/" in name or "\\" in name:
                    raise ValueError(tr("Enter one new folder name"))
                self.start_extraction(keys, Path(parent) / name)
            except ValueError as error:
                self.status.setText(str(error))

    def start_extraction(self, keys, destination):
        if (
            not writes_allowed(self)
            or self.thread is not None
            or self.archive is None
            or self.stale
            or self.closing
        ):
            return False
        self.operation = "extract"
        self.outcome = None
        self.worker = ArchiveExtractWorker(self.archive, keys, destination)
        self.worker.progress.connect(self.extraction_progress)
        self.worker.finished.connect(self.receive)
        self.thread = prepare_worker_thread(
            self, self.worker, on_finished=self.task_finished
        )
        self.cancel.setEnabled(True)
        self.bar.show()
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)
        self.refresh_write_access()
        self.thread.start()
        return True

    def extraction_progress(self, done, total, name):
        self.bar.setValue(int(done * 1000 / total) if total else 1000)
        self.status.setText(
            tr(
                "Extracting {name}: {done:,} / {total:,} bytes",
                name=name,
                done=done,
                total=total,
            )
        )

    def cancel_task(self):
        if self.worker is not None:
            self.worker.cancel()
            self.cancel.setEnabled(False)

    def done(self, result):
        if self.thread is not None or self.preview_thread is not None:
            self.closing = True
            self.pending_preview_key = None
            self.cancel_task()
            if self.preview_worker is not None:
                self.preview_worker.cancel()
            return
        self.source_timer.stop()
        self.image.clear()
        self.archive = None
        super().done(result)


class ArchivePreviewWorker(CancellableWorker):
    finished = Signal(object, str)

    def __init__(self, archive, key, generation):
        super().__init__()
        self.archive, self.key, self.generation = archive, key, generation

    def run(self):
        try:
            content = read_member(self.archive, self.key, cancelled=self._cancelled)
            result = preview_bytes(
                Path(self.key),
                content.data,
                size=content.size,
                truncated=content.truncated,
            )
            result = decode_preview_image(result)
            check_cancelled(self._cancelled)
        except SnapshotCancelled:
            self.finished.emit((self.generation, self.key, None, False), "")
        except (
            OSError,
            ValueError,
            RuntimeError,
            zipfile.BadZipFile,
            UnicodeError,
        ) as error:
            self.finished.emit((self.generation, self.key, None, False), str(error))
        else:
            self.finished.emit(
                (self.generation, self.key, result, content.crc_checked), ""
            )


class ArchiveExtractWorker(CancellableWorker):
    progress = Signal(object, object, str)
    finished = Signal(object, str)

    def __init__(self, archive, keys, destination):
        super().__init__()
        self.archive, self.keys, self.destination = archive, keys, destination
        self.last_progress = 0.0

    def report(self, done, total, name):
        now = time.monotonic()
        if done == total or now - self.last_progress >= 0.05:
            self.last_progress = now
            self.progress.emit(done, total, name)

    def run(self):
        try:
            result = extract_members(
                self.archive,
                self.keys,
                self.destination,
                cancelled=self._cancelled,
                progress=self.report,
            )
        except SnapshotCancelled:
            self.finished.emit(
                None, tr("ZIP extraction cancelled; temporary output was removed.")
            )
        except (
            OSError,
            ValueError,
            RuntimeError,
            zipfile.BadZipFile,
            UnicodeError,
        ) as error:
            self.finished.emit(None, str(error))
        else:
            record_events(
                [
                    {
                        "category": "archive",
                        "action": "extract",
                        "status": "success",
                        "source": self.archive.path,
                        "target": result,
                    }
                ]
            )
            self.finished.emit(result, "")
