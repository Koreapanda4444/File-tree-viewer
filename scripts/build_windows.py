"""Build the same onedir portable application locally and on GitHub Actions."""

from __future__ import annotations

import argparse
import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from version import __version__


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    options = parser.parse_args()
    if os.name != "nt" or struct.calcsize("P") != 8:
        parser.error("Windows x64 is required to build the Windows executable")
    version = tuple(int(part) for part in __version__.split(".")) + (0,)
    with tempfile.TemporaryDirectory(prefix="ftv-build-") as temporary:
        directory = Path(temporary)
        metadata = directory / "version.txt"
        fields = {
            "CompanyName": "Koreapanda",
            "FileDescription": "File Tree Viewer",
            "FileVersion": __version__,
            "InternalName": "FileTreeViewer",
            "OriginalFilename": "FileTreeViewer.exe",
            "ProductName": "File Tree Viewer",
            "ProductVersion": __version__,
        }
        strings = ",".join(
            f"StringStruct({key!r}, {value!r})" for key, value in fields.items()
        )
        metadata.write_text(
            f"VSVersionInfo(ffi=FixedFileInfo(filevers={version!r}, prodvers={version!r}, mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0,0)), kids=[StringFileInfo([StringTable('040904B0', [{strings}])]), VarFileInfo([VarStruct('Translation',[1033,1200])])])",
            encoding="utf-8",
        )
        command = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onedir",
            "--windowed",
            "--name",
            "FileTreeViewer",
            "--contents-directory",
            "_internal",
            "--icon",
            str(ROOT / "assets/app-icon.ico"),
            "--version-file",
            str(metadata),
            "--add-data",
            str(ROOT / "assets/app-icon.png") + os.pathsep + "assets",
            "--add-data",
            str(ROOT / "ui/translations") + os.pathsep + "ui/translations",
            "--collect-submodules",
            "send2trash",
            "--distpath",
            str(options.dist.resolve()),
            "--workpath",
            str(directory / "work"),
            "--specpath",
            str(directory),
            str(ROOT / "main.py"),
        ]
        subprocess.run(command, cwd=ROOT, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
