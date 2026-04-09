from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk, Menu
from typing import Any, Dict, List, Optional
from pathlib import Path

import customtkinter as ctk
import ui_text as T
from utils import format_ts, format_bytes, looks_like_text

try:
    from PIL import Image
except Exception:
    Image = None


class SimplePrompt(ctk.CTkToplevel):
    def __init__(self, master, title: str, message: str):
        super().__init__(master)
        self.title(title)
        self.resizable(False, False)
        self.result: Optional[str] = None

        self.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self, text=message).grid(row=0, column=0, padx=16, pady=(14, 8), sticky="w")
        self.entry = ctk.CTkEntry(self, width=360)
        self.entry.grid(row=1, column=0, padx=16, pady=(0, 12), sticky="ew")
        self.entry.focus_set()

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.grid(row=2, column=0, padx=16, pady=(0, 14), sticky="e")
        ctk.CTkButton(btns, text=T.DIALOG_OK, width=90, command=self._ok).grid(row=0, column=0, padx=(0, 8))
        ctk.CTkButton(btns, text=T.DIALOG_CANCEL, width=90, fg_color="#666666", hover_color="#777777", command=self._cancel).grid(row=0, column=1)

        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self._cancel())
        self.grab_set()

    def _ok(self):
        v = self.entry.get().strip()
        self.result = v if v else None
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class TextEditor(ctk.CTkToplevel):
    def __init__(self, master, title: str, filename: str, initial: str):
        super().__init__(master)
        self.title(f"{title} — {filename}")
        self.geometry("820x560")
        self.minsize(720, 480)
        self.result: Optional[str] = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self)
        header.grid(row=0, column=0, sticky="ew", padx=12, pady=12)
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text=filename, font=ctk.CTkFont(size=15, weight="bold")).grid(row=0, column=0, sticky="w", padx=10, pady=10)

        btns = ctk.CTkFrame(header, fg_color="transparent")
        btns.grid(row=0, column=1, sticky="e", padx=10, pady=10)
        ctk.CTkButton(btns, text=T.EDITOR_SAVE, width=110, command=self._save).grid(row=0, column=0, padx=(0, 8))
        ctk.CTkButton(btns, text=T.EDITOR_CANCEL, width=110, fg_color="#666666", hover_color="#777777", command=self._cancel).grid(row=0, column=1)

        self.text = ctk.CTkTextbox(self)
        self.text.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self.text.insert("1.0", initial)

        self.bind("<Control-s>", lambda e: self._save())
        self.bind("<Escape>", lambda e: self._cancel())
        self.grab_set()

    def _save(self):
        self.result = self.text.get("1.0", "end-1c")
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")

        self.title(T.APP_TITLE)
        self.geometry("1500x920")
        self.minsize(1180, 760)

        self.controller: Any = None
        self.var_show_hidden = tk.BooleanVar(value=False)
        self.var_filter_mode = tk.BooleanVar(value=False)

        self._configure_ttk()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.tabs = ctk.CTkTabview(self)
        self.tabs.grid(row=0, column=0, sticky="nsew", padx=14, pady=(14, 8))
        self.tabs.add(T.TAB_REAL)
        self.tabs.add(T.TAB_VM)
        self.tabs.set(T.TAB_REAL)
        self.tabs.configure(command=self._on_tab_changed)

        self.real_tab = self.tabs.tab(T.TAB_REAL)
        self.vm_tab = self.tabs.tab(T.TAB_VM)
        for tab in (self.real_tab, self.vm_tab):
            tab.grid_columnconfigure(0, weight=1)
            tab.grid_rowconfigure(1, weight=1)

        self._build_real()
        self._build_vm()
        self._build_bottom()

        # shortcuts
        self.bind_all("<Control-z>", lambda e: self._on_undo())
        self.bind_all("<Delete>", lambda e: self._on_delete())
        self.bind_all("<F2>", lambda e: self._on_rename())

        self._drag_item_real = None
        self._drag_item_vm = None

        self._real_filtered = False
        self._vm_filtered = False

    def set_controller(self, controller: Any):
        self.controller = controller
        self.controller.refresh_vm()

    # ---------- styling ----------
    def _configure_ttk(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Treeview",
            background="#1f2937",
            fieldbackground="#1f2937",
            foreground="#f3f4f6",
            rowheight=30,
            borderwidth=0,
            font=("Segoe UI", 11),
        )
        style.map("Treeview", background=[("selected", "#1f6aa5")], foreground=[("selected", "#ffffff")])

    # ---------- layout blocks ----------
    def _toolbar(self, parent, is_vm: bool):
        bar = ctk.CTkFrame(parent)
        bar.grid(row=0, column=0, sticky="ew", padx=10, pady=10)
        bar.grid_columnconfigure(2, weight=1)

        self.root_label = ctk.CTkLabel(bar, text="Root: -", anchor="w")
        self.root_label.grid(row=0, column=0, padx=(12, 10), pady=10, sticky="w")

        ctk.CTkButton(bar, text=T.BTN_SELECT_ROOT, width=160, command=self._on_select_root_real).grid(
            row=0, column=1, padx=(0, 10), pady=10
        )

        entry = ctk.CTkEntry(bar, placeholder_text=T.PLACEHOLDER_SEARCH)
        entry.grid(row=0, column=2, padx=(0, 10), pady=10, sticky="ew")
        entry.bind("<Return>", lambda e: self._on_search())
        if is_vm:
            self.vm_search_entry = entry
        else:
            self.real_search_entry = entry

        ctk.CTkButton(bar, text=T.BTN_REFRESH, width=110, command=self._on_refresh).grid(row=0, column=3, padx=(0, 10), pady=10)
        ctk.CTkCheckBox(bar, text=T.LBL_SHOW_HIDDEN, variable=self.var_show_hidden, command=self._on_toggle_hidden).grid(
            row=0, column=4, padx=(0, 12), pady=10
        )
        ctk.CTkCheckBox(bar, text=T.LBL_FILTER_MODE, variable=self.var_filter_mode, command=self._on_refresh).grid(
            row=0, column=5, padx=(0, 12), pady=10
        )
        ctk.CTkButton(bar, text=T.BTN_CLEAR_SEARCH, width=90, command=self._on_clear_search).grid(
            row=0, column=6, padx=(0, 12), pady=10
        )

        status = ctk.CTkLabel(bar, text="", anchor="w")
        status.grid(row=0, column=7, padx=(0, 12), pady=10, sticky="w")
        if is_vm:
            self.vm_search_status = status
        else:
            self.real_search_status = status

    def set_search_status(self, is_vm: bool, text: str):
        try:
            (self.vm_search_status if is_vm else self.real_search_status).configure(text=text)
        except Exception:
            pass

    def clear_search_status(self, is_vm: bool):
        self.set_search_status(is_vm, "")

    def _split(self, parent, is_vm: bool):
        body = ctk.CTkFrame(parent)
        body.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(2, weight=1)
        body.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left.grid_rowconfigure(1, weight=1)
        left.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(left, text=T.PANEL_EXPLORER, font=ctk.CTkFont(size=15, weight="bold")).grid(
            row=0, column=0, padx=12, pady=(10, 6), sticky="w"
        )

        tree_frame = ctk.CTkFrame(left, fg_color="transparent")
        tree_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        tree = ttk.Treeview(tree_frame, show="tree", selectmode="extended")
        yscroll = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=yscroll.set)
        tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        tree.tag_configure("match", background="#334155", foreground="#fde68a")

        sep = ttk.Separator(body, orient="vertical")
        sep.grid(row=0, column=1, sticky="ns")

        right = ctk.CTkFrame(body)
        right.grid(row=0, column=2, sticky="nsew", padx=(8, 0))
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(3, weight=1)

        ctk.CTkLabel(right, text=T.PANEL_DETAILS, font=ctk.CTkFont(size=15, weight="bold")).grid(
            row=0, column=0, padx=12, pady=(10, 6), sticky="w"
        )

        rows: Dict[str, ctk.CTkLabel] = {}
        box = ctk.CTkFrame(right)
        box.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        box.grid_columnconfigure(1, weight=1)

        for i, (k, lbl) in enumerate(
            [
                ("name", T.DETAIL_NAME),
                ("path", T.DETAIL_PATH),
                ("type", T.DETAIL_TYPE),
                ("size", T.DETAIL_SIZE),
                ("modified", T.DETAIL_MODIFIED),
            ]
        ):
            ctk.CTkLabel(box, text=f"{lbl}:", width=90, anchor="w").grid(row=i, column=0, padx=10, pady=6, sticky="w")
            v = ctk.CTkLabel(box, text="-", anchor="w", wraplength=520, justify="left")
            v.grid(row=i, column=1, padx=10, pady=6, sticky="w")
            rows[k] = v

        btns = ctk.CTkFrame(box, fg_color="transparent")
        btns.grid(row=6, column=0, columnspan=2, sticky="ew", padx=10, pady=(8, 4))
        for c in range(4):
            btns.grid_columnconfigure(c, weight=1)
        for idx, (txt, cmd) in enumerate(
            [(T.BTN_RENAME, self._on_rename), (T.BTN_DELETE, self._on_delete), (T.BTN_COPY, self._on_copy), (T.BTN_MOVE, self._on_move)]
        ):
            ctk.CTkButton(btns, text=txt, command=cmd).grid(row=0, column=idx, padx=6, pady=6, sticky="ew")

        btns2 = ctk.CTkFrame(box, fg_color="transparent")
        btns2.grid(row=7, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 10))
        btns2.grid_columnconfigure(0, weight=1)
        btns2.grid_columnconfigure(1, weight=1)
        ctk.CTkButton(btns2, text=T.BTN_NEW_FILE, command=self._on_new_file).grid(row=0, column=0, padx=6, pady=6, sticky="ew")
        ctk.CTkButton(btns2, text=T.BTN_NEW_FOLDER, command=self._on_new_folder).grid(row=0, column=1, padx=6, pady=6, sticky="ew")

        ctk.CTkLabel(right, text=T.PANEL_PREVIEW, font=ctk.CTkFont(size=15, weight="bold")).grid(
            row=2, column=0, padx=12, pady=(0, 6), sticky="nw"
        )
        pbox = ctk.CTkFrame(right)
        pbox.grid(row=3, column=0, sticky="nsew", padx=10, pady=(0, 10))
        pbox.grid_rowconfigure(0, weight=1)
        pbox.grid_columnconfigure(0, weight=1)

        ptext = ctk.CTkTextbox(pbox)
        ptext.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        ptext.configure(state="disabled")

        pimg = ctk.CTkLabel(pbox, text="")
        pimg.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        pimg.grid_remove()

        if is_vm:
            self.vm_tree = tree
            self._vm_detail_rows = rows
            self.vm_preview_text = ptext
            self.vm_preview_img = pimg
            self._vm_preview_ref = None
        else:
            self.tree = tree
            self._detail_rows = rows
            self.preview_text = ptext
            self.preview_img = pimg
            self._preview_ref = None

    def _build_real(self):
        self._toolbar(self.real_tab, is_vm=False)
        self._split(self.real_tab, is_vm=False)

        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<<TreeviewOpen>>", self._on_tree_open)
        self.tree.bind("<Button-3>", self._on_tree_right_click)
        self.tree.bind("<ButtonPress-1>", self._on_drag_start_real)
        self.tree.bind("<ButtonRelease-1>", self._on_drag_end_real)
        self.tree.bind("<Double-1>", self._on_tree_double_click)

        self._tree_item_to_payload: Dict[str, Optional[Path]] = {}

    def _build_vm(self):
        self._toolbar(self.vm_tab, is_vm=True)

        vmbar = ctk.CTkFrame(self.vm_tab)
        vmbar.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        for i in range(4):
            vmbar.grid_columnconfigure(i, weight=1)
        ctk.CTkButton(vmbar, text=T.VM_BTN_SAVE, command=self._on_vm_save).grid(row=0, column=0, padx=6, pady=10, sticky="ew")
        ctk.CTkButton(vmbar, text=T.VM_BTN_LOAD, command=self._on_vm_load).grid(row=0, column=1, padx=6, pady=10, sticky="ew")
        ctk.CTkButton(vmbar, text=T.VM_BTN_CLEAR, command=self._on_vm_clear).grid(row=0, column=2, padx=6, pady=10, sticky="ew")
        ctk.CTkButton(vmbar, text=T.VM_BTN_EXPORT, command=self._on_vm_export).grid(row=0, column=3, padx=6, pady=10, sticky="ew")

        vm_body = ctk.CTkFrame(self.vm_tab)
        vm_body.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 10))
        vm_body.grid_columnconfigure(0, weight=1)
        vm_body.grid_rowconfigure(0, weight=1)
        self._split(vm_body, is_vm=True)

        self.vm_tree.bind("<<TreeviewSelect>>", self._on_vm_tree_select)
        self.vm_tree.bind("<Button-3>", self._on_vm_tree_right_click)
        self.vm_tree.bind("<ButtonPress-1>", self._on_drag_start_vm)
        self.vm_tree.bind("<ButtonRelease-1>", self._on_drag_end_vm)
        self.vm_tree.bind("<Double-1>", self._on_vm_tree_double_click)

        self._vm_tree_item_to_id: Dict[str, Optional[str]] = {}

    def _build_bottom(self):
        bottom = ctk.CTkFrame(self)
        bottom.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 14))
        bottom.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(bottom, text=T.PANEL_LOG, font=ctk.CTkFont(size=15, weight="bold")).grid(
            row=0, column=0, padx=12, pady=(10, 6), sticky="w"
        )
        self.log_box = ctk.CTkTextbox(bottom, height=120)
        self.log_box.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 10))
        self.log_box.configure(state="disabled")

        self.undo_bar = ctk.CTkFrame(bottom)
        self.undo_bar.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 10))
        self.undo_bar.grid_columnconfigure(0, weight=1)
        self.undo_label = ctk.CTkLabel(self.undo_bar, text="", anchor="w")
        self.undo_label.grid(row=0, column=0, padx=10, pady=8, sticky="w")
        self.undo_btn = ctk.CTkButton(self.undo_bar, text=T.BTN_UNDO, width=120, command=self._on_undo)
        self.undo_btn.grid(row=0, column=1, padx=10, pady=8)
        self.set_undo_status(None)

    # ---------- public API ----------
    def append_log(self, line: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", line + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def set_undo_status(self, msg: Optional[str]):
        self.undo_label.configure(text=msg or "")
        self.undo_btn.configure(state="normal" if msg else "disabled")

    def set_root_label(self, root: str):
        self.root_label.configure(text=f"Root: {root}")

    def update_details(self, meta):
        target = self._detail_rows if self.tabs.get() == T.TAB_REAL else self._vm_detail_rows
        target["name"].configure(text=meta.name)
        target["path"].configure(text=meta.path)
        target["type"].configure(text=meta.type)
        target["size"].configure(text=format_bytes(meta.size_bytes))
        target["modified"].configure(text=format_ts(meta.modified_ts))

    def clear_details(self):
        target = self._detail_rows if self.tabs.get() == T.TAB_REAL else self._vm_detail_rows
        for k in target:
            target[k].configure(text="-")

    def clear_preview(self):
        self._set_tb(self.preview_text, "")
        self.preview_img.grid_remove()
        self.preview_text.grid()
        self._preview_ref = None

        self._set_tb(self.vm_preview_text, "")
        self.vm_preview_img.grid_remove()
        self.vm_preview_text.grid()
        self._vm_preview_ref = None

    def set_preview_text(self, text: str):
        if self.tabs.get() == T.TAB_REAL:
            self.preview_img.grid_remove()
            self.preview_text.grid()
            self._set_tb(self.preview_text, text)
        else:
            self.vm_preview_img.grid_remove()
            self.vm_preview_text.grid()
            self._set_tb(self.vm_preview_text, text)

    def set_preview_image(self, filepath: str):
        if Image is None:
            self.set_preview_text("(Pillow not installed)")
            return
        try:
            img = Image.open(filepath)
            img.thumbnail((680, 440))
            cimg = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
        except Exception as e:
            self.set_preview_text(f"(image preview error) {e}")
            return

        if self.tabs.get() == T.TAB_REAL:
            self.preview_text.grid_remove()
            self.preview_img.grid()
            self.preview_img.configure(image=cimg)
            self._preview_ref = cimg
        else:
            self.vm_preview_text.grid_remove()
            self.vm_preview_img.grid()
            self.vm_preview_img.configure(image=cimg)
            self._vm_preview_ref = cimg

    def prompt(self, title: str, message: str) -> Optional[str]:
        dlg = SimplePrompt(self, title, message)
        self.wait_window(dlg)
        return dlg.result

    def open_text_editor(self, title: str, filename: str, initial: str) -> Optional[str]:
        dlg = TextEditor(self, title, filename, initial)
        self.wait_window(dlg)
        return dlg.result

    def _set_tb(self, tb: ctk.CTkTextbox, text: str):
        tb.configure(state="normal")
        tb.delete("1.0", "end")
        tb.insert("1.0", text)
        tb.configure(state="disabled")

    # ---------- tree build ----------
    def build_tree_real(self, root: Path, backend):
        self._real_filtered = False
        self.tree.delete(*self.tree.get_children())
        self._tree_item_to_payload = {}
        root_item = self.tree.insert("", "end", text=str(root), open=True)
        self._tree_item_to_payload[root_item] = root
        self.clear_search_status(is_vm=False)

        dummy = self.tree.insert(root_item, "end", text="(loading...)")
        self._tree_item_to_payload[dummy] = None
        self._load_children_async(root_item, root, backend)

    def _load_children_async(self, parent_item, folder: Path, backend):
        def worker():
            try:
                children = backend.list_children(folder)
            except Exception:
                children = []

            def apply():
                try:
                    for c in self.tree.get_children(parent_item):
                        self.tree.delete(c)
                except Exception:
                    return

                for p, is_dir in children:
                    it = self.tree.insert(parent_item, "end", text=p.name, open=False)
                    self._tree_item_to_payload[it] = p
                    if is_dir:
                        d = self.tree.insert(it, "end", text="(loading...)")
                        self._tree_item_to_payload[d] = None

                q = (self.real_search_entry.get() or "").strip()
                if q and not self.var_filter_mode.get():
                    cnt = self._apply_highlight(self.tree, q)
                    self.set_search_status(is_vm=False, text=f"{T.LBL_SEARCH_STATUS_PREFIX}: {cnt}")

            self.after(0, apply)

        threading.Thread(target=worker, daemon=True).start()

    def build_tree_vm(self, root_id: str, backend):
        self._vm_filtered = False
        self.vm_tree.delete(*self.vm_tree.get_children())
        self._vm_tree_item_to_id = {}
        r = self.vm_tree.insert("", "end", text=backend.nodes[root_id].name, open=True)
        self._vm_tree_item_to_id[r] = root_id
        self.clear_search_status(is_vm=True)
        self._insert_vm(r, root_id, backend)

    def _insert_vm(self, parent_item, node_id: str, backend):
        for c in self.vm_tree.get_children(parent_item):
            self.vm_tree.delete(c)
        for cid, is_dir in backend.list_children(node_id):
            it = self.vm_tree.insert(parent_item, "end", text=backend.nodes[cid].name, open=is_dir)
            self._vm_tree_item_to_id[it] = cid
            if is_dir:
                self._insert_vm(it, cid, backend)

    # ---------- search highlight + filter ----------
    def _apply_highlight(self, tree: ttk.Treeview, query: str) -> int:
        q = query.lower().strip()
        count = 0

        def walk(item):
            nonlocal count
            txt = (tree.item(item, "text") or "")
            tree.item(item, tags=())
            if q and q in txt.lower():
                tree.item(item, tags=("match",))
                count += 1
            for c in tree.get_children(item):
                walk(c)

        for top in tree.get_children(""):
            walk(top)
        return count

    def apply_filter_real(self, query: str):
        if not self.controller or not self.controller.real.root:
            return
        query = query.strip()
        if not query:
            self.controller.refresh_real()
            self.clear_search_status(is_vm=False)
            return

        root = self.controller.real.root
        backend = self.controller.real
        self._real_filtered = True
        self.set_search_status(is_vm=False, text=f"{T.LBL_SEARCH_STATUS_PREFIX}: ...")

        def worker():
            max_nodes = 15000
            visited = 0
            matches = 0
            truncated = False

            def dfs(folder: Path):
                nonlocal visited, matches, truncated
                if visited > max_nodes:
                    truncated = True
                    return False, []
                visited += 1
                included = []
                try:
                    children = backend.list_children(folder)
                except Exception:
                    children = []
                folder_match = query.lower() in folder.name.lower()
                any_child = False
                for p, is_dir in children:
                    if visited > max_nodes:
                        truncated = True
                        break
                    if is_dir:
                        ok, kids = dfs(p)
                        if ok:
                            included.append((p, True, kids))
                            any_child = True
                    else:
                        visited += 1
                        if query.lower() in p.name.lower():
                            matches += 1
                            included.append((p, False, []))
                            any_child = True
                return folder_match or any_child, included

            _, kids = dfs(root)

            def apply():
                self.tree.delete(*self.tree.get_children())
                self._tree_item_to_payload = {}
                r = self.tree.insert("", "end", text=str(root), open=True)
                self._tree_item_to_payload[r] = root

                def insert(parent_item, nodes):
                    for p, is_dir, ckids in nodes:
                        it = self.tree.insert(parent_item, "end", text=p.name, open=True)
                        self._tree_item_to_payload[it] = p
                        if is_dir:
                            insert(it, ckids)

                insert(r, kids)
                self._apply_highlight(self.tree, query)
                suffix = f" ({T.LBL_SEARCH_STATUS_TRUNCATED})" if truncated else ""
                self.set_search_status(is_vm=False, text=f"{T.LBL_SEARCH_STATUS_PREFIX}: {matches}{suffix}")
                self.append_log(f"Filter (Real): '{query}' — matches: {matches}" + (" (partial)" if truncated else ""))

            self.after(0, apply)

        threading.Thread(target=worker, daemon=True).start()

    def apply_filter_vm(self, query: str):
        if not self.controller:
            return
        query = query.strip()
        if not query:
            self.controller.refresh_vm()
            self.clear_search_status(is_vm=True)
            return

        backend = self.controller.vm
        self._vm_filtered = True
        self.set_search_status(is_vm=True, text=f"{T.LBL_SEARCH_STATUS_PREFIX}: ...")

        def worker():
            max_nodes = 20000
            visited = 0
            matches = 0
            truncated = False

            def dfs(node_id: str):
                nonlocal visited, matches, truncated
                if visited > max_nodes:
                    truncated = True
                    return False, []
                visited += 1
                node = backend.nodes[node_id]
                node_match = query.lower() in node.name.lower()
                included = []
                any_child = False
                for cid, is_dir in backend.list_children(node_id):
                    if visited > max_nodes:
                        truncated = True
                        break
                    if is_dir:
                        ok, kids = dfs(cid)
                        if ok:
                            included.append((cid, True, kids))
                            any_child = True
                    else:
                        visited += 1
                        if query.lower() in backend.nodes[cid].name.lower():
                            matches += 1
                            included.append((cid, False, []))
                            any_child = True
                return node_match or any_child, included

            _, kids = dfs(backend.root_id)

            def apply():
                self.vm_tree.delete(*self.vm_tree.get_children())
                self._vm_tree_item_to_id = {}
                r = self.vm_tree.insert("", "end", text=backend.nodes[backend.root_id].name, open=True)
                self._vm_tree_item_to_id[r] = backend.root_id

                def insert(parent_item, nodes):
                    for cid, is_dir, ckids in nodes:
                        it = self.vm_tree.insert(parent_item, "end", text=backend.nodes[cid].name, open=True)
                        self._vm_tree_item_to_id[it] = cid
                        if is_dir:
                            insert(it, ckids)

                insert(r, kids)
                self._apply_highlight(self.vm_tree, query)
                suffix = f" ({T.LBL_SEARCH_STATUS_TRUNCATED})" if truncated else ""
                self.set_search_status(is_vm=True, text=f"{T.LBL_SEARCH_STATUS_PREFIX}: {matches}{suffix}")
                self.append_log(f"Filter (VM): '{query}' — matches: {matches}" + (" (partial)" if truncated else ""))

            self.after(0, apply)

        threading.Thread(target=worker, daemon=True).start()

    # ---------- menus ----------
    def _make_menu(self, is_vm: bool) -> Menu:
        m = Menu(self, tearoff=0)
        m.add_command(label=T.MENU_OPEN, command=self._on_menu_open)
        if not is_vm:
            m.add_command(label=T.MENU_REVEAL, command=self._on_menu_reveal)
        m.add_command(label=T.MENU_EDIT, command=self._on_menu_edit)
        m.add_separator()
        m.add_command(label=T.MENU_NEW_FILE, command=self._on_new_file)
        m.add_command(label=T.MENU_NEW_FOLDER, command=self._on_new_folder)
        m.add_separator()
        m.add_command(label=T.MENU_RENAME, command=self._on_rename)
        m.add_command(label=T.MENU_DELETE, command=self._on_delete)
        m.add_separator()
        m.add_command(label=T.MENU_COPY_PATH, command=self._on_copy_path)
        m.add_command(label=T.MENU_REFRESH, command=self._on_refresh)
        return m

    def _on_tree_right_click(self, e):
        it = self.tree.identify_row(e.y)
        if it:
            self.tree.selection_set(it)
            self.tree.focus(it)
        if not hasattr(self, "_menu_real"):
            self._menu_real = self._make_menu(False)
        self._menu_real.tk_popup(e.x_root, e.y_root)

    def _on_vm_tree_right_click(self, e):
        it = self.vm_tree.identify_row(e.y)
        if it:
            self.vm_tree.selection_set(it)
            self.vm_tree.focus(it)
        if not hasattr(self, "_menu_vm"):
            self._menu_vm = self._make_menu(True)
        self._menu_vm.tk_popup(e.x_root, e.y_root)

    def _on_copy_path(self):
        if self.tabs.get() == T.TAB_REAL:
            sel = self.tree.selection()
            if not sel:
                return
            p = self._tree_item_to_payload.get(sel[0])
            if not p:
                return
            txt = str(p)
        else:
            sel = self.vm_tree.selection()
            if not sel:
                return
            nid = self._vm_tree_item_to_id.get(sel[0])
            if not nid:
                return
            txt = self.controller.vm.get_path(nid)
        self.clipboard_clear()
        self.clipboard_append(txt)
        self.append_log(f"Copied path: {txt}")

    def _on_menu_open(self):
        if not self.controller:
            return
        if self.tabs.get() == T.TAB_REAL:
            paths = self._real_selected_paths()
            if not paths:
                return
            self.controller.real_open(paths[0])
        else:
            ids = self._vm_selected_ids()
            if not ids:
                return
            nid = ids[0]
            node = self.controller.vm.nodes.get(nid)
            if not node:
                return
            if node.is_dir:
                try:
                    it = self.vm_tree.selection()[0]
                    self.vm_tree.item(it, open=not self.vm_tree.item(it, "open"))
                except Exception:
                    pass
            else:
                self.controller.vm_edit_file(nid)

    def _on_menu_reveal(self):
        if not self.controller:
            return
        if self.tabs.get() != T.TAB_REAL:
            return
        paths = self._real_selected_paths()
        if not paths:
            return
        self.controller.real_reveal(paths[0])

    def _on_menu_edit(self):
        if not self.controller:
            return
        if self.tabs.get() == T.TAB_REAL:
            paths = self._real_selected_paths()
            if not paths:
                return
            self.controller.real_edit_text(paths[0])
        else:
            ids = self._vm_selected_ids()
            if not ids:
                return
            self.controller.vm_edit_file(ids[0])

    # ---------- drag & drop ----------
    def _on_drag_start_real(self, e):
        self._drag_item_real = self.tree.identify_row(e.y)

    def _on_drag_end_real(self, e):
        if not self.controller:
            return
        src = self._drag_item_real
        self._drag_item_real = None
        if not src:
            return
        dst = self.tree.identify_row(e.y)
        if not dst or dst == src:
            return
        sp = self._tree_item_to_payload.get(src)
        dp = self._tree_item_to_payload.get(dst)
        if not sp or not dp or not Path(dp).is_dir():
            return
        self.tree.selection_set(src)
        self.controller.real_on_select_path(sp)
        self.controller.real_move_many([sp], Path(dp))

    def _on_drag_start_vm(self, e):
        self._drag_item_vm = self.vm_tree.identify_row(e.y)

    def _on_drag_end_vm(self, e):
        if not self.controller:
            return
        src = self._drag_item_vm
        self._drag_item_vm = None
        if not src:
            return
        dst = self.vm_tree.identify_row(e.y)
        if not dst or dst == src:
            return
        sid = self._vm_tree_item_to_id.get(src)
        did = self._vm_tree_item_to_id.get(dst)
        if not sid or not did:
            return
        node = self.controller.vm.nodes.get(did)
        if not node or not node.is_dir:
            return
        self.vm_tree.selection_set(src)
        self.controller.vm_on_select_node(sid)
        self.controller.vm_move_many([sid], did)

    # ---------- double click ----------
    def _on_tree_double_click(self, e):
        if not self.controller:
            return
        it = self.tree.identify_row(e.y)
        if not it:
            return
        p = self._tree_item_to_payload.get(it)
        if not p:
            return
        p = Path(p)
        if p.is_dir():
            self.tree.item(it, open=not self.tree.item(it, "open"))
        else:
            ext = p.suffix.lower()
            if looks_like_text(ext):
                self.controller.real_edit_text(p)
            else:
                self.controller.real_open(p)

    def _on_vm_tree_double_click(self, e):
        if not self.controller:
            return
        it = self.vm_tree.identify_row(e.y)
        if not it:
            return
        nid = self._vm_tree_item_to_id.get(it)
        if not nid:
            return
        node = self.controller.vm.nodes.get(nid)
        if not node:
            return
        if node.is_dir:
            self.vm_tree.item(it, open=not self.vm_tree.item(it, "open"))
        else:
            self.controller.vm_edit_file(nid)

    # ---------- selection helpers ----------
    def _real_selected_paths(self) -> List[Path]:
        out: List[Path] = []
        for it in self.tree.selection():
            p = self._tree_item_to_payload.get(it)
            if isinstance(p, Path):
                out.append(p)
        return out

    def _vm_selected_ids(self) -> List[str]:
        out: List[str] = []
        for it in self.vm_tree.selection():
            nid = self._vm_tree_item_to_id.get(it)
            if nid:
                out.append(nid)
        return out

    # ---------- events ----------
    def _on_tab_changed(self):
        if self.controller:
            self.controller.on_tab_change(self.tabs.get())

    def _on_select_root_real(self):
        if self.controller:
            self.controller.select_root_real()

    def _on_refresh(self):
        if not self.controller:
            return
        if self.tabs.get() == T.TAB_REAL:
            q = (self.real_search_entry.get() or "").strip()
            if self.var_filter_mode.get() and q:
                self.apply_filter_real(q)
            else:
                self._real_filtered = False
                self.controller.refresh_real()
                cnt = self._apply_highlight(self.tree, q)
                self.set_search_status(is_vm=False, text=f"{T.LBL_SEARCH_STATUS_PREFIX}: {cnt}" if q else "")
        else:
            q = (self.vm_search_entry.get() or "").strip()
            if self.var_filter_mode.get() and q:
                self.apply_filter_vm(q)
            else:
                self._vm_filtered = False
                self.controller.refresh_vm()
                cnt = self._apply_highlight(self.vm_tree, q)
                self.set_search_status(is_vm=True, text=f"{T.LBL_SEARCH_STATUS_PREFIX}: {cnt}" if q else "")

    def _on_toggle_hidden(self):
        if not self.controller:
            return
        v = bool(self.var_show_hidden.get())
        self.controller.real.show_hidden = v
        self.controller.vm.show_hidden = v
        self._on_refresh()

    def _on_clear_search(self):
        if self.tabs.get() == T.TAB_REAL:
            self.real_search_entry.delete(0, "end")
            self.clear_search_status(is_vm=False)
        else:
            self.vm_search_entry.delete(0, "end")
            self.clear_search_status(is_vm=True)
        self._on_refresh()

    def _on_search(self):
        self._on_refresh()

    def _on_tree_select(self, _=None):
        if not self.controller:
            return
        sel = self.tree.selection()
        if not sel:
            return
        p = self._tree_item_to_payload.get(sel[0])
        if p:
            self.controller.real_on_select_path(p)

    def _on_tree_open(self, _=None):
        if not self.controller or not self.controller.real.root:
            return
        if self._real_filtered:
            return
        it = self.tree.focus()
        p = self._tree_item_to_payload.get(it)
        if not p or not isinstance(p, Path) or not p.is_dir():
            return
        children = self.tree.get_children(it)
        if len(children) == 1 and self.tree.item(children[0], "text") == "(loading...)":
            self._load_children_async(it, p, self.controller.real)

    def _on_vm_tree_select(self, _=None):
        if not self.controller:
            return
        sel = self.vm_tree.selection()
        if not sel:
            return
        nid = self._vm_tree_item_to_id.get(sel[0])
        if nid:
            self.controller.vm_on_select_node(nid)

    def _on_new_folder(self):
        if not self.controller:
            return
        (self.controller.real_new_folder if self.tabs.get() == T.TAB_REAL else self.controller.vm_new_folder)()

    def _on_new_file(self):
        if not self.controller:
            return
        (self.controller.real_new_file if self.tabs.get() == T.TAB_REAL else self.controller.vm_new_file)()

    def _on_rename(self):
        if not self.controller:
            return
        if self.tabs.get() == T.TAB_REAL:
            ps = self._real_selected_paths()
            if not ps:
                return
            self.controller.real_on_select_path(ps[0])
            self.controller.real_rename()
        else:
            ids = self._vm_selected_ids()
            if not ids:
                return
            self.controller.vm_on_select_node(ids[0])
            self.controller.vm_rename()

    def _on_delete(self):
        if not self.controller:
            return
        if self.tabs.get() == T.TAB_REAL:
            self.controller.real_delete_many(self._real_selected_paths())
        else:
            self.controller.vm_delete_many(self._vm_selected_ids())

    def _on_copy(self):
        if not self.controller:
            return
        if self.tabs.get() == T.TAB_REAL:
            self.controller.real_copy_dialog(self._real_selected_paths())
        else:
            ids = self._vm_selected_ids()
            if not ids:
                return
            dst = self.prompt("Copy VM", "Destination folder path (VM:/...):")
            if not dst:
                return
            dst_id = self.vm_path_to_id(dst.strip(), self.controller.vm)
            if not dst_id:
                return
            self.controller.vm_copy_many(ids, dst_id)

    def _on_move(self):
        if not self.controller:
            return
        if self.tabs.get() == T.TAB_REAL:
            self.controller.real_move_dialog(self._real_selected_paths())
        else:
            ids = self._vm_selected_ids()
            if not ids:
                return
            dst = self.prompt("Move VM", "Destination folder path (VM:/...):")
            if not dst:
                return
            dst_id = self.vm_path_to_id(dst.strip(), self.controller.vm)
            if not dst_id:
                return
            self.controller.vm_move_many(ids, dst_id)

    def _on_undo(self):
        if self.controller:
            self.controller.undo()

    def _on_vm_save(self):
        if self.controller:
            self.controller.vm_save()

    def _on_vm_load(self):
        if self.controller:
            self.controller.vm_load()

    def _on_vm_clear(self):
        if self.controller:
            self.controller.vm_clear()

    def _on_vm_export(self):
        if self.controller:
            self.controller.vm_export_to_real()

    def vm_path_to_id(self, vm_path: str, backend) -> Optional[str]:
        if not vm_path.startswith("VM:/"):
            return None
        parts = [p for p in vm_path[4:].split("/") if p]
        cur = backend.root_id
        for part in parts:
            found = None
            for cid in backend.nodes[cur].children:
                if backend.nodes[cid].name == part:
                    found = cid
                    break
            if not found:
                return None
            cur = found
        return cur
