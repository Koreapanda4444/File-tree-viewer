# File Tree Viewer

File Tree Viewer is a Python and PySide6 desktop tool for browsing real files,
editing virtual trees and reviewing recoverable file plans against Snapshots.

English | [한국어](README.ko.md)

## Features

- Real: folder tabs, favorites, name/content search, file operations and session Undo.
- Virtual: tree and text editing, JSON save/load and export to real folders.
- Plan: saved Snapshots, comparison, staged changes, Diff, Apply, Rollback and recovery.
- Folder comparison and one-way/bidirectional synchronization with conflict review.
- Text, image and binary previews, file comparison and metadata inspection.
- Disk usage, duplicate analysis, operation/recovery history and report export.
- Folder-tree preview, clipboard copy and TXT/Markdown/JSON export.
- English/Korean interface and light/dark/system themes.

## Install and run

From a checkout, use Python 3.12 or newer:

```sh
python -m pip install -r requirements.txt
python main.py
```

Windows x64 portable builds are available as `FileTreeViewer-windows-x64` from
successful [Build Windows runs](https://github.com/Koreapanda4444/File-tree-viewer/actions/workflows/build-windows.yml).
Extract the artifact and the complete program ZIP, then run
`FileTreeViewer/FileTreeViewer.exe`. Keep `_internal/` beside the EXE.
See the portable guide for data locations, updates and migration.

## Documentation

- [Portable execution, updates and data locations](docs/portable.md)
- [Desktop usage and screenshots](docs/usage.md)
- [Development, verification and builds](docs/development.md)

Review Diff and conflicts before applying changes. Snapshots store metadata rather
than file-content backups. Preserve per-root `.file-tree-viewer` recovery data and
keep independent backups of important files. Recycle-bin deletion is outside Real Undo.

## Development

```sh
python -m pip install ruff==0.16.6
python -m ruff format --check .
python -m ruff check .
python scripts/verify_core.py
python scripts/verify_gui.py
python scripts/verify_portable.py
python main.py --smoke-test
```

Existing verification uses temporary data. Quality and Build Windows use Python
3.12 on Windows, including executable startup, portable relocation and extracted
ZIP checks. See the development guide for build commands and platform limitations.
