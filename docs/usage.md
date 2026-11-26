# Usage

English | [한국어](ko/usage.md)

Execution, updates and data migration are in the [portable guide](portable.md). Source run commands are in the [README](../README.md).

## Workspaces

| Workspace | Features |
| --- | --- |
| Real File System | Editable address bar, background path completion, breadcrumbs, history, favorites, quick access, folder tabs, file operations, text editing, previews and comparison |
| Virtual File System | In-memory tree editing and Undo, JSON save/load, export to a real folder, staging into Plan |
| Plan | Saved Snapshots, structural comparison, diagnostics, duplicate review, staged changes, Diff simulation, transaction Apply, Rollback, recovery and Undo |

Use **Ctrl+1 / Ctrl+2 / Ctrl+3** to switch workspaces. **Workspace** connects Real, Virtual, Plan, folder comparison, disk analysis and history. Importing or staging a plan does not apply it.

## Browse and manage files

Use **Alt+D / Ctrl+L** to edit the address; **Alt+Left / Alt+Right / Alt+Up** navigate history or the parent folder. Folder completion runs in the background, with a maximum of 128 suggestions and 50,000 inspected entries. The current-folder name field locates files as well as folders.

Folder tabs preserve individual navigation history and view mode. Details, List and Large Icons share selection. Directories are fully sorted in the background before rows are paged into view. Watched folders refresh after external changes, with selection and expansion restored where possible. File lists support **Ctrl+C/X/V**, **Ctrl+A**, **F2**, **Delete**, **Ctrl+F**, **F5**, context menus and drag-and-drop. Text editors keep their own clipboard shortcuts.

Copy and move conflicts can be renamed, skipped or stopped. **Last Operation** shows per-item outcomes. Copy cancellation is checked during content transfer and between descendants; an active filesystem call may still need to finish. Real deletions use the recycle bin; they are not restored by Real Undo. Session-based Undo for other Real operations checks for external changes.

## Search and comparison

Live search waits 350 ms, cancels previous requests and ignores stale results. Filters combine extension/type, size, available creation time, modification time and scope. Name matching supports plain text, wildcard and bounded regular expressions.

Content search streams supported text encodings including UTF-8, BOM-detected UTF-16/32 and explicitly selected UTF-16, CP949 or CP1252. It shows matching lines and positions. Limits are 64 MiB per file, 128 KiB per line and 20 matching lines per file. Binary, unreadable or unsupported files are skipped. Search history, presets and JSON/CSV result export are available under **Search Tools**.

Text comparison reads up to 2 MiB and 10,000 lines per file. Saved Snapshot comparison uses metadata; it does not back up or prove equality of file contents. Image and binary previews are bounded. Metadata inspection reports the attributes supplied by the platform and does not evaluate the full ACL.

## Tree export

Use **Workspace → Explore → Export Folder Tree** for Real folders, Virtual trees or the original Plan Snapshot. Preview and copy the hierarchy or save TXT, Markdown or JSON. Choose depth, entry limit, hidden files, sizes and excluded folders/extensions. Existing files are not overwritten. Use **Preview Structure** separately for a Plan’s proposed result.

## Plans and synchronization

Real selections, Real-to-Plan drag-and-drop and Virtual export can prepare changes in Plan. COPY and staged UTF-8 contents preserve compatibility with the existing Plan JSON format. Staged text is limited to 1 MiB per file and 64 MiB total.

Review **Diff / Simulate**, dependencies, conflicts and the before/after structure before **Apply All**. Execution validates paths and live state, records a Journal and checksummed Mutation log, and retains backups. Failure or cancellation triggers Rollback. Undo and recovery preserve files when external changes make restoration ambiguous. The single-root Plan does not move files across filesystems.

**Workspace → Compare and analyze → Compare Folders** compares separate roots, optionally using SHA-256. One-way synchronization can preserve destination-only items or explicitly plan their backed-up deletion. Bidirectional synchronization uses the last successful baseline. Without one, it does not guess whether a missing item was created or deleted; additive initialization must be chosen explicitly. Conflicts require a selected policy or manual resolution.

