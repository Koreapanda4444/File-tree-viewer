from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, List, Optional

from tkinter import filedialog, messagebox

import ui_text as T
from backend_real import RealFSBackend
from backend_vm import VirtualFSBackend
from undo_stack import UndoStack, UndoAction
from utils import (
    looks_like_text,
    is_image_ext,
    read_text_head,
    read_text_full,
    write_text_full,
    open_path_default,
    reveal_in_explorer,
    now_ts,
)


class FileTreeController:
    def __init__(self, ui: Any):
        self.ui = ui
        self.real = RealFSBackend()
        self.vm = VirtualFSBackend()

        self.active_tab = "real"  # 'real' or 'vm'
        self.real_selected: Optional[Path] = None
        self.vm_selected: Optional[str] = None

        self.undo_real = UndoStack(maxlen=50)
        self.undo_vm = UndoStack(maxlen=50)

    # ----------------- helpers -----------------
    def log(self, action: str, detail: str) -> None:
        self.ui.append_log(f"{action}: {detail}")

    def _update_undo_ui(self):
        stack = self.undo_real if self.active_tab == "real" else self.undo_vm
        top = stack.peek()
        self.ui.set_undo_status(f"{T.LBL_UNDO_PREFIX} {top.label}" if top else None)

    def on_tab_change(self, tab_name: str) -> None:
        self.active_tab = "real" if tab_name == T.TAB_REAL else "vm"
        self.ui.clear_details()
        self.ui.clear_preview()
        self._update_undo_ui()

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
            (self.refresh_real if self.active_tab == "real" else self.refresh_vm)()
        except Exception as e:
            messagebox.showerror(T.APP_TITLE, str(e))
        finally:
            self._update_undo_ui()

    # ----------------- root / refresh -----------------
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
        if self.real.root:
            self.ui.build_tree_real(self.real.root, self.real)

    def refresh_vm(self):
        self.ui.build_tree_vm(self.vm.root_id, self.vm)

    # ----------------- selection / preview -----------------
    def real_on_select_path(self, p: Path):
        self.real_selected = p
        meta = self.real.get_meta(p)
        self.ui.update_details(meta)

        if p.is_dir():
            self.ui.clear_preview()
            return

        ext = p.suffix.lower()
        if looks_like_text(ext):
            try:
                self.ui.set_preview_text(read_text_head(p))
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
        node = self.vm.nodes[node_id]
        if node.is_dir:
            self.ui.clear_preview()
        else:
            self.ui.set_preview_text(node.content if node.content else "(empty file)")

    # ----------------- open / reveal / edit -----------------
    def real_open(self, p: Path):
        try:
            if not p.exists():
                raise FileNotFoundError(str(p))
            open_path_default(p)
            self.log("Open", str(p))
        except Exception as e:
            messagebox.showerror(T.APP_TITLE, str(e))

    def real_reveal(self, p: Path):
        try:
            if not p.exists():
                raise FileNotFoundError(str(p))
            reveal_in_explorer(p)
            self.log("Reveal", str(p))
        except Exception as e:
            messagebox.showerror(T.APP_TITLE, str(e))

    def real_edit_text(self, p: Path):
        if p.is_dir():
            return
        ext = p.suffix.lower()
        if not looks_like_text(ext):
            messagebox.showinfo(T.APP_TITLE, "This file type is not treated as text.")
            return
        try:
            old = read_text_full(p)
        except Exception as e:
            messagebox.showerror(T.APP_TITLE, f"Cannot read file: {e}")
            return

        new_text = self.ui.open_text_editor(T.EDITOR_TITLE_REAL, p.name, old)
        if new_text is None:
            return

        try:
            write_text_full(p, new_text)
        except Exception as e:
            messagebox.showerror(T.APP_TITLE, f"Cannot write file: {e}")
            return

        self.undo_real.push(
            UndoAction(
                label=f"Edit {p.name}",
                undo=lambda path=p, content=old: (write_text_full(path, content), None)[1],
            )
        )
        self._update_undo_ui()
        self.log("Edited", str(p))
        self.refresh_real()
        self.real_on_select_path(p)

    def vm_edit_file(self, node_id: str):
        node = self.vm.nodes[node_id]
        if node.is_dir:
            return
        new_text = self.ui.open_text_editor(T.EDITOR_TITLE_VM, node.name, node.content)
        if new_text is None:
            return
        self._vm_snapshot(f"VM edit {node.name}")
        node.content = new_text
        node.updated_ts = now_ts()
        self.log("VM edit", self.vm.get_path(node_id))
        self.refresh_vm()
        self.vm_on_select_node(node_id)

    # ----------------- normalize bulk selections -----------------
    def _normalize_real_paths(self, paths: Iterable[Path]) -> List[Path]:
        uniq: List[Path] = []
        seen = set()
        for p in paths:
            try:
                rp = p.resolve()
            except Exception:
                rp = p
            if rp not in seen:
                seen.add(rp)
                uniq.append(rp)
        # remove descendants if ancestor selected
        uniq.sort(key=lambda x: len(str(x)))
        selected = set(uniq)
        result: List[Path] = []
        for p in uniq:
            cur = p.parent
            skip = False
            while True:
                if cur == cur.parent:
                    break
                try:
                    if cur.resolve() in selected:
                        skip = True
                        break
                except Exception:
                    pass
                if cur == p:
                    break
                cur = cur.parent
            if not skip:
                result.append(p)
        result.sort(key=lambda x: len(str(x)), reverse=True)
        return result

    def _normalize_vm_ids(self, ids: Iterable[str]) -> List[str]:
        uniq: List[str] = []
        seen = set()
        for nid in ids:
            if nid not in seen:
                seen.add(nid)
                uniq.append(nid)
        selected = set(uniq)
        def has_ancestor(nid: str) -> bool:
            cur = self.vm.nodes[nid].parent_id
            while cur:
                if cur in selected:
                    return True
                cur = self.vm.nodes[cur].parent_id
            return False
        out = [nid for nid in uniq if nid != self.vm.root_id and not has_ancestor(nid)]
        out.sort(key=lambda nid: len(self.vm.get_path(nid)), reverse=True)
        return out

    # ----------------- Real ops -----------------
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
            messagebox.showinfo(T.APP_TITLE, str(e))
            return
        name = self.ui.prompt("New Folder", "Folder name:")
        if not name:
            return
        try:
            newp = self.real.make_folder(parent, name)
            self.undo_real.push(UndoAction(label=f"Create folder {newp.name}", undo=lambda p=newp: (self.real.delete_permanently(p), None)[1]))
            self._update_undo_ui()
            self.log("Created folder", str(newp))
            self.refresh_real()
        except Exception as e:
            messagebox.showerror(T.APP_TITLE, str(e))

    def real_new_file(self):
        try:
            parent = self._real_current_folder()
        except Exception as e:
            messagebox.showinfo(T.APP_TITLE, str(e))
            return
        name = self.ui.prompt("New File", "File name:")
        if not name:
            return
        try:
            newp = self.real.make_file(parent, name, content="")
            self.undo_real.push(UndoAction(label=f"Create file {newp.name}", undo=lambda p=newp: (self.real.delete_permanently(p), None)[1]))
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
            self.undo_real.push(UndoAction(label=f"Rename {newp.name}", undo=lambda src=newp, old_name=old.name: (self.real.rename(src, old_name), None)[1]))
            self._update_undo_ui()
            self.log("Renamed", f"{old} -> {newp}")
            self.refresh_real()
        except Exception as e:
            messagebox.showerror(T.APP_TITLE, str(e))

    def real_delete_many(self, paths: List[Path]):
        if not paths:
            return
        if not messagebox.askyesno(T.APP_TITLE, f"Move {len(paths)} item(s) to trash?"):
            return
        paths = self._normalize_real_paths(paths)
        undos = []
        errors = []
        for p in paths:
            try:
                undos.append(self.real.delete_to_trash(p))
            except Exception as e:
                errors.append(f"{p}: {e}")

        if undos:
            def undo_all():
                restored = 0
                for u in reversed(undos):
                    try:
                        self.real.restore_from_trash(u)
                        restored += 1
                    except Exception:
                        pass
                return f"(restored {restored})"

            self.undo_real.push(UndoAction(label=f"Delete {len(undos)} item(s)", undo=undo_all))
            self._update_undo_ui()
            self.log("Deleted (to trash)", f"{len(undos)} item(s)")
            self.refresh_real()

        if errors:
            messagebox.showwarning(T.APP_TITLE, "Some items failed:\n\n" + "\n".join(errors[:10]))

    def real_move_many(self, paths: List[Path], dst_folder: Path):
        if not paths:
            return
        paths = self._normalize_real_paths(paths)
        moved = []
        errors = []
        for p in paths:
            try:
                old_parent = p.parent
                newp = self.real.move(p, dst_folder)
                moved.append((newp, old_parent))
            except Exception as e:
                errors.append(f"{p}: {e}")

        if moved:
            def undo_all():
                ok = 0
                for newp, old_parent in reversed(moved):
                    try:
                        self.real.move(newp, old_parent)
                        ok += 1
                    except Exception:
                        pass
                return f"(moved back {ok})"

            self.undo_real.push(UndoAction(label=f"Move {len(moved)} item(s)", undo=undo_all))
            self._update_undo_ui()
            self.log("Moved", f"{len(moved)} item(s) -> {dst_folder}")
            self.refresh_real()

        if errors:
            messagebox.showwarning(T.APP_TITLE, "Some items failed:\n\n" + "\n".join(errors[:10]))

    def real_copy_many(self, paths: List[Path], dst_folder: Path):
        if not paths:
            return
        paths = self._normalize_real_paths(paths)
        created = []
        errors = []
        for p in reversed(paths):
            try:
                created.append(self.real.copy(p, dst_folder))
            except Exception as e:
                errors.append(f"{p}: {e}")

        if created:
            def undo_all():
                ok = 0
                for c in reversed(created):
                    try:
                        self.real.delete_permanently(c)
                        ok += 1
                    except Exception:
                        pass
                return f"(deleted {ok} copy/copies)"

            self.undo_real.push(UndoAction(label=f"Copy {len(created)} item(s)", undo=undo_all))
            self._update_undo_ui()
            self.log("Copied", f"{len(created)} item(s) -> {dst_folder}")
            self.refresh_real()

        if errors:
            messagebox.showwarning(T.APP_TITLE, "Some items failed:\n\n" + "\n".join(errors[:10]))

    def real_move_dialog(self, paths: List[Path]):
        if not self.real.root or not paths:
            return
        dst = filedialog.askdirectory(title="Move to folder (inside root)")
        if dst:
            self.real_move_many(paths, Path(dst))

    def real_copy_dialog(self, paths: List[Path]):
        if not self.real.root or not paths:
            return
        dst = filedialog.askdirectory(title="Copy to folder (inside root)")
        if dst:
            self.real_copy_many(paths, Path(dst))

    # ----------------- VM ops -----------------
    def _vm_snapshot(self, label: str):
        snap = self.vm.to_json()
        self.undo_vm.push(UndoAction(label=label, undo=lambda s=snap: (self.vm.load_json(s), None)[1]))
        self._update_undo_ui()

    def vm_new_folder(self):
        parent_id = self.vm_selected if self.vm_selected and self.vm.nodes[self.vm_selected].is_dir else self.vm.root_id
        name = self.ui.prompt("New VM Folder", "Folder name:")
        if not name:
            return
        self._vm_snapshot("VM create folder")
        nid = self.vm.make_folder(parent_id, name)
        self.log("VM folder", self.vm.get_path(nid))
        self.refresh_vm()

    def vm_new_file(self):
        parent_id = self.vm_selected if self.vm_selected and self.vm.nodes[self.vm_selected].is_dir else self.vm.root_id
        name = self.ui.prompt("New VM File", "File name:")
        if not name:
            return
        self._vm_snapshot("VM create file")
        nid = self.vm.make_file(parent_id, name, "")
        self.log("VM file", self.vm.get_path(nid))
        self.refresh_vm()

    def vm_rename(self):
        nid = self.vm_selected
        if not nid or nid == self.vm.root_id:
            return
        new_name = self.ui.prompt("Rename VM", f"New name for {self.vm.nodes[nid].name}:")
        if not new_name:
            return
        self._vm_snapshot("VM rename")
        self.vm.rename(nid, new_name)
        self.log("VM rename", self.vm.get_path(nid))
        self.refresh_vm()

    def vm_delete_many(self, ids: List[str]):
        ids = self._normalize_vm_ids(ids)
        if not ids:
            return
        if not messagebox.askyesno(T.APP_TITLE, f"Delete {len(ids)} item(s) from VM?"):
            return
        self._vm_snapshot(f"VM delete {len(ids)} item(s)")
        for nid in ids:
            if nid in self.vm.nodes:
                self.vm.delete(nid)
        self.vm_selected = None
        self.log("VM deleted", f"{len(ids)} item(s)")
        self.refresh_vm()
        self.ui.clear_details()
        self.ui.clear_preview()

    def vm_move_many(self, ids: List[str], dst_id: str):
        ids = self._normalize_vm_ids(ids)
        if not ids:
            return
        self._vm_snapshot(f"VM move {len(ids)} item(s)")
        for nid in ids:
            self.vm.move(nid, dst_id)
        self.log("VM moved", f"{len(ids)} item(s) -> {self.vm.get_path(dst_id)}")
        self.refresh_vm()

    def vm_copy_many(self, ids: List[str], dst_id: str):
        ids = self._normalize_vm_ids(ids)
        if not ids:
            return
        self._vm_snapshot(f"VM copy {len(ids)} item(s)")
        for nid in ids[::-1]:
            self.vm.copy(nid, dst_id)
        self.log("VM copied", f"{len(ids)} item(s) -> {self.vm.get_path(dst_id)}")
        self.refresh_vm()

    def vm_save(self):
        fp = filedialog.asksaveasfilename(title=T.VM_BTN_SAVE, defaultextension=".json", filetypes=[("JSON", "*.json")])
        if fp:
            Path(fp).write_text(self.vm.to_json(), encoding="utf-8")
            self.log("VM saved", fp)

    def vm_load(self):
        fp = filedialog.askopenfilename(title=T.VM_BTN_LOAD, filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if not fp:
            return
        s = Path(fp).read_text(encoding="utf-8")
        self._vm_snapshot("VM load")
        self.vm.load_json(s)
        self.vm_selected = None
        self.log("VM loaded", fp)
        self.refresh_vm()
        self.ui.clear_details()
        self.ui.clear_preview()

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
        self._export_vm_node(self.vm.root_id, dst_path)
        self.log("Exported VM", str(dst_path))
        self.refresh_real()

    def _export_vm_node(self, node_id: str, dst_folder: Path):
        for cid, is_dir in self.vm.list_children(node_id):
            node = self.vm.nodes[cid]
            target = dst_folder / node.name
            if is_dir:
                target.mkdir(parents=True, exist_ok=True)
                self._export_vm_node(cid, target)
            else:
                target.write_text(node.content, encoding="utf-8")
