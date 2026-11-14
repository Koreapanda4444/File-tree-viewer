from __future__ import annotations
from PySide6.QtCore import Qt
from PySide6.QtGui import QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStyle,
    QTreeView,
    QVBoxLayout,
    QWidget,
)
from ui.address import AddressBar
from ui.file_views import FolderViews
from ui.i18n import add_choice, tr
from ui.image_preview import ImagePreview
from ui.widgets import FlowLayout
from ui.widgets import PlainLabel as QLabel


class ExplorerPage(QWidget):
    def __init__(self, *, virtual: bool) -> None:
        super().__init__()
        self.virtual = virtual

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        layout.addLayout(self._create_location_row())
        layout.addLayout(self._create_search_row())
        layout.addWidget(self._create_content(), 1)
        layout.addLayout(self._create_action_row())

    def _create_location_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        if not self.virtual:
            self.back_button = QPushButton(tr("Back"))
            self.forward_button = QPushButton(tr("Forward"))
            self.up_button = QPushButton(tr("Up"))
            for button, icon in (
                (self.back_button, QStyle.StandardPixmap.SP_ArrowBack),
                (self.forward_button, QStyle.StandardPixmap.SP_ArrowForward),
                (self.up_button, QStyle.StandardPixmap.SP_ArrowUp),
            ):
                button.setToolTip(button.text())
                button.setIcon(self.style().standardIcon(icon))
                button.setText(tr(""))
                button.setFixedWidth(36)
                button.setEnabled(False)
                row.addWidget(button)
        row.addWidget(QLabel(tr("Workspace" if self.virtual else "Root")))

        self.location = QLineEdit()
        self.location.setReadOnly(self.virtual)
        if self.virtual:
            self.location.setText(tr("VM:/"))
        else:
            self.location.setPlaceholderText(
                tr("Enter a folder or drive path, then press Enter")
            )
        if self.virtual:
            row.addWidget(self.location, 1)
        else:
            self.address_bar = AddressBar(self.location)
            row.addWidget(self.address_bar, 1)

        self.location_buttons = []
        if self.virtual:
            self.save_button = QPushButton(tr("Save"))
            self.load_button = QPushButton(tr("Load"))
            self.export_button = QPushButton(tr("Export"))
            self.reset_button = QPushButton(tr("Reset"))
            self.location_buttons.extend(
                (
                    self.save_button,
                    self.load_button,
                    self.export_button,
                    self.reset_button,
                )
            )
        else:
            self.select_root_button = QPushButton(tr("Select Root"))
            self.refresh_button = QPushButton(tr("Refresh"))
            self.location_buttons.extend((self.select_root_button, self.refresh_button))

        for button in self.location_buttons:
            button.setEnabled(False)
            row.addWidget(button)

        self.show_hidden = QCheckBox(tr("Show hidden"))
        self.show_hidden.setVisible(not self.virtual)
        self.show_hidden.setEnabled(False)
        row.addWidget(self.show_hidden)
        return row

    def _create_search_row(self) -> QVBoxLayout:
        layout = QVBoxLayout()
        layout.setSpacing(6)
        row = QHBoxLayout()
        layout.addLayout(row)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(tr("Search files and folders"))
        self.search_input.setEnabled(False)

        self.search_button = QPushButton(tr("Search"))
        self.clear_search_button = QPushButton(tr("Clear"))
        self.search_button.setEnabled(False)
        self.clear_search_button.setEnabled(False)

        row.addWidget(self.search_input, 1)
        row.addWidget(self.search_button)
        row.addWidget(self.clear_search_button)
        if not self.virtual:
            row = FlowLayout()
            layout.addLayout(row)
            self.search_filters_button = QPushButton(tr("Filters…"))
            self.search_tools_button = QPushButton(tr("Search Tools…"))
            row.addWidget(self.search_filters_button)
            row.addWidget(self.search_tools_button)
            self.name_input = QLineEdit()
            self.name_input.setPlaceholderText(tr("Jump to file or folder…"))
            self.name_input.setFixedWidth(240)
            self.name_input.setEnabled(False)
            self.jump_button = QPushButton(tr("Jump"))
            self.jump_button.setEnabled(False)
            row.addWidget(self.name_input)
            row.addWidget(self.jump_button)
        self.search_button.setProperty("primary", True)
        return layout

    def _create_content(self) -> QSplitter:
        splitter = self.content_splitter = QSplitter(Qt.Orientation.Horizontal)

        self.tree = QTreeView()
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tree.setUniformRowHeights(True)
        self.tree.setMinimumWidth(160)
        self.tree.setIndentation(18)

        empty_model = QStandardItemModel(self.tree)
        empty_model.setHorizontalHeaderLabels(
            (tr("Name"), tr("Type"), tr("Size"), tr("Modified"))
        )
        self.tree.setModel(empty_model)
        self.tree.header().resizeSection(0, 360)
        if self.virtual:
            splitter.addWidget(self.tree)
        else:
            self.navigation_splitter = QSplitter(Qt.Orientation.Horizontal)
            self.navigation_splitter.addWidget(self.tree)
            self.folder_view = QTreeView()
            self.folder_view.setRootIsDecorated(False)
            self.folder_view.setItemsExpandable(False)
            self.folder_view.setUniformRowHeights(True)
            self.folder_view.setAlternatingRowColors(True)
            self.folder_view.setSelectionMode(
                QAbstractItemView.SelectionMode.ExtendedSelection
            )
            self.folder_view.setSelectionBehavior(
                QAbstractItemView.SelectionBehavior.SelectRows
            )
            self.folder_panel = FolderViews(self.folder_view)
            self.folder_icons = self.folder_panel.icons
            self.navigation_splitter.addWidget(self.folder_panel)
            self.navigation_splitter.setStretchFactor(0, 1)
            self.navigation_splitter.setStretchFactor(1, 3)
            splitter.addWidget(self.navigation_splitter)

        details = QWidget()
        details_layout = QVBoxLayout(details)
        details_layout.setContentsMargins(0, 0, 0, 0)

        details_group = QGroupBox(tr("Details"))
        details_group.setMinimumHeight(230)
        details_form = QFormLayout(details_group)
        self.detail_values = {}
        for label in (
            "Name",
            "Path",
            "Type",
            "Size",
            "Modified",
            "Permissions",
            "Link target",
        ):
            value = QLabel(tr("-"))
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setWordWrap(label in ("Path", "Link target"))
            self.detail_values[label.lower()] = value
            details_form.addRow(tr("{value0}:", value0=tr(label)), value)
        self.metadata_button = QPushButton(tr("Inspect Metadata"))
        self.metadata_button.setEnabled(False)
        self.metadata_button.setVisible(not self.virtual)
        details_form.addRow(self.metadata_button)
        details_layout.addWidget(details_group)

        preview_group = QGroupBox(tr("Preview"))
        preview_group.setMinimumHeight(250)
        preview_layout = QVBoxLayout(preview_group)

        preview_toolbar = FlowLayout()
        self.preview_status = QLabel(tr("Select a file to preview it"))
        self.preview_status.setWordWrap(True)
        preview_layout.addWidget(self.preview_status)

        self.save_file_button = QPushButton(tr("Save"))
        self.reload_file_button = QPushButton(tr("Reload"))
        self.open_external_button = QPushButton(tr("Open Externally"))
        for button in (
            self.save_file_button,
            self.reload_file_button,
            self.open_external_button,
        ):
            button.setEnabled(False)
            preview_toolbar.addWidget(button)
        preview_layout.addLayout(preview_toolbar)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText(tr("Select a file to preview it"))
        preview_layout.addWidget(self.preview)
        self.image_preview = ImagePreview()
        self.image_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_preview.setMinimumSize(1, 1)
        self.image_preview.hide()
        preview_layout.addWidget(self.image_preview)
        details_layout.addWidget(preview_group, 1)

        details_scroll = QScrollArea()
        details_scroll.setWidgetResizable(True)
        details_scroll.setWidget(details)
        details_scroll.setMinimumWidth(240)
        splitter.addWidget(details_scroll)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([1000, 320])
        details.setMinimumWidth(220)
        return splitter

    def _create_action_row(self) -> FlowLayout:
        row = FlowLayout()
        actions = (
            ("new_file_button", "New File"),
            ("new_folder_button", "New Folder"),
            ("rename_button", "Rename"),
            ("copy_button", "Copy"),
            ("move_button", "Move"),
            ("delete_button", "Delete"),
            ("undo_button", "Undo"),
        )

        self.action_buttons = []
        for attribute, name in actions:
            button = QPushButton(tr(name))
            button.setEnabled(False)
            setattr(self, attribute, button)
            self.action_buttons.append(button)
            row.addWidget(button)

        if not self.virtual:
            self.transfer_policy = QComboBox()
            for label, value in (
                ("Rename conflicts", "rename"),
                ("Skip conflicts", "skip"),
                ("Stop on conflict", "stop"),
            ):
                add_choice(self.transfer_policy, label, value)
            self.transfer_policy.setToolTip(
                tr("Conflict policy for Real copy/move; existing files are preserved")
            )
            self.operation_results_button = QPushButton(tr("Last Operation"))
            self.operation_results_button.setEnabled(False)
            row.addWidget(self.transfer_policy)
            row.addWidget(self.operation_results_button)
            self.compare_files_button = QPushButton(tr("Compare Files"))
            row.addWidget(self.compare_files_button)
        self.tree_export_button = QPushButton(tr("Export Tree…"))
        row.addWidget(self.tree_export_button)
        self.delete_button.setProperty("danger", True)
        return row