Synchronization is a separate multi-root coordinator with per-root transactions. A staged synchronization remains in **Plan → Multi-root Synchronization** during the app session. Review creates, overwrites, deletions and **Preview Both Folder Trees** before applying. Both roots are revalidated before changes. Cancel, Undo and interrupted recovery coordinate all participants; every drive must remain available. Recovery data is never automatically deleted. Keep independent backups for important files.

Links/reparse points, overlapping roots, ambiguous names, type conflicts, unreadable scans and conflicting recovery records block synchronization. Cross-drive transfer copies into the destination root before applying local mutations.

## Disk usage and cleanup

**Workspace → Compare and analyze → Analyze Disk Usage** scans the current Real root without modifying source files. It provides hierarchical totals, a Treemap, folder drilling, parent/path navigation, paged children, file details and **Largest 100 Files**. The table pages through 400 children at a time; the Treemap renders at most 400 large children plus a remainder tile. Empty entries remain available in the table.

Logical size counts every file path. Unique bytes count each identified hard-linked file once, assigned to the first encountered path. Allocated size uses filesystem blocks when available and otherwise estimates from logical size. Compression, deduplication and remote storage can make actual usage differ. Links, reparse points, special files and inaccessible entries are reported and skipped. `.file-tree-viewer` recovery storage is excluded. A scan is not an atomic view of concurrent filesystem changes.

**Show Selected in Real** reveals an item. **Add Selected to Plan** stages copy, move, rename or deletion after validating the Snapshot and selected paths. Choosing deletion in this dialog does not immediately delete files.

## Operation history, reports and recovery history

**Workspace → History and recovery → Operation History** records application-issued Real operations and text saves, Plan Apply/Undo/recovery, and synchronization outcomes. It does not reconstruct old Real operations or monitor changes made by other applications. Filter by UTC date, type, status, path or action; browse 250 events per page, inspect details and reveal locations.

The audit index is `data/history/operation-history.sqlite3` beside the program executable. Logging errors are reported to the application log without reversing completed file operations. The index is separate from the original per-root recovery Journals and backups.

**Export Filtered Report** streams all matching events into a new JSON, CSV or HTML file, including transaction summaries, source/target mutations and Journal references. Existing output, the audit DB and recovery storage are protected. CSV formula prefixes and HTML special characters are escaped. Cancellation discards partial output.

**Workspace → History and recovery → Recovery History** reads existing Journals under a selected root and displays up to 1,000 transactions and recorded failures. Check the selected transaction's Undo safety before requesting restoration. Synchronization checks all participant roots. Execution validates again. Interrupted transactions route to the existing Plan recovery flow. Existing staged Plan changes require review before replacement. Backup deletion is not offered here. Full content validation can take time, and closing waits for safe completion.

## Appearance and shortcuts

**Settings → Preferences** selects English or Korean (restart required), Light, Dark or System theme, preview settings and the default policy for newly staged operations. System follows the color-scheme information supplied by Qt. Comparison colors adapt to the palette. Qt icons and scaling support high DPI.

**View** controls Real, Virtual and Plan panes. Splitter sizes and visibility persist. The last pane in a split group cannot be hidden, and **Restore All Panels** restores the full layout. File-view tools and operation buttons wrap on smaller windows.

**Ctrl+P / Settings → Quick Commands** searches menus and current-workspace commands. Use arrow keys and Enter, save favorites or reuse recent commands. Enter an absolute folder path to navigate or favorite that location. Disabled commands remain unavailable. Dangerous commands require confirmation and retain the original operation checks.

## Saved Snapshots and plans

Use **Import Snapshot** to scan a root without changing its contents. **Save Snapshot**
writes a `.ftvsnapshot`; **Open Snapshot** validates it and works from a temporary copy.
Saved Snapshots can be browsed and compared even when the original root is unavailable;
applying a Plan requires that root. Comparison uses metadata, and move/rename matches
are candidates based on file identity rather than proof of identical contents.

**Save Plan / Load Plan** retain the existing JSON format. A different root is reported
and revalidated. Batch Rename and Organize Files prepare operations; rules support
JSON save/load. Dependency review distinguishes safely staged swaps from combinations
that require separate Plans. Staging and simulation do not change original files.

## Dual panes and current-folder filters

