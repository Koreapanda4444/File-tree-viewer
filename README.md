# File-tree-viewer (Real + VM)

A two-tab file tree tool:

- **Real File System**: browse and modify files under a selected root (safe root enforcement)
- **Virtual File System (VM)**: experiment with an in-memory tree, save/load JSON, export into Real

## Features
- Right-click context menu (Real/VM)
- Drag & drop move (drop onto folders)
- Shortcuts: **F2 rename**, **Del delete**, **Ctrl+Z undo**
- Multi-step Undo
- Preview panel:
  - Text preview (first ~50KB)
  - Image preview (png/jpg/jpeg/webp/gif) via Pillow
- Search:
  - **Highlight** matches
  - **Filter mode** shows only matching items + their parent folders

## Run
```bash
pip install -r requirements.txt
python main.py
```

## Safety (Real mode)
- All operations are restricted to the selected **root folder**
- Delete moves items into **.ftv_trash** inside the root; Undo restores
