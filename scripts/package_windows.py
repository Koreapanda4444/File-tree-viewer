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
        if (
            not names
            or len(names) != len(set(names))
            or any(
                not name.startswith("FileTreeViewer/")
                or ".." in Path(name).parts
                or "\\" in name
                for name in names
            )
        ):
            raise ValueError("Invalid portable ZIP paths")
        required = {
            "FileTreeViewer/FileTreeViewer.exe",
            "FileTreeViewer/_internal/assets/app-icon.png",
            "FileTreeViewer/_internal/ui/translations/ko.json",
            "FileTreeViewer/README.md",
            "FileTreeViewer/README.ko.md",
        }
        if not required.issubset(names):
            raise ValueError("Executable, icon, translations or portable guide missing")
        if any(
            name.startswith("FileTreeViewer/data/")
            or "/scripts/" in name
            or "/tests/" in name
            or "app-icon-source" in name
            for name in names
        ):
            raise ValueError("Private data or development files in distribution")
        if archive.testzip() is not None:
            raise ValueError("ZIP CRC validation failed")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, default=Path("release"))
    options = parser.parse_args()
    source = options.source.resolve()
    if source.name != "FileTreeViewer" or not (source / "FileTreeViewer.exe").is_file():
        parser.error("Choose the built FileTreeViewer folder")
    options.output.mkdir(parents=True, exist_ok=True)
    destination = options.output / f"FileTreeViewer-{__version__}-windows-x64.zip"
    with zipfile.ZipFile(
        destination, "w", zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path in sorted(source.rglob("*")):
            relative = path.relative_to(source)
            if path.is_file() and relative.parts[0] != "data":
                archive.write(path, "FileTreeViewer/" + relative.as_posix())
        root = Path(__file__).resolve().parents[1]
        archive.write(root / "docs/portable.md", "FileTreeViewer/README.md")
        archive.write(root / "docs/ko/portable.md", "FileTreeViewer/README.ko.md")
    verify_archive(destination)
    with destination.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    (options.output / "SHA256SUMS").write_text(
        f"{digest}  {destination.name}\n", encoding="ascii"
    )
    print(f"{digest}  {destination.name}")


if __name__ == "__main__":
    main()