Toggle **Dual pane** or press `Ctrl+Shift+D`; `F6` focuses the other pane. Paths,
history, tabs, view, sorting, selection and filters are independent. Shortcuts and
workspace commands use the active pane. Transfers reuse the existing conflict,
results and Undo engine. Invalid same-folder or nested-source transfers are
rejected. Ctrl-drag copies, Shift-drag moves, and an unmodified drop offers a menu.
The worker owns the complete transfer, including the move, to avoid a second OS deletion.

The folder filter is non-recursive and separate from search. Name matching is
case-insensitive; extensions can be comma/space separated. Counts cover all indexed
entries under the current hidden-file policy, including unpaged matches. **Keep on
navigation** controls whether the filter survives a folder change. Recursive search
disables the local-filter controls until normal browsing resumes.

## Read-only mode, images and SHA-256

Read-only mode rejects actual file changes at shared engine boundaries, including
deferred workers. Finish/cancel active mutations before changing mode. In-memory
Virtual editing, Plan staging and saving new reports, Snapshots or workspace JSON
remain available. Exporting Virtual contents into an actual directory is blocked.
This does not change OS permissions or prevent other applications from writing.

Image zoom uses the decoded frame, preserves aspect ratio and keeps smooth
rendering. Use the wheel/buttons, 100%, Fit and scroll/drag panning. Frames above
8 million pixels are downsampled for bounded memory and labeled **100% preview**.
Existing dimension, decoder allocation (64 MiB) and file-read limits remain in
force. The separate window shares the decoded image and marks it stale if the
original changes; closing releases its frame references.

SHA-256 runs as a cancellable streaming worker. Compare a 64-character hexadecimal
value against the first file, or compare two files. Results are verified against
file state before display and rechecked before comparison/copy. Subsequent file
changes invalidate the displayed result. Closing an active dialog cancels and
waits for the worker to finish.


## ZIP archive explorer

Open a Real ZIP by double-click, its **Browse ZIP…** context item, or **Workspace →
Browse ZIP…**. Enter an internal path, select a folder in the tree, or double-click
folders in the file list. The list shows names/extensions, original/compressed size
and compression method; selection details include CRC. The ZIP stays separate from
Real's file model. Closing returns to the existing Real location. **Open ZIP externally**
and Real's existing **Open** command remain available.

Only the selected member is decompressed for preview. Text, image, Hex and first-page
PDF previews are read-only. A complete read verifies CRC; a truncated read is explicitly
marked **CRC not verified**. Unsupported members show metadata and a reason.

**Extract selection…** keeps selected internal paths, including parent folders.
**Extract all…** includes the entire archive. Choose a parent directory and one **new**
output folder name. A private staging directory is published without replacement only
after all selected bytes pass validation/CRC. Cancel/error removes owned temporary output;
if external replacement makes cleanup unsafe, the error identifies the retained staging
path. Existing files/folders are never overwritten. Extraction is logged and invalidates
relevant folder totals, but is not added to session Real Undo. It is blocked by the common
read-only policy. The original ZIP is never rewritten.

Default bounds apply before directory allocation and while reading/extracting:

| Limit | Value |
| --- | --- |
| Central directory | 16 MiB |
| Total indexed entries, including implicit folders | 50,000 |
| Internal directory depth | 128 |
| Uncompressed member size | 512 MiB |
| Uncompressed selected extraction batch | 4 GiB |
| Compression ratio | 1,000:1, with a 1 MiB small-member allowance |
| Decompressed preview | 8 MiB |

Stored and Deflate members are supported. Encrypted, special/link and unsupported
compression members remain inspectable but cannot be previewed/extracted. Multi-part,
embedded/self-extracting and malformed layouts are rejected. Paths reject traversal,
absolute/drive/UNC forms, reserved Windows names, internal recovery paths and duplicate,
case-folded or Unicode-normalized collisions. Source identity, directory contents and
output boundaries are rechecked before publication. Image decoder limits also apply.

## Quick Look

Select one file in the active Real pane and press **Space**, or choose **Quick Look**
from its context menu/Workspace/Quick Commands. Space works only in explorer views;
address/search/name fields, inline editors and the editable preview keep text input.
In Quick Look, Space or Esc closes it; arrows or **Previous/Next** select neighboring
files in current sorting/filter order (including unloaded pages), or current search results.
Selection stays synchronized with its originating pane; focusing the other pane updates
Quick Look to that pane's selection. Folders clear the file preview.

