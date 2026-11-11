from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QByteArray, QSettings
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QSpinBox,
    QVBoxLayout,
)

from application_paths import settings_path
from planning import ConflictPolicy
from ui.i18n import add_choice, add_choices, choice_text, set_choice_text, tr


def application_settings() -> QSettings:
    settings = QSettings(str(settings_path()), QSettings.Format.IniFormat)
    settings.setAtomicSyncRequired(True)
    settings.setFallbacksEnabled(False)
    return settings


def bounded_integer(value, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if minimum <= number <= maximum else default


def boolean(value, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).casefold()
    return (
        True if text in ("true", "1") else False if text in ("false", "0") else default
    )


@dataclass(frozen=True, slots=True)
class Preferences:
    show_hidden: bool = False
    wrap_preview: bool = True
    preview_font_size: int = 10
    edit_mib: int = 8
    preview_kib: int = 1024
    conflict_policy: ConflictPolicy = ConflictPolicy.ERROR
    theme: str = "System"
    language: str = "en"

    @classmethod
    def load(cls, settings: QSettings) -> Preferences:
        policy = settings.value("preferences/conflict_policy", "error")
        return cls(
            language=settings.value("preferences/language", "en")
            if settings.value("preferences/language", "en") in {"en", "ko"}
            else "en",
            theme=settings.value("preferences/theme", "System")
            if settings.value("preferences/theme", "System")
            in {"System", "Light", "Dark"}
            else "System",
            show_hidden=boolean(settings.value("preferences/show_hidden"), False),
            wrap_preview=boolean(settings.value("preferences/wrap_preview"), True),
            preview_font_size=bounded_integer(
                settings.value("preferences/font_size"), 10, 8, 24
            ),
            edit_mib=bounded_integer(settings.value("preferences/edit_mib"), 8, 1, 8),
            preview_kib=bounded_integer(
                settings.value("preferences/preview_kib"), 1024, 64, 2048
            ),
            conflict_policy=ConflictPolicy.SKIP
            if policy == "skip"
            else ConflictPolicy.ERROR,
        )

    def save(self, settings: QSettings) -> None:
        for key, value in (
            ("language", self.language),
            ("theme", self.theme),
            ("show_hidden", self.show_hidden),
            ("wrap_preview", self.wrap_preview),
            ("font_size", self.preview_font_size),
            ("edit_mib", self.edit_mib),
            ("preview_kib", self.preview_kib),
            ("conflict_policy", self.conflict_policy.value),
        ):
            settings.setValue("preferences/" + key, value)


def restore_layout(window, settings: QSettings) -> None:
    geometry = settings.value("window/geometry")
    if isinstance(geometry, QByteArray):
        window.restoreGeometry(geometry)
    window.tabs.setCurrentIndex(bounded_integer(settings.value("window/tab"), 0, 0, 2))
    for name, splitter in splitters(window):
        state = settings.value("window/" + name)
        if isinstance(state, QByteArray):
            splitter.restoreState(state)
        splitter.setChildrenCollapsible(True)


def save_layout(window, settings: QSettings) -> None:
    settings.setValue("window/geometry", window.saveGeometry())
    settings.setValue("window/tab", window.tabs.currentIndex())
    if hasattr(window, "panel_manager"):
        window.panel_manager.save()
    for name, splitter in splitters(window):
        settings.setValue("window/" + name, splitter.saveState())


def splitters(window):
    return (
        ("real_splitter", window.real_page.content_splitter),
        ("real_navigation_splitter", window.real_page.navigation_splitter),
        ("virtual_splitter", window.virtual_page.content_splitter),
        ("plan_splitter", window.plan_page.workspace_splitter),
    )


class PreferencesDialog(QDialog):
    def __init__(self, preferences: Preferences, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Preferences"))
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.language = QComboBox()
        add_choice(self.language, "English", "en")
        add_choice(self.language, "한국어", "ko")
        self.language.setCurrentIndex(1 if preferences.language == "ko" else 0)
        form.addRow(tr("Language (restart required)"), self.language)
        self.theme = QComboBox()
        add_choices(self.theme, ("System", "Light", "Dark"))
        set_choice_text(self.theme, preferences.theme)
        form.addRow(tr("Application theme"), self.theme)
        self.hidden = QCheckBox(tr("Show hidden files in Real"))
        self.hidden.setChecked(preferences.show_hidden)
        self.wrap = QCheckBox(tr("Wrap preview text"))
        self.wrap.setChecked(preferences.wrap_preview)
        form.addRow(self.hidden)
        form.addRow(self.wrap)
        self.font_size = self._spin(preferences.preview_font_size, 8, 24)
        self.edit_size = self._spin(preferences.edit_mib, 1, 8)
        self.preview_size = self._spin(preferences.preview_kib, 64, 2048)
        form.addRow(tr("Preview font size"), self.font_size)
        form.addRow(tr("Editable file limit (MiB)"), self.edit_size)
        form.addRow(tr("Large file prefix (KiB)"), self.preview_size)
        self.policy = QComboBox()
        add_choice(self.policy, "Stop on conflict", ConflictPolicy.ERROR)
        add_choice(self.policy, "Skip conflicting operation", ConflictPolicy.SKIP)
        self.policy.setCurrentIndex(
            1 if preferences.conflict_policy is ConflictPolicy.SKIP else 0
        )
        form.addRow(tr("New manual Plan operations"), self.policy)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _spin(self, value, minimum, maximum):
        field = QSpinBox()
        field.setRange(minimum, maximum)
        field.setValue(value)
        return field

    def preferences(self) -> Preferences:
        return Preferences(
            self.hidden.isChecked(),
            self.wrap.isChecked(),
            self.font_size.value(),
            self.edit_size.value(),
            self.preview_size.value(),
            self.policy.currentData(),
            choice_text(self.theme),
            self.language.currentData(),
        )
