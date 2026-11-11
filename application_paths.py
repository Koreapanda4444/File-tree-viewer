from __future__ import annotations

import sys
import tempfile
from pathlib import Path

_data_override: Path | None = None


def application_directory():
    return (
        Path(sys.executable).resolve().parent
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parent
    )


def resource_path(*parts):
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base.joinpath(*parts)


def configure_data_directory(path=None):
    global _data_override
    _data_override = Path(path).resolve() if path is not None else None


def data_directory():
    return _data_override or application_directory() / "data"


def settings_path():
    return data_directory() / "settings" / "preferences.ini"


def history_path():
    return data_directory() / "history" / "operation-history.sqlite3"


def prepare_data_directory():
    directory = data_directory()
    directory.mkdir(parents=True, exist_ok=True)
    # Probe the actual portable location before starting workers or reading settings.
    with tempfile.TemporaryFile(prefix=".write-check-", dir=directory) as stream:
        stream.write(b"portable")
        stream.flush()