The window shows the name/path, type, size, loading/error state, **Refresh** and
**Open externally**. It never edits or automatically opens files in another application.
Image zoom, fit and panning reuse `ImagePreview`. Text/image/Hex reading uses the same
bounded engines as the docked preview: up to 8 MiB for complete files, otherwise a
1 MiB partial read; Hex displays at most 4 KiB. PDF uses Qt's bundled PDF engine for
**the first page only**, with a raster no larger than 1600 × 1600 and a complete input
no larger than 8 MiB. Encrypted, damaged or larger PDFs show information/Hex instead.
No additional renderer dependency is installed.

Rapid switches coalesce to the latest request and cancel previous reads. File identity
is checked before display and every 750 ms afterward. A change/removal clears the old
result and requires Refresh. Closing cancels and waits for the worker; native decoding
or blocked OS calls may finish their current call before cancellation takes effect.

## On-demand folder size

Right-click one real folder → **Calculate folder size…**, or use the Workspace/Quick
Commands entry for the selected folder (current folder when no folder is selected).
**Cancel** stops a scan; closing waits for worker cleanup. Read-only mode permits it.
Folders are never recursively measured merely because they are displayed.

The result reports the scan interval, file/folder counts and:

- **Logical size**: regular-file length summed for every path.
- **Unique size**: hard links counted once per device/file identifier within the selected root.
  Missing identifiers are explicitly marked as estimated.
- **Allocated size**: unique regular-file blocks reported by the OS. If any needed block
  count is unavailable (including Windows configurations without `st_blocks`), the total
  says **Unavailable**, rather than substituting logical size as an exact allocated value.

Directory blocks, links, junctions/reparse points, special files and `.file-tree-viewer`
recovery data are excluded consistently with disk analysis. Access/skipped errors and
detected changes produce an explicit partial result. Metadata is checked again after
scanning, but the interval is not an atomic snapshot and cannot catch all external changes.

Only complete, uncancelled results enter an in-memory **128-entry LRU cache**, with a
**30-second TTL**. Loaded-directory watcher events, Real changes, Plan/synchronization/
recovery and extraction invalidate related totals. Directory identity is checked on reuse;
root modification time alone never proves recursive freshness. Unwatched descendant
edits may remain unnoticed until expiry. **Recalculate** always performs a new scan.

Valid totals appear with **Σ** in Size and Details, with their calculation time in the
tooltip. Expired/invalidated totals disappear. Displayed totals do not change the
existing metadata-based sorting policy. The result dialog retains its dated result with
an expiry warning and a Recalculate button. No cache or new data format is persisted.


## Tree-focused navigation

Real has one shared file model/selection across the hierarchy and the Details/List/
Large Icons folder views. Its layout selector changes the arrangement, not the data:

| Layout | Default arrangement |
| --- | --- |
| Tree Focus | Wide hierarchy with Name, Type, Size and Modified columns; details/preview alongside |
| Explorer | Tree, folder listing and details/preview |
| Compact | Folder listing first; extra panels available on request |

Use the **Folder tree** and **Details and preview** toggles or the **View** panel menus
to customize the active pane. At least one file view remains available. **View →
Explorer layout** and Quick Commands expose the same presets. Details/List/Large Icons
remain independent of the layout. Hidden controls remain reachable through **Files**,
context menus and existing shortcuts. Layout switches keep the same model, selection,
expanded nodes, scroll position and unsaved editor contents.

Layout choices are per explorer pane/workspace, shared by its folder tabs. Single-pane
and dual-pane arrangements are stored separately. Enabling dual panes keeps tree
preferences; each side can choose its own layout/tree visibility. Leaving dual panes
restores the single-pane arrangement, and re-enabling restores the previous dual setup.
F6 focuses the other pane's visible file view. Theme-aware borders and the active-pane
label identify the target of commands. Existing transfer/Undo/read-only checks apply.

The Name column elides long names in the middle; the persistent path line/tooltip and
**Copy Path** provide the full path. Headers resize interactively, scrolling works on
both axes and indentation/branch guides follow the Qt palette and DPI scaling.

