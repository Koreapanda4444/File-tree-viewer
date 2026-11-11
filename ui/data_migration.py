from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths
from PySide6.QtWidgets import QMessageBox

from application_paths import history_path, settings_path
from fs_ops import move_no_replace
from ui.i18n import tr
from ui.preferences import application_settings


def previous_data():
    settings = []
    seen = set()
    for format in (QSettings.Format.NativeFormat, QSettings.Format.IniFormat):
        candidate = QSettings(
            format, QSettings.Scope.UserScope, "FileTreeViewer", "FileTreeViewer"
        )
        candidate.setFallbacksEnabled(False)
        if candidate.fileName() not in seen and candidate.allKeys():
            settings.append(candidate)
            seen.add(candidate.fileName())
    history = (
        Path(
            QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.AppLocalDataLocation
            )
        )
        / "operation-history.sqlite3"
    )
    return (
        settings,
        history
        if history.is_file() and history.absolute() != history_path().absolute()
        else None,
    )


def copy_history(source, destination):
    if destination.exists():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=".history-import-", suffix=".sqlite3", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(name)
    try:
        original = sqlite3.connect(
            Path(source).resolve().as_uri() + "?mode=ro", uri=True, timeout=5
        )
        copy = sqlite3.connect(temporary)
        try:
            original.backup(copy)
            if copy.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise ValueError("The previous history database is damaged")
        finally:
            copy.close()
            original.close()
        with temporary.open("r+b") as stream:
            os.fsync(stream.fileno())
        move_no_replace(temporary, destination, allow_cross_device=False)
        return True
    finally:
        temporary.unlink(missing_ok=True)


def import_previous_data(parent=None, *, startup=False):
    if startup and settings_path().exists():
        return
    sources, history = previous_data()
    if not sources and history is None:
        if not startup:
            QMessageBox.information(
                parent,
                tr("Import Previous Data"),
                tr("No previous settings or history were found."),
            )
        return
    message = tr(
        "Copy previous settings and operation history into this portable folder? Existing portable values and history will be preserved. Original data and transaction recovery files will not be removed."
    )
    locations = [source.fileName() for source in sources] + (
        [str(history)] if history else []
    )
    if (
        QMessageBox.question(
            parent,
            tr("Import Previous Data"),
            message + "\n\n" + "\n".join(locations),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        != QMessageBox.StandardButton.Yes
    ):
        return
    try:
        target = application_settings()
        for source in sources:
            for key in source.allKeys():
                if not target.contains(key):
                    target.setValue(key, source.value(key))
        target.sync()
        if target.status() != QSettings.Status.NoError:
            raise OSError("Portable settings could not be saved")
        copied = history is not None and copy_history(history, history_path())
    except (OSError, ValueError, sqlite3.Error) as error:
        QMessageBox.warning(
            parent,
            tr("Data Import Failed"),
            tr("Previous data was preserved. Check permissions and try again.")
            + "\n\n"
            + str(error),
        )
    else:
        QMessageBox.information(
            parent,
            tr("Data Import Complete"),
            tr(
                "Settings were imported. Restart the application to use imported preferences. Existing portable history was preserved unless a new history database was copied."
            )
            + ("\n" + str(history_path()) if copied else ""),
        )
