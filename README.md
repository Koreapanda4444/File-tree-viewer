# File Tree Viewer

English | [한국어](README.ko.md)

File Tree Viewer is a Windows desktop application for browsing real files,
editing virtual folder trees and reviewing changes before applying them.
Snapshots and recoverable Plans help organize files while keeping changes visible.

## Preview

![Real file workspace](docs/images/real.png)

![Plan workspace](docs/images/plan.png)

## Features

- Real: folder tabs, favorites, name/content search, file previews and file operations with Undo.
- Virtual: tree and text editing, JSON save/load and export to real folders.
- Plan: saved Snapshots, change previews, conflict review, Apply, Rollback and recovery.
- Folder comparison, one-way/bidirectional synchronization, disk usage and duplicate analysis.
- Folder-tree export, operation/recovery history and reports.
- English/Korean UI and light/dark/system themes.

## Getting Started

Use Python 3.12 or newer from the repository folder:

```sh
python -m pip install -r requirements.txt
python main.py
```

On Windows, `build_windows.bat` creates
`dist/FileTreeViewer/FileTreeViewer.exe`. Keep the whole program folder,
including `_internal/`, together. A built copy does not require Python.
See the portable guide for settings, updates and moving the application.

## Documentation

- [Desktop usage](docs/usage.md)
- [Portable execution and data locations](docs/portable.md)

Review Diff and conflicts before applying a Plan. Snapshots contain metadata, not
file-content backups. Preserve each target root's `.file-tree-viewer` recovery data
and keep independent backups of important files. Recycle-bin deletion is outside Real Undo.
