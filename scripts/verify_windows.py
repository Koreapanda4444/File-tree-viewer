from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path


def run(executable, cwd):
    subprocess.run(
        [str(executable), "--smoke-test", "--verify-portable"],
        cwd=cwd,
        check=True,
        timeout=90,
    )


def verify(directory):
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
        original_settings = settings.read_bytes()
        relocated = root / "Relocated application"
        copied.rename(relocated)
        run(relocated / "FileTreeViewer.exe", cwd)
        if (
            not (relocated / "data/settings/preferences.ini").is_file()
            or not original_settings
        ):
            raise AssertionError("Portable settings were lost when moving the folder")
    print(
        "PASS: executable, both languages, Unicode path, unrelated CWD and portable folder relocation"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("The packaged executable must be verified on Windows")
    if args.source.suffix == ".zip":
        expected = (
            (args.source.parent / "SHA256SUMS").read_text(encoding="ascii").split()[0]
        )
        with args.source.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if expected != actual:
            raise AssertionError("Release checksum does not match the ZIP")
        with tempfile.TemporaryDirectory(prefix="ftv-extracted-") as temporary:
            with zipfile.ZipFile(args.source) as archive:
                archive.extractall(temporary)
            verify(Path(temporary) / "FileTreeViewer")
    else:
        verify(args.source.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
