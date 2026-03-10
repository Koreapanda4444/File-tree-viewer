# File Tree Viewer (Real + VM)

Two-tab file tree tool:

- **Real File System**: browse & modify files under a selected root (safe root enforcement)
- **Virtual File System (VM)**: experiment with an in-memory tree, save/load JSON, export into Real

## Added in this version
- Double-click:
  - Real: open file in default app
  - VM: edit file content in an editor modal
- Async filesystem loading (Real): folder expansion loads in a background thread
- Multi-select + bulk ops: delete/move/copy

## UX features
- Right-click context menu (Real/VM)
- Drag & drop move (drop onto folders)
- Shortcuts: **F2 rename**, **Del delete**, **Ctrl+Z undo**
- Multi-step Undo
- Preview panel (text/image)
- Search highlight + filter mode

## Run
```bash
pip install -r requirements.txt
python main.py
```
