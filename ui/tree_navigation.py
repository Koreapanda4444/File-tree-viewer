"""Bounded, cooperative tree actions; directory reads stay in the existing index pool."""

from __future__ import annotations

from collections import deque
from time import monotonic

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QInputDialog, QMenu, QToolButton

from ui.i18n import tr


class TreeNavigation(QObject):
    max_depth = 8
    max_items = 5000
    max_seconds = 30

    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.running = self.collapsing = False
        self.queue = deque()
        self.jobs = {}
        self.timer = QTimer(self)
        self.timer.setInterval(5)
        self.timer.timeout.connect(self.step)
        page.model.modelAboutToBeReset.connect(self.cancel)
        page.model.directory_error.connect(self.index_error)
        self.button = QToolButton()
        self.button.setText(tr("Tree actions"))
        self.button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.button)
        self.add_actions(menu)
        self.button.setMenu(menu)
        page.explorer_layout.bar.addWidget(self.button)
        self.cancel_button = QToolButton()
        self.cancel_button.setText(tr("Cancel tree task"))
        self.cancel_button.clicked.connect(self.cancel)
        self.cancel_button.setEnabled(False)
        page.explorer_layout.bar.addWidget(self.cancel_button)

    def available(self):
        p = self.page
        return p.model.root is not None and not (
            p.close_requested or p.operation_is_running or p.search_view_active
        )

    def target(self):
        paths = self.page.selected_paths()
        path = paths[0] if len(paths) == 1 else self.page.model.root_path
        node = self.page.model.nodes_by_path.get(path)
        return (
            path
            if node is not None and node.is_directory and not node.is_link
            else None
        )

    def add_actions(self, menu):
        for title, callback in (
            ("Reveal selection in tree", self.page.reveal_in_tree),
            ("Expand parent path", self.page.reveal_in_tree),
            ("Expand selected folder", lambda: self.start(1)),
            ("Collapse selected folder", lambda: self.start(1, collapse=True)),
            ("Expand to depth…", self.choose_depth),
            (
                "Collapse subtree",
                lambda: self.start(self.max_depth, collapse=True, recursive=True),
            ),
        ):
            action = menu.addAction(tr(title), callback)
            action.command_enabled = self.available
        menu.aboutToShow.connect(
            lambda: [action.setEnabled(self.available()) for action in menu.actions()]
        )

    def choose_depth(self):
        if not self.available():
            return
        depth, accepted = QInputDialog.getInt(
            self.page,
            tr("Expand to depth"),
            tr("Depth (1 = direct children; up to 8 levels and 5,000 items):"),
            2,
            1,
            self.max_depth,
        )
        if accepted:
            self.start(depth)

    def start(self, depth, *, collapse=False, recursive=False, path=None):
        if (
            not self.available()
            or type(depth) is not int
            or not 1 <= depth <= self.max_depth
        ):
            return False
        path = path or self.target()
        node = self.page.model.nodes_by_path.get(path)
        if node is None or not node.is_directory or node.is_link:
            self.page.status_changed.emit(tr("Select a real folder in the tree"))
            return False
        self.cancel()
        self.page.file_revealer.stop()
        self.page.explorer_state.stop()
        self.page.explorer_layout.set_panel(0, True)
        self.depth, self.collapse, self.recursive = depth, collapse, recursive
        self.queue = deque([(path, 0, 0)])
        self.jobs = {}
        self.processed = self.errors = 0
        self.started = self.last_report = monotonic()
        self.page.model.reserve_budget(self, self.max_items)
        self.running = True
        self.cancel_button.setEnabled(True)
        self.timer.start()
        self.report()
        return True

    def index_error(self, error):
        if self.running:
            self.errors += 1

    def report(self):
        self.last_report = monotonic()
        self.page.status_changed.emit(
            tr(
                "Tree task: {count:,} / {limit:,} items · errors: {errors:,}",
                count=self.processed,
                limit=self.max_items,
                errors=self.errors,
            )
        )

    def step(self):
        if not self.running:
            return
        if not self.available():
            self.cancel()
            return
        if monotonic() - self.started > self.max_seconds:
            self.finish("Tree task stopped at its time limit")
            return
        model, tree = self.page.model, self.page.tree
        end = monotonic() + 0.006
        allowance = 64
        while self.queue and allowance > 0 and monotonic() < end:
            path, level, offset = self.queue[0]
            source = model.index_for_path(path)
            node = model.node_from_index(source)
            if node is None or node.is_link:
                self.queue.popleft()
                continue
            visible = self.page.browse_model.mapFromSource(source)
            if offset == 0:
                if self.collapse:
                    self.collapsing = True
                    try:
                        tree.collapse(visible)
                    finally:
                        self.collapsing = False
                    if not self.recursive:
                        self.queue.popleft()
                        continue
                else:
                    before = node.job
                    tree.expand(visible)
                    if model.canFetchMore(source) and not node.children:
                        model.fetchMore(source)
                    if node.job is not None and node.job is not before:
                        self.jobs[path] = node.job
            if node.fetch_pending and not self.collapse:
                break
            stop = min(
                len(node.children),
                offset + allowance,
                offset + self.max_items - self.processed,
            )
            for child in node.children[offset:stop]:
                self.processed += 1
                if (
                    child.is_directory
                    and not child.is_link
                    and (self.collapse or level + 1 < self.depth)
                ):
                    self.queue.append((child.path, level + 1, 0))
            allowance -= stop - offset
            offset = stop
            self.queue[0] = path, level, offset
            if self.processed >= self.max_items:
                self.finish("Tree task stopped at its item limit")
                return
            if offset < len(node.children):
                continue
            if not self.collapse and not node.fully_loaded:
                if model.load_budget == 0:
                    self.finish("Tree task stopped at its item limit")
                    return
                if model.canFetchMore(source):
                    model.fetchMore(source)
                    if node.job is not None:
                        self.jobs[path] = node.job
                break
            self.queue.popleft()
        if not self.queue:
            self.finish("Tree task completed")
        elif monotonic() - self.last_report > 0.1:
            self.report()

    def finish(self, message):
        if not self.running:
            return
        self.running = False
        self.timer.stop()
        self.page.model.release_budget(self)
        self.cancel_button.setEnabled(False)
        self.queue.clear()
        state = self.page.explorer_state
        state.restoring = True
        try:
            for path, job in self.jobs.items():
                node = self.page.model.nodes_by_path.get(path)
                if node is not None and node.job is job:
                    self.page.model.release_children(
                        self.page.model.index_for_path(path), preserve_inventory=True
                    )
        finally:
            state.restoring = False
            self.jobs.clear()
        self.page.status_changed.emit(
            tr(
                "{message} · {count:,} items · errors: {errors:,}",
                message=tr(message),
                count=self.processed,
                errors=self.errors,
            )
        )

    def cancel(self):
        self.finish("Tree task cancelled")
