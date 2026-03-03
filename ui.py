\
from __future__ import annotations

import tkinter as tk
from tkinter import ttk, Menu
from typing import Optional, Any
from pathlib import Path

import customtkinter as ctk

import ui_text as T
from utils import format_ts, format_bytes
from models import FileMeta

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

        lbl = ctk.CTkLabel(self, text=message)
        lbl.grid(row=0, column=0, padx=16, pady=(14, 8), sticky="w")

        self.entry = ctk.CTkEntry(self, width=340)
        self.entry.grid(row=1, column=0, padx=16, pady=(0, 12), sticky="ew")
        self.entry.focus_set()

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.grid(row=2, column=0, padx=16, pady=(0, 14), sticky="e")
        ok = ctk.CTkButton(btns, text=T.DIALOG_OK, width=90, command=self._ok)
        ok.grid(row=0, column=0, padx=(0, 8))
        cancel = ctk.CTkButton(btns, text=T.DIALOG_CANCEL, width=90, fg_color="#666666", hover_color="#777777", command=self._cancel)
        cancel.grid(row=0, column=1)

        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self._cancel())

        self.update_idletasks()
        w = self.winfo_width()
        h = self.winfo_height()
        x = master.winfo_rootx() + (master.winfo_width() - w) // 2
        y = master.winfo_rooty() + (master.winfo_height() - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.grab_set()

    def _ok(self):
        v = self.entry.get().strip()
        self.result = v if v else None
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")
        ctk.set_widget_scaling(1.08)
        ctk.set_window_scaling(1.0)

        self.title(T.APP_TITLE)
        self.geometry("1460x900")
        self.minsize(1180, 760)

        self.controller: Any = None

        self._font_section = ctk.CTkFont(size=16, weight="bold")
        self._font_body = ctk.CTkFont(size=13)
        self._font_ui = ctk.CTkFont(size=13)

        self.var_show_hidden = tk.BooleanVar(value=False)
        self.var_filter_mode = tk.BooleanVar(value=False)

        # search state per tab
        self._search_query_real = ""
        self._search_query_vm = ""
        self._search_filtered_real = False
        self._search_filtered_vm = False

        self._drag_item_real = None
        self._drag_item_vm = None

        self._configure_ttk_tree_style()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=0)

        self._build_tabs()
        self._build_log_and_undo_bar()

        # shortcuts
        self.bind_all("<Control-z>", lambda e: self._on_undo())
        self.bind_all("<Delete>", lambda e: self._on_delete())
        self.bind_all("<F2>", lambda e: self._on_rename())

    def set_controller(self, controller: Any):
        self.controller = controller
        self.controller.refresh_vm()

    def _configure_ttk_tree_style(self):
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
        style.map(
            "Treeview",
            background=[("selected", "#1f6aa5")],
            foreground=[("selected", "#ffffff")],
        )
        style.configure(
            "Vertical.TScrollbar",
            troughcolor="#12161d",
            background="#334155",
            bordercolor="#12161d",
            arrowcolor="#e5e7eb",
        )

    # ---------- UI Building ----------
    def _build_tabs(self):
        self.tabs = ctk.CTkTabview(self)
        self.tabs.grid(row=0, column=0, sticky="nsew", padx=14, pady=(14, 8))
        self.tabs.add(T.TAB_REAL)
        self.tabs.add(T.TAB_VM)
        self.tabs.set(T.TAB_REAL)
        self.tabs.configure(command=self._on_tab_changed)

        self.real_tab = self.tabs.tab(T.TAB_REAL)
        self.vm_tab = self.tabs.tab(T.TAB_VM)

        for tab in [self.real_tab, self.vm_tab]:
            tab.grid_columnconfigure(0, weight=1)
            tab.grid_rowconfigure(1, weight=1)

        self._build_real_tab(self.real_tab)
        self._build_vm_tab(self.vm_tab)

    def _build_toolbar(self, parent, is_vm: bool):
        bar = ctk.CTkFrame(parent)
        bar.grid(row=0, column=0, sticky="ew", padx=10, pady=10)
        bar.grid_columnconfigure(2, weight=1)

        self.root_label = ctk.CTkLabel(bar, text="Root: -", anchor="w", font=self._font_ui)
        self.root_label.grid(row=0, column=0, padx=(12, 10), pady=10, sticky="w")

        select_btn = ctk.CTkButton(
            bar, text=T.BTN_SELECT_ROOT, width=160,
            command=self._on_select_root_real
        )
        select_btn.configure(height=34, font=self._font_ui)
        select_btn.grid(row=0, column=1, padx=(0, 10), pady=10)

        search_entry = ctk.CTkEntry(bar, placeholder_text=T.PLACEHOLDER_SEARCH, height=34, font=self._font_ui)
        search_entry.grid(row=0, column=2, padx=(0, 10), pady=10, sticky="ew")
        search_entry.bind("<Return>", lambda e: self._on_search())
        if is_vm:
            self.vm_search_entry = search_entry
        else:
            self.real_search_entry = search_entry

        refresh_btn = ctk.CTkButton(bar, text=T.BTN_REFRESH, width=110, command=self._on_refresh)
        refresh_btn.configure(height=34, font=self._font_ui)
        refresh_btn.grid(row=0, column=3, padx=(0, 10), pady=10)

        chk_hidden = ctk.CTkCheckBox(
            bar, text=T.LBL_SHOW_HIDDEN, variable=self.var_show_hidden, command=self._on_toggle_hidden, font=self._font_ui
        )
        chk_hidden.grid(row=0, column=4, padx=(0, 12), pady=10)

        chk_filter = ctk.CTkCheckBox(
            bar, text=T.LBL_FILTER_MODE, variable=self.var_filter_mode, command=self._on_filter_toggle, font=self._font_ui
        )
        chk_filter.grid(row=0, column=5, padx=(0, 12), pady=10)

        clear_btn = ctk.CTkButton(bar, text=T.BTN_CLEAR_SEARCH, width=90, command=self._on_clear_search)
        clear_btn.configure(height=34, font=self._font_ui)
        clear_btn.grid(row=0, column=6, padx=(0, 12), pady=10)

        return bar

    def _build_split_view(self, parent, is_vm: bool):
        body = ctk.CTkFrame(parent)
        body.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=0)
        body.grid_columnconfigure(2, weight=1)
        body.grid_rowconfigure(0, weight=1)

        # Left: explorer
        left = ctk.CTkFrame(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=0)
        left.grid_rowconfigure(1, weight=1)
        left.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(left, text=T.PANEL_EXPLORER, font=self._font_section).grid(
            row=0, column=0, padx=12, pady=(10, 6), sticky="w"
        )

        tree_frame = ctk.CTkFrame(left, fg_color="transparent")
        tree_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        tree = ttk.Treeview(tree_frame, show="tree")
        yscroll = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview, style="Vertical.TScrollbar")
        tree.configure(yscrollcommand=yscroll.set)
        tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")

        # tag for highlight
        tree.tag_configure("match", background="#334155", foreground="#fde68a")

        # Middle separator
        sep = ttk.Separator(body, orient="vertical")
        sep.grid(row=0, column=1, sticky="ns")

        # Right: details + preview
        right = ctk.CTkFrame(body)
        right.grid(row=0, column=2, sticky="nsew", padx=(8, 0), pady=0)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(right, text=T.PANEL_DETAILS, font=self._font_section).grid(
            row=0, column=0, padx=12, pady=(10, 6), sticky="w"
        )

        details_box = ctk.CTkFrame(right)
        details_box.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        details_box.grid_columnconfigure(1, weight=1)

        detail_rows = {}
        for i, (k, label) in enumerate([
            ("name", T.DETAIL_NAME),
            ("path", T.DETAIL_PATH),
            ("type", T.DETAIL_TYPE),
            ("size", T.DETAIL_SIZE),
            ("modified", T.DETAIL_MODIFIED),
        ]):
            ctk.CTkLabel(details_box, text=f"{label}:", width=90, anchor="w", font=self._font_body).grid(
                row=i, column=0, padx=10, pady=6, sticky="w"
            )
            v = ctk.CTkLabel(details_box, text="-", anchor="w", wraplength=520, justify="left", font=self._font_body)
            v.grid(row=i, column=1, padx=10, pady=6, sticky="w")
            detail_rows[k] = v

        btns = ctk.CTkFrame(details_box, fg_color="transparent")
        btns.grid(row=6, column=0, columnspan=2, padx=10, pady=(8, 4), sticky="ew")
        for c in range(4):
            btns.grid_columnconfigure(c, weight=1)

        b_rename = ctk.CTkButton(btns, text=T.BTN_RENAME, command=self._on_rename)
        b_delete = ctk.CTkButton(btns, text=T.BTN_DELETE, command=self._on_delete)
        b_copy = ctk.CTkButton(btns, text=T.BTN_COPY, command=self._on_copy)
        b_move = ctk.CTkButton(btns, text=T.BTN_MOVE, command=self._on_move)
        for b in (b_rename, b_delete, b_copy, b_move):
            b.configure(height=34, font=self._font_ui)

        b_rename.grid(row=0, column=0, padx=6, pady=6, sticky="ew")
        b_delete.grid(row=0, column=1, padx=6, pady=6, sticky="ew")
        b_copy.grid(row=0, column=2, padx=6, pady=6, sticky="ew")
        b_move.grid(row=0, column=3, padx=6, pady=6, sticky="ew")

        btns2 = ctk.CTkFrame(details_box, fg_color="transparent")
        btns2.grid(row=7, column=0, columnspan=2, padx=10, pady=(0, 10), sticky="ew")
        btns2.grid_columnconfigure(0, weight=1)
        btns2.grid_columnconfigure(1, weight=1)
        b_new_file = ctk.CTkButton(btns2, text=T.BTN_NEW_FILE, command=self._on_new_file)
        b_new_folder = ctk.CTkButton(btns2, text=T.BTN_NEW_FOLDER, command=self._on_new_folder)
        b_new_file.configure(height=34, font=self._font_ui)
        b_new_folder.configure(height=34, font=self._font_ui)
        b_new_file.grid(row=0, column=0, padx=6, pady=6, sticky="ew")
        b_new_folder.grid(row=0, column=1, padx=6, pady=6, sticky="ew")

        # Preview area
        ctk.CTkLabel(right, text=T.PANEL_PREVIEW, font=self._font_section).grid(
            row=2, column=0, padx=12, pady=(0, 6), sticky="nw"
        )
        preview_box = ctk.CTkFrame(right)
        preview_box.grid(row=3, column=0, sticky="nsew", padx=10, pady=(0, 10))
        preview_box.grid_rowconfigure(0, weight=1)
        preview_box.grid_columnconfigure(0, weight=1)

        preview_text = ctk.CTkTextbox(preview_box)
        preview_text.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        preview_text.configure(state="disabled")

        preview_img = ctk.CTkLabel(preview_box, text="")
        preview_img.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        preview_img.grid_remove()  # hidden by default

        if is_vm:
            self.vm_tree = tree
            self._vm_detail_rows = detail_rows
            self.vm_preview_text = preview_text
            self.vm_preview_img = preview_img
            self._vm_preview_image_ref = None
        else:
            self.tree = tree
            self._detail_rows = detail_rows
            self.preview_text = preview_text
            self.preview_img = preview_img
            self._preview_image_ref = None

        return body

    def _build_real_tab(self, parent):
        self._build_toolbar(parent, is_vm=False)
        self._build_split_view(parent, is_vm=False)

        # real tree binds
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<<TreeviewOpen>>", self._on_tree_open)
        self.tree.bind("<Button-3>", self._on_tree_right_click)
        self.tree.bind("<ButtonPress-1>", self._on_drag_start_real)
        self.tree.bind("<ButtonRelease-1>", self._on_drag_end_real)

        self._tree_item_to_payload = {}

    def _build_vm_tab(self, parent):
        self._build_toolbar(parent, is_vm=True)

        vmbar = ctk.CTkFrame(parent)
        vmbar.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        for i in range(4):
            vmbar.grid_columnconfigure(i, weight=1)
        ctk.CTkButton(vmbar, text=T.VM_BTN_SAVE, command=self._on_vm_save).grid(row=0, column=0, padx=6, pady=10, sticky="ew")
        ctk.CTkButton(vmbar, text=T.VM_BTN_LOAD, command=self._on_vm_load).grid(row=0, column=1, padx=6, pady=10, sticky="ew")
        ctk.CTkButton(vmbar, text=T.VM_BTN_CLEAR, command=self._on_vm_clear).grid(row=0, column=2, padx=6, pady=10, sticky="ew")
        ctk.CTkButton(vmbar, text=T.VM_BTN_EXPORT, command=self._on_vm_export).grid(row=0, column=3, padx=6, pady=10, sticky="ew")

        # vm split view under buttons
        vm_body = ctk.CTkFrame(parent)
        vm_body.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 10))
        vm_body.grid_columnconfigure(0, weight=1)
        vm_body.grid_rowconfigure(0, weight=1)
        self._build_split_view(vm_body, is_vm=True)

        # vm binds
        self.vm_tree.bind("<<TreeviewSelect>>", self._on_vm_tree_select)
        self.vm_tree.bind("<Button-3>", self._on_vm_tree_right_click)
        self.vm_tree.bind("<ButtonPress-1>", self._on_drag_start_vm)
        self.vm_tree.bind("<ButtonRelease-1>", self._on_drag_end_vm)

        self._vm_tree_item_to_id = {}

    def _build_log_and_undo_bar(self):
        bottom = ctk.CTkFrame(self)
        bottom.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))
        bottom.grid_rowconfigure(1, weight=1)
        bottom.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(bottom, text=T.PANEL_LOG, font=self._font_section).grid(
            row=0, column=0, padx=12, pady=(10, 6), sticky="w"
        )

        self.log_box = ctk.CTkTextbox(bottom, height=140)
        self.log_box.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 10))
        self.log_box.configure(state="disabled")

        self.undo_bar = ctk.CTkFrame(bottom)
        self.undo_bar.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 10))
        self.undo_bar.grid_columnconfigure(0, weight=1)

        self.undo_label = ctk.CTkLabel(self.undo_bar, text="", anchor="w", font=self._font_body)
        self.undo_label.grid(row=0, column=0, padx=10, pady=8, sticky="w")

        self.undo_btn = ctk.CTkButton(self.undo_bar, text=T.BTN_UNDO, width=120, command=self._on_undo)
        self.undo_btn.grid(row=0, column=1, padx=10, pady=8)
        self.set_undo_status(None)

    # ---------- Public UI API ----------
    def append_log(self, line: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", line + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def set_undo_status(self, msg: Optional[str]):
        if msg:
            self.undo_label.configure(text=msg)
            self.undo_btn.configure(state="normal")
        else:
            self.undo_label.configure(text="")
            self.undo_btn.configure(state="disabled")

    def set_root_label(self, root: str):
        self.root_label.configure(text=f"Root: {root}")

    def clear_details(self):
        target = self._detail_rows if self.tabs.get() == T.TAB_REAL else self._vm_detail_rows
        for k in target:
            target[k].configure(text="-")

    def update_details(self, meta: FileMeta):
        target = self._detail_rows if self.tabs.get() == T.TAB_REAL else self._vm_detail_rows
        target["name"].configure(text=meta.name)
        target["path"].configure(text=meta.path)
        target["type"].configure(text=meta.type)
        target["size"].configure(text=format_bytes(meta.size_bytes))
        target["modified"].configure(text=format_ts(meta.modified_ts))

    def prompt(self, title: str, message: str) -> Optional[str]:
        dlg = SimplePrompt(self, title, message)
        self.wait_window(dlg)
        return dlg.result

    # ---------- Preview API ----------
    def clear_preview(self):
        # text
        self._set_textbox(self.preview_text, "")
        self.preview_img.grid_remove()
        self.preview_text.grid()
        self._preview_image_ref = None

        self._set_textbox(self.vm_preview_text, "")
        self.vm_preview_img.grid_remove()
        self.vm_preview_text.grid()
        self._vm_preview_image_ref = None

    def set_preview_text(self, text: str):
        if self.tabs.get() == T.TAB_REAL:
            self.preview_img.grid_remove()
            self.preview_text.grid()
            self._set_textbox(self.preview_text, text)
        else:
            self.vm_preview_img.grid_remove()
            self.vm_preview_text.grid()
            self._set_textbox(self.vm_preview_text, text)

    def set_preview_image(self, filepath: str):
        if Image is None:
            self.set_preview_text("(Pillow not installed: cannot preview images)")
            return
        try:
            img = Image.open(filepath)
            img.thumbnail((640, 420))
            cimg = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
        except Exception as e:
            self.set_preview_text(f"(image preview error) {e}")
            return

        if self.tabs.get() == T.TAB_REAL:
            self.preview_text.grid_remove()
            self.preview_img.grid()
            self.preview_img.configure(image=cimg)
            self._preview_image_ref = cimg
        else:
            self.vm_preview_text.grid_remove()
            self.vm_preview_img.grid()
            self.vm_preview_img.configure(image=cimg)
            self._vm_preview_image_ref = cimg

    def _set_textbox(self, tb: ctk.CTkTextbox, text: str):
        tb.configure(state="normal")
        tb.delete("1.0", "end")
        tb.insert("1.0", text)
        tb.configure(state="disabled")

    # ---------- Tree builders ----------
    def build_tree_real(self, root: Path, backend):
        # normal mode (lazy)
        self._search_filtered_real = False
        self._search_query_real = ""
        self.tree.delete(*self.tree.get_children())
        self._tree_item_to_payload = {}
        root_item = self.tree.insert("", "end", text=str(root), open=True)
        self._tree_item_to_payload[root_item] = root
        self._insert_real_children_lazy(root_item, root, backend)

    def _insert_real_children_lazy(self, parent_item, folder: Path, backend):
        for child in self.tree.get_children(parent_item):
            self.tree.delete(child)
        for p, is_dir in backend.list_children(folder):
            item = self.tree.insert(parent_item, "end", text=p.name, open=False)
            self._tree_item_to_payload[item] = p
            if is_dir:
                dummy = self.tree.insert(item, "end", text="(loading...)")
                self._tree_item_to_payload[dummy] = None

    def build_tree_vm(self, root_id: str, backend):
        self._search_filtered_vm = False
        self._search_query_vm = ""
        self.vm_tree.delete(*self.vm_tree.get_children())
        self._vm_tree_item_to_id = {}
        root_item = self.vm_tree.insert("", "end", text=backend.nodes[root_id].name, open=True)
        self._vm_tree_item_to_id[root_item] = root_id
        self._insert_vm_children(root_item, root_id, backend)

    def _insert_vm_children(self, parent_item, node_id: str, backend):
        for child in self.vm_tree.get_children(parent_item):
            self.vm_tree.delete(child)
        for cid, is_dir in backend.list_children(node_id):
            item = self.vm_tree.insert(parent_item, "end", text=backend.nodes[cid].name, open=is_dir)
            self._vm_tree_item_to_id[item] = cid
            if is_dir:
                self._insert_vm_children(item, cid, backend)

    # ---------- Search: highlight + filter ----------
    def _apply_highlight(self, tree: ttk.Treeview, query: str):
        query = query.lower().strip()
        def walk(item):
            txt = (tree.item(item, "text") or "")
            # clear tags
            tree.item(item, tags=())
            if query and query in txt.lower():
                tree.item(item, tags=("match",))
            for c in tree.get_children(item):
                walk(c)
        for top in tree.get_children(""):
            walk(top)

    def apply_filter_real(self, query: str):
        if not self.controller or not self.controller.real.root:
            return
        query = query.strip()
        if not query:
            self.controller.refresh_real()
            return

        root = self.controller.real.root
        backend = self.controller.real
        self.tree.delete(*self.tree.get_children())
        self._tree_item_to_payload = {}
        self._search_query_real = query
        self._search_filtered_real = True

        max_nodes = 15000
        visited = 0
        matches = 0

        def dfs(folder: Path):
            nonlocal visited, matches
            if visited > max_nodes:
                return False, []
            visited += 1

            included_children = []
            try:
                children = backend.list_children(folder)
            except Exception:
                children = []

            folder_match = query.lower() in folder.name.lower() or query.lower() in str(folder).lower()
            any_child = False
            for p, is_dir in children:
                if visited > max_nodes:
                    break
                if is_dir:
                    ok, child_nodes = dfs(p)
                    if ok:
                        included_children.append((p, True, child_nodes))
                        any_child = True
                else:
                    visited += 1
                    name_match = query.lower() in p.name.lower()
                    if name_match:
                        matches += 1
                        included_children.append((p, False, []))
                        any_child = True
            return folder_match or any_child, included_children

        ok, children_tree = dfs(root)
        root_item = self.tree.insert("", "end", text=str(root), open=True)
        self._tree_item_to_payload[root_item] = root

        def insert(parent_item, nodes):
            for p, is_dir, kids in nodes:
                item = self.tree.insert(parent_item, "end", text=p.name, open=True)
                self._tree_item_to_payload[item] = p
                if is_dir:
                    insert(item, kids)

        insert(root_item, children_tree)
        self._apply_highlight(self.tree, query)
        self.append_log(f"Filter (Real): '{query}' — matches: {matches} (visited limit: {max_nodes})")

    def apply_filter_vm(self, query: str):
        if not self.controller:
            return
        query = query.strip()
        if not query:
            self.controller.refresh_vm()
            return

        backend = self.controller.vm
        self.vm_tree.delete(*self.vm_tree.get_children())
        self._vm_tree_item_to_id = {}
        self._search_query_vm = query
        self._search_filtered_vm = True

        matches = 0
        max_nodes = 20000
        visited = 0

        def dfs(node_id: str):
            nonlocal matches, visited
            if visited > max_nodes:
                return False, []
            visited += 1
            node = backend.nodes[node_id]
            node_match = query.lower() in node.name.lower()
            included = []
            any_child = False
            for cid, is_dir in backend.list_children(node_id):
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

        ok, kids = dfs(backend.root_id)
        root_item = self.vm_tree.insert("", "end", text=backend.nodes[backend.root_id].name, open=True)
        self._vm_tree_item_to_id[root_item] = backend.root_id

        def insert(parent_item, nodes):
            for cid, is_dir, ckids in nodes:
                item = self.vm_tree.insert(parent_item, "end", text=backend.nodes[cid].name, open=True)
                self._vm_tree_item_to_id[item] = cid
                if is_dir:
                    insert(item, ckids)

        insert(root_item, kids)
        self._apply_highlight(self.vm_tree, query)
        self.append_log(f"Filter (VM): '{query}' — matches: {matches}")

    # ---------- Context menu ----------
    def _make_menu(self, is_vm: bool) -> Menu:
        menu = Menu(self, tearoff=0)
        menu.add_command(label=T.MENU_NEW_FILE, command=self._on_new_file)
        menu.add_command(label=T.MENU_NEW_FOLDER, command=self._on_new_folder)
        menu.add_separator()
        menu.add_command(label=T.MENU_RENAME, command=self._on_rename)
        menu.add_command(label=T.MENU_DELETE, command=self._on_delete)
        menu.add_separator()
        menu.add_command(label=T.MENU_COPY_PATH, command=self._on_copy_path)
        menu.add_command(label=T.MENU_REFRESH, command=self._on_refresh)
        menu.add_separator()
        menu.add_command(label=T.MENU_EXPAND_ALL, command=lambda: self._expand_all(self.vm_tree if is_vm else self.tree))
        menu.add_command(label=T.MENU_COLLAPSE_ALL, command=lambda: self._collapse_all(self.vm_tree if is_vm else self.tree))
        return menu

    def _on_tree_right_click(self, event):
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            self.tree.focus(item)
        if not hasattr(self, "_menu_real"):
            self._menu_real = self._make_menu(is_vm=False)
        self._menu_real.tk_popup(event.x_root, event.y_root)

    def _on_vm_tree_right_click(self, event):
        item = self.vm_tree.identify_row(event.y)
        if item:
            self.vm_tree.selection_set(item)
            self.vm_tree.focus(item)
        if not hasattr(self, "_menu_vm"):
            self._menu_vm = self._make_menu(is_vm=True)
        self._menu_vm.tk_popup(event.x_root, event.y_root)

    def _expand_all(self, tree):
        def rec(item):
            tree.item(item, open=True)
            for c in tree.get_children(item):
                rec(c)
        for top in tree.get_children(""):
            rec(top)

    def _collapse_all(self, tree):
        def rec(item):
            tree.item(item, open=False)
            for c in tree.get_children(item):
                rec(c)
        for top in tree.get_children(""):
            rec(top)

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

    # ---------- Drag & drop ----------
    def _on_drag_start_real(self, event):
        self._drag_item_real = self.tree.identify_row(event.y)

    def _on_drag_end_real(self, event):
        if not self.controller:
            return
        src_item = self._drag_item_real
        self._drag_item_real = None
        if not src_item:
            return
        dst_item = self.tree.identify_row(event.y)
        if not dst_item or dst_item == src_item:
            return
        src_path = self._tree_item_to_payload.get(src_item)
        dst_path = self._tree_item_to_payload.get(dst_item)
        if not src_path or not dst_path:
            return
        # only drop onto folder
        if not Path(dst_path).is_dir():
            return
        # set selection to src (so controller uses it)
        self.tree.selection_set(src_item)
        self.controller.real_on_select_path(src_path)
        self.controller.real_move_to(Path(dst_path))

    def _on_drag_start_vm(self, event):
        self._drag_item_vm = self.vm_tree.identify_row(event.y)

    def _on_drag_end_vm(self, event):
        if not self.controller:
            return
        src_item = self._drag_item_vm
        self._drag_item_vm = None
        if not src_item:
            return
        dst_item = self.vm_tree.identify_row(event.y)
        if not dst_item or dst_item == src_item:
            return
        src_id = self._vm_tree_item_to_id.get(src_item)
        dst_id = self._vm_tree_item_to_id.get(dst_item)
        if not src_id or not dst_id:
            return
        if not self.controller.vm.nodes[dst_id].is_dir:
            return
        self.vm_tree.selection_set(src_item)
        self.controller.vm_on_select_node(src_id)
        self.controller.vm_move_to(dst_id)

    # ---------- Event handlers ----------
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
            q = self.real_search_entry.get().strip() if hasattr(self, "real_search_entry") else ""
            if self.var_filter_mode.get() and q:
                self.apply_filter_real(q)
            else:
                self.controller.refresh_real()
                self._apply_highlight(self.tree, q)
        else:
            q = self.vm_search_entry.get().strip() if hasattr(self, "vm_search_entry") else ""
            if self.var_filter_mode.get() and q:
                self.apply_filter_vm(q)
            else:
                self.controller.refresh_vm()
                self._apply_highlight(self.vm_tree, q)

    def _on_toggle_hidden(self):
        if not self.controller:
            return
        v = bool(self.var_show_hidden.get())
        self.controller.real.show_hidden = v
        self.controller.vm.show_hidden = v
        self._on_refresh()

    def _on_filter_toggle(self):
        # if filter toggled on with existing query, apply immediately; else refresh
        self._on_refresh()

    def _on_clear_search(self):
        if self.tabs.get() == T.TAB_REAL:
            self.real_search_entry.delete(0, "end")
        else:
            self.vm_search_entry.delete(0, "end")
        self._on_refresh()

    def _on_search(self):
        if self.tabs.get() == T.TAB_REAL:
            q = (self.real_search_entry.get() or "").strip()
            if self.var_filter_mode.get():
                self.apply_filter_real(q)
            else:
                self._apply_highlight(self.tree, q)
                self._jump_to_first(self.tree, q)
        else:
            q = (self.vm_search_entry.get() or "").strip()
            if self.var_filter_mode.get():
                self.apply_filter_vm(q)
            else:
                self._apply_highlight(self.vm_tree, q)
                self._jump_to_first(self.vm_tree, q)

    def _jump_to_first(self, tree: ttk.Treeview, q: str):
        q = q.strip().lower()
        if not q:
            return
        def walk(item):
            if q in (tree.item(item, "text") or "").lower():
                return item
            for c in tree.get_children(item):
                r = walk(c)
                if r:
                    return r
            return None
        for top in tree.get_children(""):
            hit = walk(top)
            if hit:
                tree.see(hit)
                tree.selection_set(hit)
                tree.focus(hit)
                break

    def _on_tree_select(self, _evt=None):
        if not self.controller:
            return
        sel = self.tree.selection()
        if not sel:
            return
        p = self._tree_item_to_payload.get(sel[0])
        if p:
            self.controller.real_on_select_path(p)

    def _on_tree_open(self, _evt=None):
        # lazy load real children unless filtered
        if not self.controller or not self.controller.real.root:
            return
        if self._search_filtered_real:
            return
        item = self.tree.focus()
        p = self._tree_item_to_payload.get(item)
        if not p or not isinstance(p, Path):
            return
        if not p.is_dir():
            return
        children = self.tree.get_children(item)
        if len(children) == 1 and self.tree.item(children[0], "text") == "(loading...)":
            self._insert_real_children_lazy(item, p, self.controller.real)

    def _on_vm_tree_select(self, _evt=None):
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
        if self.tabs.get() == T.TAB_REAL:
            self.controller.real_new_folder()
        else:
            self.controller.vm_new_folder()

    def _on_new_file(self):
        if not self.controller:
            return
        if self.tabs.get() == T.TAB_REAL:
            self.controller.real_new_file()
        else:
            self.controller.vm_new_file()

    def _on_rename(self):
        if not self.controller:
            return
        if self.tabs.get() == T.TAB_REAL:
            self.controller.real_rename()
        else:
            self.controller.vm_rename()

    def _on_delete(self):
        if not self.controller:
            return
        if self.tabs.get() == T.TAB_REAL:
            self.controller.real_delete()
        else:
            self.controller.vm_delete()

    def _on_copy(self):
        if not self.controller:
            return
        if self.tabs.get() == T.TAB_REAL:
            self.controller.real_copy_dialog()
        else:
            # VM copy asks for destination path
            dst = self.prompt("Copy VM", "Destination folder path (VM:/...):")
            if not dst:
                return
            dst_id = self.vm_path_to_id(dst.strip(), self.controller.vm)
            if not dst_id:
                return
            self.controller.vm_copy_to(dst_id)

    def _on_move(self):
        if not self.controller:
            return
        if self.tabs.get() == T.TAB_REAL:
            self.controller.real_move_dialog()
        else:
            dst = self.prompt("Move VM", "Destination folder path (VM:/...):")
            if not dst:
                return
            dst_id = self.vm_path_to_id(dst.strip(), self.controller.vm)
            if not dst_id:
                return
            self.controller.vm_move_to(dst_id)

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

    # ---------- VM path helper ----------
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
