from __future__ import annotations

import argparse
import configparser
import hashlib
import os
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


def read_settings(path):
    settings = configparser.ConfigParser(interpolation=None)
    settings.read(path, encoding="utf-8-sig")
    return settings


def history_rows(path):
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as database:
        if database.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise AssertionError("Portable operation history is damaged")
        return database.execute("SELECT * FROM events ORDER BY id").fetchall()


def verify_architecture(executable):
    with executable.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise AssertionError("The executable has no Windows PE header")
        stream.seek(0x3C)
        offset = struct.unpack("<I", stream.read(4))[0]
        stream.seek(offset)
        if stream.read(6) != b"PE\x00\x00\x64\x86":
            raise AssertionError("The executable is not Windows x64")


def run(executable, cwd):
    subprocess.run(
        [str(executable), "--smoke-test", "--verify-portable"],
        cwd=cwd,
        check=True,
        timeout=90,
    )


def verify(directory):
    if (directory / "data").exists():
        raise AssertionError("The distribution contains user or verification data")
    verify_architecture(directory / "FileTreeViewer.exe")
    with tempfile.TemporaryDirectory(prefix="ftv-portable-") as temporary:
        root = Path(temporary)
        copied = root / "Folder with spaces" / "파일 트리 뷰어"
        shutil.copytree(directory, copied)
        cwd = root / "unrelated-working-directory"
        cwd.mkdir()
        run(copied / "FileTreeViewer.exe", cwd)
        settings = copied / "data/settings/preferences.ini"
        history = copied / "data/history/operation-history.sqlite3"
        if not settings.is_file() or not history.is_file() or (cwd / "data").exists():
            raise AssertionError("Portable data was not saved beside the executable")
        original_history = history_rows(history)
        if not original_history:
            raise AssertionError("Portable startup did not write operation history")
        token = uuid.uuid4().hex
        preferences = QSettings(str(settings), QSettings.Format.IniFormat)
        preferences.setValue("verification/portable_token", token)
        preferences.setValue("preferences/theme", "Dark")
        preferences.sync()
        if preferences.status() != QSettings.Status.NoError:
            raise AssertionError("Portable verification preferences could not be saved")
        del preferences
        relocated = root / "Relocated application"
        copied.rename(relocated)
        run(relocated / "FileTreeViewer.exe", cwd)
        restored = read_settings(relocated / "data/settings/preferences.ini")
        if (
            restored.get("verification", "portable_token") != token
            or restored.get("preferences", "theme") != "Dark"
        ):
            raise AssertionError("Portable settings were lost when moving the folder")
        restored_history = history_rows(
            relocated / "data/history/operation-history.sqlite3"
        )
        if (
            restored_history[: len(original_history)] != original_history
            or len(restored_history) != len(original_history) + 1
        ):
            raise AssertionError("Portable history was lost when moving the folder")
        if (cwd / "data").exists() or (directory / "data").exists():
            raise AssertionError("Verification wrote outside its disposable copy")
    print(
        "PASS: Windows x64 executable, both languages, Unicode path, unrelated CWD, relocated settings and history"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("The packaged executable must be verified on Windows")
    if args.source.suffix.lower() == ".zip":
        checksums = dict(
            reversed(line.split(maxsplit=1))
            for line in (args.source.parent / "SHA256SUMS")
            .read_text(encoding="ascii")
            .splitlines()
            if line.strip()
        )
        expected = checksums.get(args.source.name)
        with args.source.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if expected != actual:
            raise AssertionError("Release checksum does not match the ZIP")
        verify_archive(args.source)
        with tempfile.TemporaryDirectory(prefix="ftv-extracted-") as temporary:
            with zipfile.ZipFile(args.source) as archive:
                archive.extractall(temporary)
            verify(Path(temporary) / "FileTreeViewer")
    else:
        verify(args.source.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
