# File Tree Viewer

English | [한국어](README.ko.md)

## Overview

File Tree Viewer is a Python and PySide6 Windows application that combines a real
file explorer, a virtual folder-tree editor and a Snapshot-based change planner.
Manage files directly, sketch a directory structure independently, or prepare a
set of changes and inspect the result before applying it to disk.

## Features

### Real File System

Browse folders through tabs, breadcrumbs, favorites and an editable address bar.
Search file names or contents, preview text, images and binary data, edit text,
compare text files and inspect file metadata. Copy, move, rename and other file
commands work directly on the selected files, with session Undo for supported operations.

### Virtual File System

Create and edit a folder tree and text files in memory without changing real files.
Save and reopen the structure as JSON, export it to a real folder, or stage its
contents in Plan. This workspace is useful for drafting a project layout before
creating it on disk.

### Plan

Take a Snapshot of a folder, stage creates, copies, moves, renames and deletions,
and inspect the proposed structure and conflicts before applying them. Batch Rename
and organization rules help prepare groups of changes. Application records support
Rollback, Undo and interrupted-work recovery.

### Folder comparison and analysis

Compare two folders and prepare one-way or bidirectional synchronization with
conflict review. Explore disk usage through folder totals, a Treemap and large-file
lists, or review duplicate files and stage selected cleanup operations in Plan.

### Exports and workspace preferences

Preview, copy or export folder trees as TXT, Markdown or JSON. Browse operation
and recovery history and export reports as JSON, CSV or HTML. English/Korean UI,
light/dark/system themes, saved panel layouts and quick commands support everyday use.

## Usage

### Browse files or draft a structure

Open a folder in **Real** to navigate, search and preview its contents. Use **Virtual**
to build a separate tree, save it as JSON and export it when ready.
**Ctrl+1 / Ctrl+2 / Ctrl+3** switches between Real, Virtual and Plan.

### Plan a group of changes

1. In **Plan**, choose **Import Snapshot** and select the folder to organize.
   The Snapshot records its structure and metadata for planning.
2. Add new files/folders, renames, copies, moves or deletions to the operation list.
   These are staged changes; the original files remain unchanged at this point.
3. Open **Preview Structure** to compare the original tree with the proposed result.
4. Run **Diff / Simulate** to review conflicts, dependencies and operation details.
5. Choose **Apply All**, review the confirmation and apply the planned changes.
6. For a completed Plan, use **Undo Last Apply** when available. Use
   **Recover Interrupted Plan** to review and recover unfinished work.

Snapshots describe file metadata rather than storing file-content backups.
Saved Virtual trees, Snapshots and Plans can be reopened for later work; detailed
workspace and recovery behavior is covered in the usage guide.

## Getting Started

### Run from source

Use Python 3.12 or newer. From the repository folder:

```sh
python -m pip install -r requirements.txt
python main.py
```

### Build the Windows application

On Windows, use 64-bit Python 3.12 or newer available as `python`, and run:

```powershell
.\build_windows.bat
```

The BAT installs build dependencies into the current Python environment and creates
`dist/FileTreeViewer/FileTreeViewer.exe`. Run that EXE from the generated folder;
keep `_internal/` and the other program files together. The built application
includes Python and Qt and is created locally from this repository's source code.

Settings and operation history live in `data/` beside the EXE. Preserve that folder
when updating or moving a built copy; saved Snapshots and Plans remain at the paths
you selected. The portable guide covers data locations and migration.

## Documentation

- [Desktop usage](docs/usage.md) — workspace controls, search, comparisons, synchronization, analysis and Plan workflows.
- [Portable execution and data](docs/portable.md) — building and running the Windows folder, settings, updates and data migration.
