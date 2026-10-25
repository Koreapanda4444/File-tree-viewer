from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QSize
from PySide6.QtWidgets import QAbstractItemView, QApplication, QPushButton, QStyle

ICONS = {
    "back": QStyle.StandardPixmap.SP_ArrowBack,
    "forward": QStyle.StandardPixmap.SP_ArrowForward,
    "up": QStyle.StandardPixmap.SP_ArrowUp,
    "open": QStyle.StandardPixmap.SP_DialogOpenButton,
    "load": QStyle.StandardPixmap.SP_DialogOpenButton,
    "save": QStyle.StandardPixmap.SP_DialogSaveButton,
    "export": QStyle.StandardPixmap.SP_DialogSaveButton,
    "delete": QStyle.StandardPixmap.SP_TrashIcon,
    "remove": QStyle.StandardPixmap.SP_TrashIcon,
    "refresh": QStyle.StandardPixmap.SP_BrowserReload,
    "search": QStyle.StandardPixmap.SP_FileDialogContentsView,
    "compare": QStyle.StandardPixmap.SP_FileDialogDetailedView,
    "copy": QStyle.StandardPixmap.SP_FileIcon,
    "move": QStyle.StandardPixmap.SP_ArrowRight,
    "undo": QStyle.StandardPixmap.SP_ArrowBack,
    "recover": QStyle.StandardPixmap.SP_DriveHDIcon,
    "cancel": QStyle.StandardPixmap.SP_DialogCancelButton,
    "close": QStyle.StandardPixmap.SP_DialogCloseButton,
    "new": QStyle.StandardPixmap.SP_FileDialogNewFolder,
    "add": QStyle.StandardPixmap.SP_FileDialogNewFolder,
    "apply": QStyle.StandardPixmap.SP_DialogApplyButton,
    "preview": QStyle.StandardPixmap.SP_FileDialogInfoView,
}


class AppearanceController(QObject):
    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Polish:
            if isinstance(watched, QPushButton):
                word = watched.text().replace("&", "").lower().split(" ", 1)[0]
                icon = ICONS.get(word)
                if icon is not None and watched.icon().isNull():
                    watched.setIcon(watched.style().standardIcon(icon))
                    watched.setIconSize(QSize(16, 16))
                watched.setAutoDefault(False)
            elif isinstance(watched, QAbstractItemView):
                watched.setAlternatingRowColors(True)
                watched.setVerticalScrollMode(
                    QAbstractItemView.ScrollMode.ScrollPerPixel
                )
        return False


def install_appearance():
    app = QApplication.instance()
    if not hasattr(app, "file_tree_appearance"):
        controller = AppearanceController(app)
        app.file_tree_appearance = controller
        app.installEventFilter(controller)
        app.setWindowIcon(
            app.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon)
        )
    return app.file_tree_appearance
