from __future__ import annotations

import argparse
import hashlib
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import uuid
import zipfile
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QSettings

from scripts.package_windows import verify_archive


def preferences(path):
    settings = QSettings(str(path), QSettings.Format.IniFormat)
    settings.sync()
    return settings


def rows(path):
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as database:
        if database.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise AssertionError("History database is damaged")
        return database.execute("SELECT * FROM events ORDER BY id").fetchall()


def verify(directory):
    if (directory / "data").exists():
        raise AssertionError("Distribution contains user data")
    executable = directory / "FileTreeViewer.exe"
    with executable.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise AssertionError("Missing Windows executable header")
        stream.seek(0x3C)
        offset = struct.unpack("<I", stream.read(4))[0]
        stream.seek(offset)
        if stream.read(6) != b"PE\0\0\x64\x86":
            raise AssertionError("Executable is not Windows x64")
    with tempfile.TemporaryDirectory(prefix="ftv-windows-") as tmp:
        root = Path(tmp)
        copy = root / "With spaces" / "파일 트리 뷰어"
        shutil.copytree(directory, copy)
        cwd = root / "unrelated"
        cwd.mkdir()
        subprocess.run(
            [str(copy / "FileTreeViewer.exe"), "--verify-startup"],
            cwd=cwd,
            check=True,
            timeout=90,
        )
        ini = copy / "data/settings/preferences.ini"
        settings = preferences(ini)
        token = uuid.uuid4().hex
        settings.setValue("verification/token", token)
        settings.setValue("preferences/language", "ko")
        settings.setValue("preferences/theme", "Dark")
        settings.sync()
        if settings.status() != QSettings.Status.NoError:
            raise AssertionError("Settings did not save")
        del settings
        # Write a real audit entry in the disposable copy, then verify relocation.
        from operation_history import HistoryStore

        history = copy / "data/history/operation-history.sqlite3"
        HistoryStore(history).append(
            [{"category": "verification", "action": "relocation", "status": "success"}]
        )
        original = rows(history)
        relocated = root / "Relocated program"
        copy.rename(relocated)
        subprocess.run(
            [str(relocated / "FileTreeViewer.exe"), "--verify-startup"],
            cwd=cwd,
            check=True,
            timeout=90,
        )
        saved = preferences(relocated / "data/settings/preferences.ini")
        if (
            saved.value("verification/token") != token
            or saved.value("preferences/language") != "ko"
            or saved.value("preferences/theme") != "Dark"
        ):
            raise AssertionError("Settings were lost after relocation")
        del saved
        if rows(relocated / "data/history/operation-history.sqlite3") != original:
            raise AssertionError("History was lost after relocation")
        if (cwd / "data").exists() or (directory / "data").exists():
            raise AssertionError("Verification wrote outside its disposable folder")
    print(
        "PASS: x64 EXE, both languages, Unicode path, unrelated CWD, dual panes, read-only mode and relocated settings/history"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    if args.source.suffix.lower() != ".zip":
        verify(args.source.resolve())
        return
    checksums = dict(
        reversed(line.split(maxsplit=1))
        for line in (args.source.parent / "SHA256SUMS")
        .read_text(encoding="ascii")
        .splitlines()
        if line.strip()
    )
    with args.source.open("rb") as stream:
        if (
            checksums.get(args.source.name)
            != hashlib.file_digest(stream, "sha256").hexdigest()
        ):
            raise AssertionError("ZIP SHA-256 mismatch")
    verify_archive(args.source)
    with tempfile.TemporaryDirectory(prefix="ftv-unzip-") as tmp:
        with zipfile.ZipFile(args.source) as archive:
            archive.extractall(tmp)
        verify(Path(tmp) / "FileTreeViewer")


if __name__ == "__main__":
    main()
