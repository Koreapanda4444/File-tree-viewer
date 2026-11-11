from __future__ import annotations

import argparse
import hashlib
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from version import __version__


def verify_archive(path):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if not names or any(
            not name.startswith("FileTreeViewer/")
            or ".." in Path(name).parts
            or "\\" in name
            for name in names
        ):
            raise ValueError("Portable ZIP must contain only the FileTreeViewer folder")
        if "FileTreeViewer/FileTreeViewer.exe" not in names or not any(
            name.startswith("FileTreeViewer/_internal/") for name in names
        ):
            raise ValueError("Portable executable or runtime is missing")
        if any(
            name.startswith("FileTreeViewer/data/")
            or "/scripts/" in name
            or "/tests/" in name
            or "app-icon-source" in name
            for name in names
        ):
            raise ValueError("Development files or verification data found in ZIP")
        if archive.testzip() is not None:
            raise ValueError("Portable ZIP failed its CRC check")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path, default=Path("release"))
    options = parser.parse_args()
    source = options.directory.resolve()
    if source.name != "FileTreeViewer" or not (source / "FileTreeViewer.exe").is_file():
        parser.error("Choose the built FileTreeViewer directory")
    options.output.mkdir(parents=True, exist_ok=True)
    destination = options.output / f"FileTreeViewer-{__version__}-windows-x64.zip"
    with zipfile.ZipFile(
        destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path in sorted(source.rglob("*")):
            relative = path.relative_to(source)
            if path.is_file() and relative.parts[0] != "data":
                archive.write(path, "FileTreeViewer/" + relative.as_posix())
    verify_archive(destination)
    with destination.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    (options.output / "SHA256SUMS").write_text(
        f"{digest}  {destination.name}\n", encoding="ascii"
    )
    print(f"{digest}  {destination.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
