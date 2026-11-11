from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

from PySide6.QtGui import QIcon


def application_icon():
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return QIcon(str(base / "assets" / "app-icon.png"))


def set_windows_application_id():
    if os.name == "nt":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "Koreapanda.FileTreeViewer"
        )
