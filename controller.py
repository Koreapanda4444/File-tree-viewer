from __future__ import annotations
from pathlib import Path
from typing import Optional, Any
import tkinter as tk
from tkinter import filedialog, messagebox
from models import FileMeta, UndoDelete
from utils import now_ts, format_ts, format_bytes
from backend_real import RealFSBackend
from backend_vm import VirtualFSBackend
import ui_text as T

class FileTreeController:
    def __init__(self, ui: Any):
        self.ui = ui
        self.real = RealFSBackend()
        self.vm = VirtualFSBackend()

        self.active_tab = "real"  # 'real' or 'vm'
        self.real_selected: Optional[Path] = None
        self.vm_selected: Optional[str] = None

    # ---------- Logging ----------
    def log(self, action: str, detail: str) -> None:
        self.ui.append_log(f"{action}: {detail}")

    # ---------- Tab ----------
    def on_tab_change(self, tab_name: str) -> None:
        self.active_tab = "real" if tab_name == T.TAB_REAL else "vm"
        self.ui.clear_details()
        if self.active_tab == "real":
            self.ui.set_warning(None)
        else:
            self.ui.set_warning(None)

    # ---------- Real ----------
    def select_root_real(self):
        folder = filedialog.askdirectory(title=T.BTN_SELECT_ROOT)
        if not folder:
            return
        root = Path(folder)
        self.real.set_root(root)
        self.ui.set_root_label(str(root))
        self.log("Root", str(root))
        self.refresh_real()

    def refresh_real(self):
        if not self.real.root:
            return
        self.ui.build_tree_real(self.real.root, self.real)

    def real_on_select_path(self, p: Path):
        self.real_selected = p
        meta = self.real.get_meta(p)
        self.ui.update_details(meta)

    def real_new_folder(self):
        if not self.real.root:
            messagebox.showinfo(T.APP_TITLE, "Select a root folder first.")
            return
        parent = self.real_selected if self.real_selected and self.real_selected.is_dir() else self.real.root
        name = self.ui.prompt("New Folder", "Folder name:")
        if not name:
            return
        try:
            newp = self.real.make_folder(parent, name)
            self.log("Created folder", str(newp))
            self.refresh_real()
        except Exception as e:
            messagebox.showerror(T.APP_TITLE, str(e))

    def real_new_file(self):
        if not self.real.root:
            messagebox.showinfo(T.APP_TITLE, "Select a root folder first.")
            return
        parent = self.real_selected if self.real_selected and self.real_selected.is_dir() else self.real.root
        name = self.ui.prompt("New File", "File name:")
        if not name:
            return
        try:
            newp = self.real.make_file(parent, name, content="")
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
            newp = self.real.rename(p, new_name)
            self.log("Renamed", f"{p} -> {newp}")
            self.real_selected = newp
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
            self.log("Deleted (to trash)", str(p))
            self.ui.set_warning(f"{p}")
            self.refresh_real()
        except Exception as e:
            messagebox.showerror(T.APP_TITLE, str(e))

    def real_undo(self):
        try:
            restored = self.real.undo_delete()
            if restored:
                self.log("Undo delete", str(restored))
                self.ui.set_warning(None)
                self.refresh_real()
            else:
                messagebox.showinfo(T.APP_TITLE, "Nothing to undo.")
        except Exception as e:
            messagebox.showerror(T.APP_TITLE, str(e))

    def real_move(self):
        src = self.real_selected
        if not src or not self.real.root:
            return
        dst = filedialog.askdirectory(title="Move to folder (inside root)")
        if not dst:
            return
        try:
            newp = self.real.move(src, Path(dst))
            self.log("Moved", f"{src} -> {newp}")
            self.refresh_real()
        except Exception as e:
            messagebox.showerror(T.APP_TITLE, str(e))

    def real_copy(self):
        src = self.real_selected
        if not src or not self.real.root:
            return
        dst = filedialog.askdirectory(title="Copy to folder (inside root)")
        if not dst:
            return
        try:
            newp = self.real.copy(src, Path(dst))
            self.log("Copied", f"{src} -> {newp}")
            self.refresh_real()
        except Exception as e:
            messagebox.showerror(T.APP_TITLE, str(e))

    # ---------- VM ----------
    def refresh_vm(self):
        self.ui.build_tree_vm(self.vm.root_id, self.vm)

    def vm_on_select_node(self, node_id: str):
        self.vm_selected = node_id
        meta = self.vm.get_meta(node_id)
        self.ui.update_details(meta)

    def vm_new_folder(self):
        parent_id = self.vm_selected if self.vm_selected and self.vm.nodes[self.vm_selected].is_dir else self.vm.root_id
        name = self.ui.prompt("New VM Folder", "Folder name:")
        if not name:
            return
        try:
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
            path = self.vm.get_path(nid)
            self.vm.delete(nid)
            self.vm_selected = None
            self.log("VM deleted", path)
            self.refresh_vm()
            self.ui.clear_details()
        except Exception as e:
            messagebox.showerror(T.APP_TITLE, str(e))

    def vm_move(self):
        nid = self.vm_selected
        if not nid or nid == self.vm.root_id:
            return
        dst = self.ui.prompt("Move VM", "Destination folder path (VM:/...):")
        if not dst:
            return
        dst_id = self.ui.vm_path_to_id(dst.strip(), self.vm)
        if not dst_id:
            messagebox.showerror(T.APP_TITLE, "Destination path not found.")
            return
        try:
            self.vm.move(nid, dst_id)
            self.log("VM moved", self.vm.get_path(nid))
            self.refresh_vm()
        except Exception as e:
            messagebox.showerror(T.APP_TITLE, str(e))

    def vm_copy(self):
        nid = self.vm_selected
        if not nid:
            return
        dst = self.ui.prompt("Copy VM", "Destination folder path (VM:/...):")
        if not dst:
            return
        dst_id = self.ui.vm_path_to_id(dst.strip(), self.vm)
        if not dst_id:
            messagebox.showerror(T.APP_TITLE, "Destination path not found.")
            return
        try:
            new_id = self.vm.copy(nid, dst_id)
            self.log("VM copied", self.vm.get_path(new_id))
            self.refresh_vm()
        except Exception as e:
            messagebox.showerror(T.APP_TITLE, str(e))

    def vm_save(self):
        fp = filedialog.asksaveasfilename(
            title=T.VM_BTN_SAVE, defaultextension=".json", filetypes=[("JSON", "*.json")]
        )
        if not fp:
            return
        try:
            Path(fp).write_text(self.vm.to_json(), encoding="utf-8")
            self.log("VM saved", fp)
        except Exception as e:
            messagebox.showerror(T.APP_TITLE, str(e))

    def vm_load(self):
        fp = filedialog.askopenfilename(
            title=T.VM_BTN_LOAD, filetypes=[("JSON", "*.json"), ("All files", "*.*")]
        )
        if not fp:
            return
        try:
            s = Path(fp).read_text(encoding="utf-8")
            self.vm.load_json(s)
            self.vm_selected = None
            self.log("VM loaded", fp)
            self.refresh_vm()
            self.ui.clear_details()
        except Exception as e:
            messagebox.showerror(T.APP_TITLE, str(e))

    def vm_clear(self):
        if not messagebox.askyesno(T.APP_TITLE, "Reset VM to default sample tree?"):
            return
        self.vm.reset()
        self.vm_selected = None
        self.log("VM", "reset")
        self.refresh_vm()
        self.ui.clear_details()

    def vm_export_to_real(self):
        if not self.real.root:
            messagebox.showinfo(T.APP_TITLE, "Select a Real root first, then export.")
            return
        dst = filedialog.askdirectory(title=T.VM_BTN_EXPORT)
        if not dst:
            return
        dst_path = Path(dst)
        # Basic plan preview (count nodes)
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
        # export children of node into dst_folder
        for cid, is_dir in self.vm.list_children(node_id):
            node = self.vm.nodes[cid]
            target = dst_folder / node.name
            if is_dir:
                target.mkdir(parents=True, exist_ok=True)
                self._export_vm_node(cid, target)
            else:
                target.write_text(node.content, encoding="utf-8")
