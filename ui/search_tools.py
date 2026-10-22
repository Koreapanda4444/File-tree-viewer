from __future__ import annotations

import sqlite3
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMenu

from search_export import export_search
from search_history import SearchHistory, SearchRequest
from snapshot import SnapshotCancelled
from ui.preferences import application_settings
from ui.tasks import CancellableWorker, prepare_worker_thread


class SearchExportWorker(CancellableWorker):
    progress = Signal(int)
    finished = Signal(object, str)

    def __init__(self, database_path, destination, format, request):
        super().__init__()
        self.database_path, self.destination, self.format, self.request = (
            database_path,
            destination,
            format,
            request,
        )

    def run(self):
        try:
            result = export_search(
                self.database_path,
                self.destination,
                format=self.format,
                request=self.request,
                cancelled=self._cancelled,
                progress=self.progress.emit,
            )
        except SnapshotCancelled:
            self.finished.emit(None, "Export cancelled")
        except (OSError, RuntimeError, ValueError, TypeError, sqlite3.Error) as error:
            self.finished.emit(None, str(error))
        else:
            self.finished.emit(result, "")


class SearchTools(QObject):
    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.history = SearchHistory(application_settings())
        self.outcome = None
        page.search_export_thread = None
        page.search_export_worker = None
        page.search_tools_button.clicked.connect(self.show_menu)

    def request(self):
        page = self.page
        return SearchRequest(
            page.search_input.text().strip(),
            page.search_options,
            str(page.model.root_path) if page.model.root_path else None,
        )

    def show_menu(self):
        page = self.page
        menu = QMenu(page)
        ready = (
            not page.search_is_running
            and not page.operation_is_running
            and page.search_export_thread is None
        )
        recent = menu.addMenu("Recent searches")
        for item in self.history.recent:
            action = recent.addAction(self.label(item))
            action.setEnabled(ready)
            action.triggered.connect(
                lambda checked=False, request=item: self.restore(request)
            )
        recent.setEnabled(bool(self.history.recent))
        presets = menu.addMenu("Presets")
        remove = menu.addMenu("Remove preset")
        for name, item in sorted(self.history.presets.items()):
            action = presets.addAction(name)
            action.setEnabled(ready)
            action.triggered.connect(
                lambda checked=False, request=item: self.restore(request)
            )
            remove.addAction(name, lambda key=name: self.remove_preset(key))
        presets.setEnabled(bool(self.history.presets))
        remove.setEnabled(bool(self.history.presets))
        save = menu.addAction("Save current conditions as preset…", self.save_preset)
        save.setEnabled(page.has_search_request())
        menu.addSeparator()
        action = menu.addAction("Open selected result location", self.open_location)
        action.setEnabled(
            ready and page.search_view_active and len(page.selected_paths()) == 1
        )
        action = menu.addAction("Export results…", self.export_results)
        action.setEnabled(
            ready and page.search_view_active and page.search_results.total_count > 0
        )
        menu.exec(
            page.search_tools_button.mapToGlobal(
                page.search_tools_button.rect().bottomLeft()
            )
        )
        menu.deleteLater()

    @staticmethod
    def label(request):
        query = request.query or (
            f"Contents: {request.options.content_query}"
            if request.options.content_query
            else "Filtered search"
        )
        return query[:80] + (f" — {request.root}" if request.root else "")

    def restore(self, request):
        page = self.page
        if (
            page.search_is_running
            or page.operation_is_running
            or page.search_export_thread is not None
        ):
            return
        if (
            request.root
            and Path(request.root) != page.model.root_path
            and not page.set_root(Path(request.root))
        ):
            return
        page.search_options = request.options
        page.search_input.setText(request.query)
        page.search_filters_button.setText(
            "Filters ●" if request.options.active else "Filters…"
        )
        page.update_search_button()
        page.live_search.changed()

    def save_preset(self):
        name, accepted = QInputDialog.getText(
            self.page, "Save Search Preset", "Preset name:"
        )
        if accepted:
            try:
                saved = self.history.set_preset(name, self.request())
            except ValueError as error:
                self.page.status_changed.emit(str(error))
            else:
                self.page.status_changed.emit(
                    "Search preset saved" if saved else "Could not save search preset"
                )

    def remove_preset(self, name):
        self.history.presets.pop(name, None)
        if not self.history.save():
            self.page.status_changed.emit("Could not save preset changes")

    def open_location(self):
        paths = self.page.selected_paths()
        if len(paths) == 1:
            self.page.jump_to_item(str(paths[0]))

    def export_results(self):
        page = self.page
        if (
            page.search_is_running
            or page.operation_is_running
            or page.search_export_thread is not None
        ):
            return
        destination, selected = QFileDialog.getSaveFileName(
            page,
            "Export Search Results to a New File",
            "search-results.json",
            "JSON (*.json);;CSV (*.csv)",
        )
        if not destination:
            return
        worker = SearchExportWorker(
            page.search_results.database_path,
            destination,
            "csv" if "CSV" in selected else "json",
            page.search_context,
        )
        worker.finished.connect(self.receive)
        worker.progress.connect(
            lambda count: page.task_progress.emit(
                "Exporting search results", count, page.search_results.total_count
            )
        )
        page.search_export_thread = prepare_worker_thread(
            page, worker, on_finished=self.finished
        )
        page.search_export_worker = worker
        self.outcome = None
        page.search_export_thread.start()

    def receive(self, result, error):
        self.outcome = (result, error)

    def finished(self):
        page = self.page
        page.search_export_thread = None
        page.search_export_worker = None
        if self.outcome:
            result, error = self.outcome
            page.status_changed.emit(error or f"Exported search results: {result}")
        if page.close_requested:
            page._emit_ready_to_close()
