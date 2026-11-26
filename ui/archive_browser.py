from __future__ import annotations

import zipfile
from pathlib import PurePosixPath

from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTreeView,
    QVBoxLayout,
)

from archive import index_archive
from snapshot import SnapshotCancelled
from ui.i18n import tr
from ui.tasks import CancellableWorker, prepare_worker_thread
from virtual.storage import format_size

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
        self.controls.addStretch()
        self.controls.addWidget(self.cancel)
        self.controls.addWidget(close)
        layout.addLayout(self.controls)
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
        self.preview.clear()

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

    def cancel_task(self):
        if self.worker is not None:
            self.worker.cancel()
            self.cancel.setEnabled(False)

    def done(self, result):
        if self.thread is not None:
            self.closing = True
            self.cancel_task()
            return
        super().done(result)
