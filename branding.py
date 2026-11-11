from __future__ import annotations

import ctypes
import os

from PySide6.QtGui import QIcon

from application_paths import resource_path


def application_icon():
    return QIcon(str(resource_path("assets", "app-icon.png")))


def set_windows_application_id():
    if os.name == "nt":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "Koreapanda.FileTreeViewer"
        )
