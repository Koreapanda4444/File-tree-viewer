from __future__ import annotations

from PySide6.QtCore import QObject, QTimer


class LiveSearch(QObject):
    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.revision = 0
        self.active_revision = -1
        self.waiting = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(350)
        self.timer.timeout.connect(self.request)
        page.search_input.textEdited.connect(self.changed)

    @property
    def accepts(self):
        return self.active_revision == self.revision and not self.page.close_requested

    def changed(self, _text=None):
        self.revision += 1
        self.waiting = False
        if self.page.search_worker is not None:
            self.page.search_worker.cancel()
        if not self.page.close_requested:
            self.timer.start(350)

    def request_now(self):
        self.timer.stop()
        self.request()

    def request(self):
        page = self.page
        if (
            page.close_requested
            or page.operation_is_running
            or page.model.root_path is None
        ):
            return
        if page.search_is_running:
            self.waiting = True
            page.search_worker.cancel()
            return
        self.waiting = False
        if page.has_search_request():
            page.start_search()
        elif page.search_view_active:
            page.clear_search()

    def started(self, thread):
        self.timer.stop()
        self.waiting = False
        self.active_revision = self.revision
        thread.finished.connect(self.finished)

    def finished(self):
        if self.waiting and not self.page.close_requested:
            self.timer.start(0)

    def stop(self):
        self.timer.stop()
        self.waiting = False
        self.revision += 1
