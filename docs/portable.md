# Portable execution

English | [한국어](ko/portable.md)

## Run

1. Extract the entire `FileTreeViewer-1.0.0-windows-x64.zip`.
2. Run `FileTreeViewer/FileTreeViewer.exe`.
3. Keep `_internal/` beside the EXE. No Python installation is required.

Use a writable Windows x64 folder. Move the whole program folder if it is read-only.
Do not run inside the ZIP or copy only the executable.

## Settings and language

**Settings → Preferences** selects English/Korean and Light/Dark/System themes.
Restart after changing the language.

- `data/settings/preferences.ini`: settings, window/panel layout, favorites and recent locations.
- `data/history/operation-history.sqlite3`: operation-history index.
- Saved Virtual trees, Snapshots, Plans and reports: the locations you selected.

Only needed data folders are created during use. The history index is separate from
recovery backups. Snapshots store metadata, not file contents.

## Updates and relocation

Close the app and finish all work first.

- To move, copy the whole `FileTreeViewer` folder, including `data` and `_internal`.
- To update, extract the new ZIP into a separate folder and copy the old `data` into it.
  Keep the previous folder until the new copy is verified. Do not copy an active database.
- Check that saved paths and drive letters exist on the destination PC. Moving the app
  does not rewrite external file, Snapshot or Plan paths.
- Keep each target root's `.file-tree-viewer/transactions` and other recovery data in
  place. Multi-root synchronization recovery requires every participating drive.

## Import previous data

If legacy settings or history are found, the app offers to copy them on first startup.
You can also use **Settings → Import Previous Data**.

Original data is preserved. Only preferences missing from the portable settings are
copied; an existing portable history database is neither overwritten nor merged.
Restart after importing. On failure, check write permissions and retry; the original
remains available.

## File changes and recovery

Real manages actual files. Virtual edits in-memory data until export. In Plan, prepare
a Snapshot and changes, then review Diff, conflicts and previews before applying.
Keep separate backups for important files.

Plan and synchronization Journals, Mutation logs, backups and locks stay under each
target root's `.file-tree-viewer`. Removing them can prevent Undo or interrupted
recovery. External modifications or unavailable drives block unsafe recovery while
preserving the data. Recycle-bin deletion is outside Real Undo.

## Checksum

Compare the ZIP's SHA-256 with `SHA256SUMS` beside it:

```powershell
Get-FileHash .\FileTreeViewer-1.0.0-windows-x64.zip -Algorithm SHA256
```