### Root, listing and selection

The address bar names the explorer **root**, which anchors the hierarchy and recursive
search. Selecting a nested folder updates the adjacent listing; selecting a file keeps
the listing at its parent. The shared selection drives preview, metadata and file actions.
Pasting into an empty nested listing uses that displayed folder, not the root.

**Jump** and **Reveal selection in tree** locate nested paths under the existing root,
expand parents and scroll to the selected item. Paths outside the root use a suitable
new root. Double-click/Enter on a folder still opens it as a new root, and Back/Forward/
Up keep their existing behavior. Root-local filtering continues to affect only direct
children of the address-bar root; its tooltip identifies that scope. Open a selected
folder as the root to filter its direct children. Recursive search remains separate.
Unavailable, hidden/filtered paths and link parents produce a status explanation.

Path reveal loads only ancestor directories, with **128 levels**, **20,000 newly
published items** and a **15-second** limit. It does not recursively search the disk.
A new request cancels the old one; source model generations reject obsolete results.

### Depth expansion and collapse

Use **Tree actions**, the tree context menu or **Workspace → Tree navigation**:

- **Expand selected folder** shows its direct children.
- **Expand to depth…** uses depth 1 for direct children, depth 2 for the next level, etc.
- **Collapse selected folder** closes one branch; **Collapse subtree** also closes
  loaded descendant branches without reading additional directories.
- **Expand parent path / Reveal selection in tree** exposes the selected item's ancestors.
- **Cancel tree task** stops further expansion/collapse and cancels owned pending reads.

Expansion admits at most **8 levels**, processes at most **5,000 items** and stops
within its **30-second** task window. UI traversal runs in short batches; existing
background directory indexing/sorting and paged publication are reused. Links and
reparse points are not followed. Limits/cancellation/errors are reported explicitly;
a limited result does not claim that the entire subtree was expanded.

A very large individual directory can still take time to enumerate/sort in its worker.
Cancellation takes effect between entries or after a currently blocked OS/native call.
Automatic navigation restoration is also bounded: up to 512 expanded directories,
256 selections and 20,000 new items over 10 seconds. Up to 32 roots are remembered
in memory per pane; the active root/state is persisted across application restarts.
Collapsed/replaced nodes are retired in batches and old generations cannot replace
current model data. No file contents are changed by these navigation operations.

## Subtree structure summary

Choose a folder → **Show structure summary…** from the context menu, **Workspace →
Explore**, or Quick Commands. With a file selected it inspects the displayed parent
folder. This is the existing folder-size dialog/worker/cache with structural metadata,
not a second scanner. File/folder counts, logical/unique/available allocated bytes,
completion time, errors and partial-result state retain their existing meanings.

Maximum folder depth counts the inspected root as 0 and direct child folders as 1.
Maximum entry depth also includes files and matches Snapshot structure analysis.
Extensions use the same case normalization and size/count ordering as that analysis;
the dialog shows the largest eight groups. Links, special items and application
recovery directories are skipped consistently with disk usage, with partial results
when applicable.

A requested structure summary scans up to **500,000 items** and **128 directory levels**.
Reaching a limit produces an uncached partial result and suggests a smaller subtree.
Existing full folder-size/disk-analysis commands retain their scan scope. Summaries
reuse complete results from the 128-entry, 30-second folder cache; **Recalculate** forces
a new scan. Related watcher/file-operation/Plan/sync/recovery events invalidate results;
unrelated pane changes do not falsely invalidate an ongoing inspection. Cancellation,
unknown freshness, errors and limits never produce a certified complete cached result.

## Command locations

**Files** exposes new file/folder, rename, copy/move, delete, Undo, text comparison,
tree export and clipboard operations for the active Real pane. **Workspace** retains
Real/Virtual/Plan switching and groups tools under **Explore**, **Plan and design**,
**Compare and analyze**, **History and recovery**, **Pane transfers** and **Tree navigation**.
Virtual design, Snapshot, Plan Apply/Undo/recovery, synchronization, hashes, ZIP,
duplicate analysis and disk tools remain available. Fixed command IDs, recent commands,
favorites, legacy English/Korean aliases and existing shortcuts are preserved.
