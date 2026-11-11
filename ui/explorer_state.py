from __future__ import annotations

import json
from pathlib import Path
from time import monotonic

from PySide6.QtCore import QItemSelectionModel, QObject, QTimer


class ExplorerState(QObject):
    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.saved = {}
        self.pending = None
        self.restoring = False
        self.timer = QTimer(self)
        self.timer.setInterval(10)
        self.timer.timeout.connect(self.restore)
        page.model.modelAboutToBeReset.connect(self.capture)
        page.model.modelReset.connect(self.schedule)
        page.model.rowsAboutToBeRemoved.connect(self.capture)
        page.model.rowsRemoved.connect(self.schedule)

    def capture(self, *args):
        page = self.page
        root = page.model.root_path
        if (
            self.restoring
            or self.pending is not None
            or root is None
            or page.search_view_active
        ):
            return
        expanded, stack = [], [page.model.root]
        while stack and len(expanded) < 512:
            node = stack.pop()
            if not node.is_directory:
                continue
            index = page.browse_model.mapFromSource(
                page.model.index_for_path(node.path)
            )
            if page.tree.isExpanded(index):
                expanded.append(str(node.path))
                stack.extend(node.children)
        state = {
            "root": str(root),
            "expanded": expanded,
            "selected": [str(path) for path in page.selected_paths()[:256]],
            "scroll": [view.verticalScrollBar().value() for view in self.views()],
        }
        self.saved[str(root)] = state
        self.pending = state
        if len(self.saved) > 32:
            del self.saved[next(iter(self.saved))]

    def views(self):
        return (self.page.tree, self.page.folder_view, self.page.folder_icons)

    def schedule(self, *args):
        state = self.saved.get(str(self.page.model.root_path))
        if state is None:
            self.stop()
        else:
            self.pending = {
                **state,
                "expanded": list(state["expanded"]),
                "selected": list(state["selected"]),
            }
            self.deadline = monotonic() + 10
            self.timer.start()

    def select_after_refresh(self, paths):
        self.capture()
        state = self.saved.get(str(self.page.model.root_path))
        if state is not None:
            state["selected"] = [str(path) for path in paths][:256]
            self.pending = state

    def ensure_index(self, path):
        model = self.page.model
        root = model.root_path
        if root is None or not path.is_relative_to(root):
            return None, False
        index = model.index_for_path(path)
        if index.isValid():
            return index, False
        parent = root
        for part in path.relative_to(root).parts:
            parent_index = model.index_for_path(parent)
            node = model.node_from_index(parent_index)
            if node is None:
                return None, False
            if model.canFetchMore(parent_index):
                model.fetchMore(parent_index)
                return None, True
            if node.fetch_pending:
                return None, True
            parent /= part
            if not model.index_for_path(parent).isValid():
                return None, False
        return None, False

    def restore(self):
        page, state = self.page, self.pending
        if (
            state is None
            or page.close_requested
            or state["root"] != str(page.model.root_path)
        ):
            self.stop()
            return
        if page.search_view_active:
            self.stop()
            return
        self.restoring = True
        waiting = False
        try:
            for field in ("expanded", "selected"):
                for text in list(state[field])[:32]:
                    source, pending = self.ensure_index(Path(text))
                    waiting |= pending
                    if source is not None:
                        index = page.browse_model.mapFromSource(source)
                        if field == "expanded":
                            page.tree.expand(index)
                        else:
                            selection = page.tree.selectionModel()
                            selection.select(
                                index,
                                QItemSelectionModel.SelectionFlag.Select
                                | QItemSelectionModel.SelectionFlag.Rows,
                            )
                            if not selection.currentIndex().isValid():
                                selection.setCurrentIndex(
                                    index, QItemSelectionModel.SelectionFlag.NoUpdate
                                )
                    if not pending:
                        state[field].remove(text)
            if (
                not waiting
                and not state["expanded"]
                and not state["selected"]
                or monotonic() > self.deadline
            ):
                for view, scroll in zip(self.views(), state["scroll"]):
                    view.verticalScrollBar().setValue(scroll)
                self.stop()
        finally:
            self.restoring = False

    def stop(self):
        self.timer.stop()
        self.pending = None

    def save_session(self, settings):
        self.capture()
        state = self.saved.get(str(self.page.model.root_path))
        if state is not None:
            settings.setValue(
                "navigation/explorer_state", json.dumps(state, ensure_ascii=False)
            )

    def load_session(self, settings):
        raw = settings.value("navigation/explorer_state", "")
        if not isinstance(raw, str) or not raw or len(raw) > 512 * 1024:
            return
        try:
            state = json.loads(raw)
            root = Path(state["root"])
            if not root.is_absolute() or not root.is_dir():
                return
            for field, limit in (("expanded", 512), ("selected", 256)):
                values = state[field]
                if (
                    not isinstance(values, list)
                    or len(values) > limit
                    or any(
                        not isinstance(value, str)
                        or len(value) > 4096
                        or not Path(value).is_relative_to(root)
                        for value in values
                    )
                ):
                    return
            if (
                not isinstance(state["scroll"], list)
                or len(state["scroll"]) != 3
                or any(
                    type(value) is not int or not 0 <= value < 2**31
                    for value in state["scroll"]
                )
            ):
                return
            if self.page.set_root(root):
                self.saved[str(root)] = state
                self.schedule()
        except (KeyError, TypeError, ValueError, OSError):
            return
