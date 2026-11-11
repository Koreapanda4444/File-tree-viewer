from __future__ import annotations

import json
from pathlib import Path
from string import Formatter

from PySide6.QtCore import QLibraryInfo, Qt, QTranslator
from PySide6.QtWidgets import QApplication, QComboBox

SOURCE_ROLE = int(Qt.ItemDataRole.UserRole) + 1000
_language = "en"
_catalog = {}
_reverse_catalog = {}
_qt_translator = None


def set_language(language):
    global _language, _catalog, _qt_translator, _reverse_catalog
    _language = language if language in {"en", "ko"} else "en"
    _catalog = {}
    if _language == "ko":
        path = Path(__file__).with_name("translations") / "ko.json"
        with path.open(encoding="utf-8") as stream:
            raw = json.load(stream)
        formatter = Formatter()
        for source, translated in raw.items():
            if {
                name for _, name, _, _ in formatter.parse(source) if name is not None
            } == {
                name
                for _, name, _, _ in formatter.parse(translated)
                if name is not None
            }:
                _catalog[source] = translated
    _reverse_catalog = {value: key for key, value in _catalog.items()}
    app = QApplication.instance()
    if app is not None:
        if _qt_translator is not None:
            app.removeTranslator(_qt_translator)
        _qt_translator = QTranslator(app)
        if _language == "ko":
            _qt_translator.load(
                "qt_ko", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
            )
            app.installTranslator(_qt_translator)


def tr(source, **values):
    if not isinstance(source, str):
        return source
    result = _catalog.get(source, source)
    return result.format(**values) if values else result


def source_text(display):
    return _reverse_catalog.get(display, display)


def add_choice(widget, label, *args):
    if isinstance(widget, QComboBox) and isinstance(label, str):
        widget.addItem(tr(label), *args)
        widget.setItemData(widget.count() - 1, label, SOURCE_ROLE)
    else:
        widget.addItem(label, *args)


def add_choices(widget, values):
    for label in values:
        add_choice(widget, label)


def choice_text(widget):
    return widget.currentData(SOURCE_ROLE) or widget.currentText()


def set_choice_text(widget, value):
    index = widget.findData(value, SOURCE_ROLE)
    if index >= 0:
        widget.setCurrentIndex(index)
    else:
        widget.setCurrentText(value)
