from __future__ import annotations
from pathlib import Path
from typing import Optional, Any

from tkinter import filedialog, messagebox

import ui_text as T
from backend_real import RealFSBackend
from backend_vm import VirtualFSBackend
from models import UndoDelete
from undo_stack import UndoStack, UndoAction
from utils import looks_like_text, is_image_ext, read_text_head

class FileTreeController:
    def __init__(self, ui: Any):
        self.ui = ui
        self.real = RealFSBackend()
        self.vm = VirtualFSBackend()

        self.active_tab = "real"
        self.real_selected: Optional[Path] = None
        self.vm_selected: Optional[str] = None

        self.undo_real = UndoStack(maxlen=50)
        self.undo_vm = UndoStack(maxlen=50)

    # ---------- helpers ----------
    def log(self, action: str, detail: str) -> None:
        self.ui.append_log(f"{action}: {detail}")

    def _update_undo_ui(self):
        stack = self.undo_real if self.active_tab == "real" else self.undo_vm
        top = stack.peek()
        if top:
            self.ui.set_undo_status(f"{T.LBL_UNDO_PREFIX} {top.label}")
        else:
            self.ui.set_undo_status(None)

    # ---------- tab ----------
    def on_tab_change(self, tab_name: str) -> None:
        self.active_tab = "real" if tab_name == T.TAB_REAL else "vm"
        self.ui.clear_details()
        self.ui.clear_preview()
        self._update_undo_ui()

    # ---------- undo ----------
    def undo(self):
        stack = self.undo_real if self.active_tab == "real" else self.undo_vm
        action = stack.pop()
        if not action:
            messagebox.showinfo(T.APP_TITLE, "Nothing to undo.")
            self._update_undo_ui()
            return
        try:
            detail = action.undo() or ""
            self.log("Undo", f"{action.label} {detail}".strip())
            if self.active_tab == "real":
                self.refresh_real()
            else:
                self.refresh_vm()
            self._update_undo_ui()
        except Exception as e:
            messagebox.showerror(T.APP_TITLE, str(e))
            self._update_undo_ui()

    # ---------- real root / refresh ----------
    def select_root_real(self):
        folder = filedialog.askdirectory(title=T.BTN_SELECT_ROOT)
        if not folder:
            return
        root = Path(folder)
        self.real.set_root(root)
        self.ui.set_root_label(str(root))
        self.undo_real.clear()
        self._update_undo_ui()
        self.log("Root", str(root))
        self.refresh_real()

    def refresh_real(self):
        if not self.real.root:
            return
        self.ui.build_tree_real(self.real.root, self.real)

    # ---------- vm refresh ----------
    def refresh_vm(self):
        self.ui.build_tree_vm(self.vm.root_id, self.vm)

    # ---------- selection / preview ----------
    def real_on_select_path(self, p: Path):
        self.real_selected = p
        meta = self.real.get_meta(p)
        self.ui.update_details(meta)

        # Preview
        if p.is_dir():
            self.ui.clear_preview()
            return

        ext = p.suffix.lower()
        if looks_like_text(ext):
            try:
                text = read_text_head(p)
                self.ui.set_preview_text(text)
            except Exception as e:
                self.ui.set_preview_text(f"(preview error) {e}")
        elif is_image_ext(ext):
            self.ui.set_preview_image(str(p))
        else:
            self.ui.set_preview_text("(No preview for this file type)")

    def vm_on_select_node(self, node_id: str):
        self.vm_selected = node_id
        meta = self.vm.get_meta(node_id)
        self.ui.update_details(meta)

        n = self.vm.nodes[node_id]
        if n.is_dir:
            self.ui.clear_preview()
        else:
            self.ui.set_preview_text(n.content if n.content else "(empty file)")

    # ---------- Real ops ----------
    def _real_current_folder(self) -> Path:
        if not self.real.root:
            raise ValueError("Select a root folder first.")
        if self.real_selected and self.real_selected.exists() and self.real_selected.is_dir():
            return self.real_selected
        return self.real.root

    def real_new_folder(self):
        try:
            parent = self._real_current_folder()
        except Exception as e:
            messagebox.showinfo(T.APP_TITLE, str(e)); return
        name = self.ui.prompt("New Folder", "Folder name:")
        if not name:
            return
        try:
            newp = self.real.make_folder(parent, name)
            self.undo_real.push(UndoAction(
                label=f"Create folder {newp.name}",
                undo=lambda p=newp: (self.real.delete_permanently(p), None)[1]
            ))
            self._update_undo_ui()
            self.log("Created folder", str(newp))
            self.refresh_real()
        except Exception as e:
            messagebox.showerror(T.APP_TITLE, str(e))

    def real_new_file(self):
        try:
            parent = self._real_current_folder()
        except Exception as e:
            messagebox.showinfo(T.APP_TITLE, str(e)); return
        name = self.ui.prompt("New File", "File name:")
        if not name:
            return
        try:
            newp = self.real.make_file(parent, name, content="")
            self.undo_real.push(UndoAction(
                label=f"Create file {newp.name}",
                undo=lambda p=newp: (self.real.delete_permanently(p), None)[1]
            ))
            self._update_undo_ui()
            self.log("Created file", str(newp))
            self.refresh_real()
        except Exception as e:
            messagebox.showerror(T.APP_TITLE, str(e))

    def real_rename(self):
        p = self.real_selected
        if not p:
            return
        new_name = self.ui.prompt("Rename", f"New name for {p.name}:")
        if not new_name:
            return
        try:
            old = p
            newp = self.real.rename(p, new_name)
            self.real_selected = newp
            self.undo_real.push(UndoAction(
                label=f"Rename {newp.name}",
                undo=lambda src=newp, old_name=old.name: (self.real.rename(src, old_name), None)[1]
            ))
            self._update_undo_ui()
            self.log("Renamed", f"{old} -> {newp}")
            self.refresh_real()
        except Exception as e:
            messagebox.showerror(T.APP_TITLE, str(e))

    def real_delete(self):
        p = self.real_selected
        if not p:
            return
        if not messagebox.askyesno(T.APP_TITLE, f"Move to trash?\n\n{p}"):
            return
        try:
            undo = self.real.delete_to_trash(p)
            self.undo_real.push(UndoAction(
                label=f"Delete {Path(undo.src_path).name}",
                undo=lambda u=undo: (self.real.restore_from_trash(u), None)[1]
            ))
            self._update_undo_ui()
            self.log("Deleted (to trash)", str(p))
            self.refresh_real()
        except Exception as e:
            messagebox.showerror(T.APP_TITLE, str(e))

    def real_move_to(self, dst_folder: Path):
        src = self.real_selected
        if not src or not self.real.root:
            return
        try:
            old_parent = src.parent
            newp = self.real.move(src, dst_folder)
            self.real_selected = newp
            self.undo_real.push(UndoAction(
                label=f"Move {newp.name}",
                undo=lambda p=newp, back=old_parent: (self.real.move(p, back), None)[1]
            ))
            self._update_undo_ui()
            self.log("Moved", f"{src} -> {newp}")
            self.refresh_real()
        except Exception as e:
            messagebox.showerror(T.APP_TITLE, str(e))

    def real_move_dialog(self):
        if not self.real_selected or not self.real.root:
            return
        dst = filedialog.askdirectory(title="Move to folder (inside root)")
        if not dst:
            return
        self.real_move_to(Path(dst))

    def real_copy_to(self, dst_folder: Path):
        src = self.real_selected
        if not src or not self.real.root:
            return
        try:
            newp = self.real.copy(src, dst_folder)
            self.undo_real.push(UndoAction(
                label=f"Copy {src.name}",
                undo=lambda p=newp: (self.real.delete_permanently(p), None)[1]
            ))
            self._update_undo_ui()
            self.log("Copied", f"{src} -> {newp}")
            self.refresh_real()
        except Exception as e:
            messagebox.showerror(T.APP_TITLE, str(e))

    def real_copy_dialog(self):
        if not self.real_selected or not self.real.root:
            return
        dst = filedialog.askdirectory(title="Copy to folder (inside root)")
        if not dst:
            return
        self.real_copy_to(Path(dst))

    # ---------- VM ops (snapshot undo) ----------
    def _vm_snapshot(self, label: str):
        snap = self.vm.to_json()
        self.undo_vm.push(UndoAction(
            label=label,
            undo=lambda s=snap: (self.vm.load_json(s), None)[1]
        ))
        self._update_undo_ui()

    def vm_new_folder(self):
        parent_id = self.vm_selected if self.vm_selected and self.vm.nodes[self.vm_selected].is_dir else self.vm.root_id
        name = self.ui.prompt("New VM Folder", "Folder name:")
        if not name:
            return
        try:
            self._vm_snapshot("VM create folder")
            nid = self.vm.make_folder(parent_id, name)
            self.log("VM folder", self.vm.get_path(nid))
            self.refresh_vm()
        except Exception as e:
            messagebox.showerror(T.APP_TITLE, str(e))

    def vm_new_file(self):
        parent_id = self.vm_selected if self.vm_selected and self.vm.nodes[self.vm_selected].is_dir else self.vm.root_id
        name = self.ui.prompt("New VM File", "File name:")
        if not name:
            return
        try:
            self._vm_snapshot("VM create file")
            nid = self.vm.make_file(parent_id, name, "")
            self.log("VM file", self.vm.get_path(nid))
            self.refresh_vm()
        except Exception as e:
            messagebox.showerror(T.APP_TITLE, str(e))

    def vm_rename(self):
        nid = self.vm_selected
        if not nid or nid == self.vm.root_id:
            return
        new_name = self.ui.prompt("Rename VM", f"New name for {self.vm.nodes[nid].name}:")
        if not new_name:
            return
        try:
            self._vm_snapshot("VM rename")
            self.vm.rename(nid, new_name)
            self.log("VM rename", self.vm.get_path(nid))
            self.refresh_vm()
        except Exception as e:
            messagebox.showerror(T.APP_TITLE, str(e))

    def vm_delete(self):
        nid = self.vm_selected
        if not nid or nid == self.vm.root_id:
            return
        if not messagebox.askyesno(T.APP_TITLE, f"Delete from VM?\n\n{self.vm.get_path(nid)}"):
            return
        try:
            self._vm_snapshot("VM delete")
            path = self.vm.get_path(nid)
            self.vm.delete(nid)
            self.vm_selected = None
            self.log("VM deleted", path)
            self.refresh_vm()
            self.ui.clear_details()
            self.ui.clear_preview()
        except Exception as e:
            messagebox.showerror(T.APP_TITLE, str(e))

    def vm_move_to(self, dst_id: str):
        nid = self.vm_selected
        if not nid or nid == self.vm.root_id:
            return
        try:
            self._vm_snapshot("VM move")
            self.vm.move(nid, dst_id)
            self.log("VM moved", self.vm.get_path(nid))
            self.refresh_vm()
        except Exception as e:
            messagebox.showerror(T.APP_TITLE, str(e))

    def vm_copy_to(self, dst_id: str):
        nid = self.vm_selected
        if not nid:
            return
        try:
            self._vm_snapshot("VM copy")
            new_id = self.vm.copy(nid, dst_id)
            self.log("VM copied", self.vm.get_path(new_id))
            self.refresh_vm()
        except Exception as e:
            messagebox.showerror(T.APP_TITLE, str(e))

    def vm_save(self):
        fp = filedialog.asksaveasfilename(title=T.VM_BTN_SAVE, defaultextension=".json", filetypes=[("JSON","*.json")])
        if not fp:
            return
        try:
            Path(fp).write_text(self.vm.to_json(), encoding="utf-8")
            self.log("VM saved", fp)
        except Exception as e:
            messagebox.showerror(T.APP_TITLE, str(e))

    def vm_load(self):
        fp = filedialog.askopenfilename(title=T.VM_BTN_LOAD, filetypes=[("JSON","*.json"),("All files","*.*")])
        if not fp:
            return
        try:
            s = Path(fp).read_text(encoding="utf-8")
            self._vm_snapshot("VM load")
            self.vm.load_json(s)
            self.vm_selected = None
            self.log("VM loaded", fp)
            self.refresh_vm()
            self.ui.clear_details()
            self.ui.clear_preview()
        except Exception as e:
            messagebox.showerror(T.APP_TITLE, str(e))

    def vm_clear(self):
        if not messagebox.askyesno(T.APP_TITLE, "Reset VM to default sample tree?"):
            return
        self._vm_snapshot("VM reset")
        self.vm.reset()
        self.vm_selected = None
        self.log("VM", "reset")
        self.refresh_vm()
        self.ui.clear_details()
        self.ui.clear_preview()

    def vm_export_to_real(self):
        if not self.real.root:
            messagebox.showinfo(T.APP_TITLE, "Select a Real root first, then export.")
            return
        dst = filedialog.askdirectory(title=T.VM_BTN_EXPORT)
        if not dst:
            return
        dst_path = Path(dst)
        total = len(self.vm.nodes) - 1
        if not messagebox.askyesno(T.APP_TITLE, f"Export VM tree into:\n{dst_path}\n\nNodes: {total}\n\nProceed?"):
            return
        try:
            self._export_vm_node(self.vm.root_id, dst_path)
            self.log("Exported VM", str(dst_path))
            self.refresh_real()
        except Exception as e:
            messagebox.showerror(T.APP_TITLE, str(e))

    def _export_vm_node(self, node_id: str, dst_folder: Path):
        for cid, is_dir in self.vm.list_children(node_id):
            node = self.vm.nodes[cid]
            target = dst_folder / node.name
            if is_dir:
                target.mkdir(parents=True, exist_ok=True)
                self._export_vm_node(cid, target)
            else:
                target.write_text(node.content, encoding="utf-8")
