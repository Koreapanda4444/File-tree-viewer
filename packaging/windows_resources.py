from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from version import __version__


def main():
    version = tuple(int(part) for part in __version__.split(".")) + (0,)
    directory = ROOT / "build" / "windows"
    directory.mkdir(parents=True, exist_ok=True)
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


if __name__ == "__main__":
    main()
