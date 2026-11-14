# Development, verification and builds

English | [한국어](ko/development.md)

## Run from source

Use Python 3.12 or newer. Windows CI and desktop builds use Python 3.12 x64.

```sh
python -m pip install -r requirements.txt
python main.py
```

Source execution also stores settings and history in `data/` beside the checkout.
`application_paths.py` distinguishes the application directory, PyInstaller resources
and the current working directory. See the [portable guide](portable.md) for data migration.

## Source verification

```sh
python -m pip install ruff==0.16.6
python -m ruff format --check .
python -m ruff check .
python -m compileall -q -x '(^|[/\\])(\.git|\.venv|build|dist|release)([/\\]|$)' .
python scripts/verify_core.py
python scripts/verify_gui.py
python scripts/verify_portable.py
python main.py --smoke-test
```

| Entry point | Coverage |
| --- | --- |
| `scripts/verify_core.py` | File operations, cancellation, conflicts, external-change protection, Snapshots, Plan Apply/Undo/Rollback/locks, synchronization and tree export |
| `scripts/verify_gui.py` | Background sorting/paging, stale work cancellation, folder monitoring, restored selection, search and tree preview/copy |
| `scripts/verify_portable.py` | Resource/data paths, portable preferences/history, safe legacy migration and read-only errors on Linux |
| `main.py --smoke-test` | English/Korean Real, Virtual and Plan startup/shutdown with temporary settings and icon loading |
| `scripts/verify_windows.py` | Actual x64 EXE, Korean/space paths, other working directories, relocated settings/history, ZIP structure/checksum and extracted execution |

Checks create and remove temporary fixtures. User files and settings are not used.
Set `QT_QPA_PLATFORM=offscreen` for GUI verification without a desktop. The program
ZIP excludes verification scripts, fixtures, development environments and user data.

## Windows portable build

```powershell
.\build_windows.bat
```

The wrapper runs from its own directory, creates a temporary environment, verifies
source behavior, builds with PyInstaller `--onedir`, verifies EXE/relocation and the
extracted ZIP, then removes intermediate files.

| Path | Contents |
| --- | --- |
| `dist/FileTreeViewer/` | EXE and `_internal` runtime |
| `release/FileTreeViewer-1.0.0-windows-x64.zip` | Program folder and English/Korean guide |
| `release/SHA256SUMS` | ZIP SHA-256 |

## CI and generated files

Quality (`.github/workflows/quality.yml`) runs source checks on Windows Python 3.12.
Build Windows (`build-windows.yml`) retains its separate build triggers and uses the
same scripts as the local wrapper. It verifies the EXE and extracted ZIP and retains
`FileTreeViewer-windows-x64` for 14 days. The source checks before packaging are kept
so manual/tag builds also validate their inputs. Portable output remains under
`release/`; this is a build directory, not a version-history document.

Local environments, caches, build output, `data/` and per-root `.file-tree-viewer/`
recovery storage are ignored by Git.

## Source layout

| Location | Responsibility |
| --- | --- |
| `fs_paths.py`, `fs_ops.py`, `file_operations.py` | Shared path checks and file mutations |
| `snapshot*.py` | Snapshot scanning, storage and metadata comparison |
| `planning.py`, `plan_*.py` | Plan models, dependencies, staging, simulation and transactions |
| `journal.py`, `recovery.py`, `recovery_history.py` | Mutation logs, Rollback/Undo and interrupted recovery |
| `file_content.py`, `file_preview.py`, `file_comparison.py`, `file_metadata.py` | Bounded reading, previews, comparison and metadata |
| `analysis.py`, `diagnostics.py`, `duplicate_management.py`, `reports.py` | Structure/duplicate analysis and report export |
| `disk_usage.py`, `operation_history.py` | Disk accounting and persistent audit index |
| `folder_compare.py`, `sync_*.py` | Multi-root comparison, synchronization, baselines and previews |
| `real/` | Qt filesystem explorer models and workers |
| `virtual/` | Virtual tree storage, models and workspace operations |
| `ui/` | Presentation, dialogs, controllers and worker adapters |
| `scripts/`, `build_windows.bat` | Verification/build entry points and Windows wrapper |

Root modules are shared by Real, Virtual and Plan. Keep their existing imports and
PyInstaller entry point rather than relocating the application into a package.
`ui/window.py` coordinates the shared explorer and Real/Virtual lifetimes;
`ui/plan.py` coordinates Plan tasks, cancellation and recovery. Stateful controllers
are retained rather than split solely for line count.

Snapshots/search/comparison use SQLite and page caches. Trees load children lazily;
path lookups are batched. Scanning, searching, previews, comparison, analysis,
serialization, simulation and application run through workers with coordinated shutdown.

`assets/app-icon-source.png` is the editable source; `app-icon.png` supplies the app
and window icon, while `app-icon.ico` supplies the Windows executable icon. The source
image is excluded from portable output. Translations remain in `ui/translations/ko.json`.


`ui/plan_models.py` owns the paged Snapshot and Plan table models. Both the Plan
controller and synchronization previews import these models directly, avoiding a
preview-to-controller dependency. Existing `ui.plan` model imports remain available.

## Scope and limitations

- Automatic checks use local temporary folders. Network drives, device disconnection,
  power failure, native Explorer drag interactions and physical DPI setups require
  environment-specific verification.
- Linux can verify UI and data safety; Windows executable verification requires Windows.
- Single-root Plans cannot move across filesystems. Multi-root synchronization stages
  destination copies and coordinates local transactions.
- Snapshot comparison uses metadata and is not a content backup. Duplicate and sync
  checks use SHA-256 separately.
- Recovery data remains under target roots, separately from application `data/`. It
  does not replace backups against OS, filesystem or device failure.
