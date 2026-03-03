from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Optional, Any
from pathlib import Path

import customtkinter as ctk

import ui_text as T
from utils import format_ts, format_bytes
from models import FileMeta

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

        # Center on screen
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

        # Top-level layout
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=0)

        self._configure_ttk_tree_style()

        self._build_tabs()
        self._build_log_and_warning()

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

    def set_controller(self, controller: Any):
        self.controller = controller
        # initial VM render
        self.controller.refresh_vm()

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

        self.root_label = ctk.CTkLabel(bar, text="Select Root Folder: -", anchor="w", font=self._font_ui)
        self.root_label.grid(row=0, column=0, padx=(12, 10), pady=10, sticky="w")

        select_btn = ctk.CTkButton(
            bar, text=T.BTN_SELECT_ROOT, width=140,
            command=(self._on_select_root_real if not is_vm else self._on_select_root_real)  # VM export uses real root
        )
        select_btn.configure(height=34, font=self._font_ui)
        select_btn.grid(row=0, column=1, padx=(0, 10), pady=10)

        self.search_entry = ctk.CTkEntry(bar, placeholder_text=T.PLACEHOLDER_SEARCH, height=34, font=self._font_ui)
        self.search_entry.grid(row=0, column=2, padx=(0, 10), pady=10, sticky="ew")
        self.search_entry.bind("<Return>", lambda e: self._on_search())

        refresh_btn = ctk.CTkButton(
            bar, text=T.BTN_REFRESH, width=110,
            command=(self._on_refresh)
        )
        refresh_btn.configure(height=34, font=self._font_ui)
        refresh_btn.grid(row=0, column=3, padx=(0, 10), pady=10)

        # simple view options: hidden toggle
        self.var_show_hidden = tk.BooleanVar(value=False)
        chk = ctk.CTkCheckBox(bar, text="Show hidden", variable=self.var_show_hidden, command=self._on_toggle_hidden, font=self._font_ui)
        chk.grid(row=0, column=4, padx=(0, 12), pady=10)

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

        # ttk Treeview inside
        self.tree = ttk.Treeview(tree_frame, show="tree")
        yscroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=yscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")

        # Middle separator
        sep = ttk.Separator(body, orient="vertical")
        sep.grid(row=0, column=1, sticky="ns")

        # Right: details panel
        right = ctk.CTkFrame(body)
        right.grid(row=0, column=2, sticky="nsew", padx=(8, 0), pady=0)
        right.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(right, text=T.PANEL_DETAILS, font=self._font_section).grid(
            row=0, column=0, padx=12, pady=(10, 6), sticky="w"
        )

        self.details_box = ctk.CTkFrame(right)
        self.details_box.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        self.details_box.grid_columnconfigure(1, weight=1)

        self._detail_rows = {}
        for i, (k, label) in enumerate([
            ("name", T.DETAIL_NAME),
            ("path", T.DETAIL_PATH),
            ("type", T.DETAIL_TYPE),
            ("size", T.DETAIL_SIZE),
            ("modified", T.DETAIL_MODIFIED),
        ]):
            ctk.CTkLabel(self.details_box, text=f"{label}:", width=100, anchor="w", font=self._font_body).grid(row=i, column=0, padx=10, pady=6, sticky="w")
            v = ctk.CTkLabel(self.details_box, text="-", anchor="w", wraplength=480, justify="left", font=self._font_body)
            v.grid(row=i, column=1, padx=10, pady=6, sticky="w")
            self._detail_rows[k] = v

        # Action buttons
        btns = ctk.CTkFrame(self.details_box, fg_color="transparent")
        btns.grid(row=6, column=0, columnspan=2, padx=10, pady=(12, 6), sticky="ew")
        for c in range(4):
            btns.grid_columnconfigure(c, weight=1)

        self.btn_rename = ctk.CTkButton(btns, text=T.BTN_RENAME, command=self._on_rename)
        self.btn_delete = ctk.CTkButton(btns, text=T.BTN_DELETE, command=self._on_delete)
        self.btn_copy = ctk.CTkButton(btns, text=T.BTN_COPY, command=self._on_copy)
        self.btn_move = ctk.CTkButton(btns, text=T.BTN_MOVE, command=self._on_move)
        for button in (self.btn_rename, self.btn_delete, self.btn_copy, self.btn_move):
            button.configure(height=34, font=self._font_ui)

        self.btn_rename.grid(row=0, column=0, padx=6, pady=6, sticky="ew")
        self.btn_delete.grid(row=0, column=1, padx=6, pady=6, sticky="ew")
        self.btn_copy.grid(row=0, column=2, padx=6, pady=6, sticky="ew")
        self.btn_move.grid(row=0, column=3, padx=6, pady=6, sticky="ew")

        btns2 = ctk.CTkFrame(self.details_box, fg_color="transparent")
        btns2.grid(row=7, column=0, columnspan=2, padx=10, pady=(0, 8), sticky="ew")
        btns2.grid_columnconfigure(0, weight=1)
        btns2.grid_columnconfigure(1, weight=1)

        self.btn_new_file = ctk.CTkButton(btns2, text=T.BTN_NEW_FILE, command=self._on_new_file)
        self.btn_new_folder = ctk.CTkButton(btns2, text=T.BTN_NEW_FOLDER, command=self._on_new_folder)
        self.btn_new_file.configure(height=34, font=self._font_ui)
        self.btn_new_folder.configure(height=34, font=self._font_ui)
        self.btn_new_file.grid(row=0, column=0, padx=6, pady=6, sticky="ew")
        self.btn_new_folder.grid(row=0, column=1, padx=6, pady=6, sticky="ew")

        # Bind tree selection
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<<TreeviewOpen>>", self._on_tree_open)

        # Storage for mapping tree item -> real path or vm node_id
        self._tree_item_to_payload = {}
        self._tree_payload_kind = "real" if not is_vm else "vm"

        return body

    def _build_real_tab(self, parent):
        self._build_toolbar(parent, is_vm=False)
        self._build_split_view(parent, is_vm=False)

    def _build_vm_tab(self, parent):
        self._build_toolbar(parent, is_vm=True)
        parent.grid_rowconfigure(2, weight=1)

        # VM extra buttons row
        vmbar = ctk.CTkFrame(parent)
        vmbar.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        for i in range(4):
            vmbar.grid_columnconfigure(i, weight=1)
        vm_save = ctk.CTkButton(vmbar, text=T.VM_BTN_SAVE, command=self._on_vm_save, height=34, font=self._font_ui)
        vm_load = ctk.CTkButton(vmbar, text=T.VM_BTN_LOAD, command=self._on_vm_load, height=34, font=self._font_ui)
        vm_clear = ctk.CTkButton(vmbar, text=T.VM_BTN_CLEAR, command=self._on_vm_clear, height=34, font=self._font_ui)
        vm_export = ctk.CTkButton(vmbar, text=T.VM_BTN_EXPORT, command=self._on_vm_export, height=34, font=self._font_ui)
        vm_save.grid(row=0, column=0, padx=6, pady=10, sticky="ew")
        vm_load.grid(row=0, column=1, padx=6, pady=10, sticky="ew")
        vm_clear.grid(row=0, column=2, padx=6, pady=10, sticky="ew")
        vm_export.grid(row=0, column=3, padx=6, pady=10, sticky="ew")

        # main split view under vmbar
        vm_body = ctk.CTkFrame(parent)
        vm_body.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 10))
        vm_body.grid_columnconfigure(0, weight=1)
        vm_body.grid_rowconfigure(0, weight=1)

        # reuse split view builder but attach to vm_body (single cell)
        vm_body.grid_columnconfigure(0, weight=1)
        vm_body.grid_rowconfigure(0, weight=1)

        # build the split view inside vm_body
        # to avoid duplicating widgets, we create a separate tree for VM
        self.vm_container = ctk.CTkFrame(vm_body)
        self.vm_container.grid(row=0, column=0, sticky="nsew")
        self.vm_container.grid_columnconfigure(0, weight=1)
        self.vm_container.grid_rowconfigure(0, weight=1)

        # Create a second split view and store VM-specific tree refs
        self._build_vm_split(self.vm_container)

    def _build_vm_split(self, parent):
        body = ctk.CTkFrame(parent)
        body.grid(row=0, column=0, sticky="nsew", padx=0, pady=0)
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=0)
        body.grid_columnconfigure(2, weight=1)
        body.grid_rowconfigure(0, weight=1)

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

        self.vm_tree = ttk.Treeview(tree_frame, show="tree")
        yscroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.vm_tree.yview)
        self.vm_tree.configure(yscrollcommand=yscroll.set)
        self.vm_tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")

        sep = ttk.Separator(body, orient="vertical")
        sep.grid(row=0, column=1, sticky="ns")

        right = ctk.CTkFrame(body)
        right.grid(row=0, column=2, sticky="nsew", padx=(8, 0), pady=0)
        right.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(right, text=T.PANEL_DETAILS, font=self._font_section).grid(
            row=0, column=0, padx=12, pady=(10, 6), sticky="w"
        )

        self.vm_details_box = ctk.CTkFrame(right)
        self.vm_details_box.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        self.vm_details_box.grid_columnconfigure(1, weight=1)

        self._vm_detail_rows = {}
        for i, (k, label) in enumerate([
            ("name", T.DETAIL_NAME),
            ("path", T.DETAIL_PATH),
            ("type", T.DETAIL_TYPE),
            ("size", T.DETAIL_SIZE),
            ("modified", T.DETAIL_MODIFIED),
        ]):
            ctk.CTkLabel(self.vm_details_box, text=f"{label}:", width=100, anchor="w", font=self._font_body).grid(row=i, column=0, padx=10, pady=6, sticky="w")
            v = ctk.CTkLabel(self.vm_details_box, text="-", anchor="w", wraplength=480, justify="left", font=self._font_body)
            v.grid(row=i, column=1, padx=10, pady=6, sticky="w")
            self._vm_detail_rows[k] = v

        btns = ctk.CTkFrame(self.vm_details_box, fg_color="transparent")
        btns.grid(row=6, column=0, columnspan=2, padx=10, pady=(12, 6), sticky="ew")
        for c in range(4):
            btns.grid_columnconfigure(c, weight=1)

        self.vm_btn_rename = ctk.CTkButton(btns, text=T.BTN_RENAME, command=self._on_rename)
        self.vm_btn_delete = ctk.CTkButton(btns, text=T.BTN_DELETE, command=self._on_delete)
        self.vm_btn_copy = ctk.CTkButton(btns, text=T.BTN_COPY, command=self._on_copy)
        self.vm_btn_move = ctk.CTkButton(btns, text=T.BTN_MOVE, command=self._on_move)
        for button in (self.vm_btn_rename, self.vm_btn_delete, self.vm_btn_copy, self.vm_btn_move):
            button.configure(height=34, font=self._font_ui)

        self.vm_btn_rename.grid(row=0, column=0, padx=6, pady=6, sticky="ew")
        self.vm_btn_delete.grid(row=0, column=1, padx=6, pady=6, sticky="ew")
        self.vm_btn_copy.grid(row=0, column=2, padx=6, pady=6, sticky="ew")
        self.vm_btn_move.grid(row=0, column=3, padx=6, pady=6, sticky="ew")

        btns2 = ctk.CTkFrame(self.vm_details_box, fg_color="transparent")
        btns2.grid(row=7, column=0, columnspan=2, padx=10, pady=(0, 8), sticky="ew")
        btns2.grid_columnconfigure(0, weight=1)
        btns2.grid_columnconfigure(1, weight=1)
        self.vm_btn_new_file = ctk.CTkButton(btns2, text=T.BTN_NEW_FILE, command=self._on_new_file)
        self.vm_btn_new_folder = ctk.CTkButton(btns2, text=T.BTN_NEW_FOLDER, command=self._on_new_folder)
        self.vm_btn_new_file.configure(height=34, font=self._font_ui)
        self.vm_btn_new_folder.configure(height=34, font=self._font_ui)
        self.vm_btn_new_file.grid(row=0, column=0, padx=6, pady=6, sticky="ew")
        self.vm_btn_new_folder.grid(row=0, column=1, padx=6, pady=6, sticky="ew")

        self.vm_tree.bind("<<TreeviewSelect>>", self._on_vm_tree_select)

        self._vm_tree_item_to_id = {}

    def _build_log_and_warning(self):
        # Bottom: log + warning bar
        bottom = ctk.CTkFrame(self)
        bottom.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))
        bottom.grid_rowconfigure(1, weight=0)
        bottom.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(bottom, text=T.PANEL_LOG, font=self._font_section).grid(
            row=0, column=0, padx=12, pady=(10, 6), sticky="w"
        )

        self.log_box = ctk.CTkTextbox(bottom, height=64, font=self._font_body)
        self.log_box.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 10))
        self.log_box.configure(state="disabled")

        self.warning_bar = ctk.CTkFrame(bottom)
        self.warning_bar.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 10))
        self.warning_bar.grid_columnconfigure(1, weight=1)

        self.warning_label = ctk.CTkLabel(self.warning_bar, text="", text_color="#f87171", anchor="w", font=self._font_body)
        self.warning_label.grid(row=0, column=0, padx=10, pady=8, sticky="w")

        self.undo_btn = ctk.CTkButton(self.warning_bar, text=T.BTN_UNDO, width=90, height=32, font=self._font_ui, command=self._on_undo)
        self.undo_btn.grid(row=0, column=2, padx=10, pady=8)
        self.set_warning(None)

    # ---------- Public UI API (controller calls) ----------
    def append_log(self, line: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", line + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def set_warning(self, deleted_path: Optional[str]):
        if deleted_path:
            self.warning_label.configure(text=f"{T.WARN_DELETED} {deleted_path}")
            self.undo_btn.configure(state="normal")
        else:
            self.warning_label.configure(text="")
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

    # ---------- Tree builders ----------
    def build_tree_real(self, root: Path, backend):
        self.tree.delete(*self.tree.get_children())
        self._tree_item_to_payload = {}
        self._tree_payload_kind = "real"
        root_item = self.tree.insert("", "end", text=str(root), open=True)
        self._tree_item_to_payload[root_item] = root

        # Insert dummy to enable lazy load
        self._insert_real_children_lazy(root_item, root, backend)

    def _insert_real_children_lazy(self, parent_item, folder: Path, backend):
        # clear existing children
        for child in self.tree.get_children(parent_item):
            self.tree.delete(child)
        for p, is_dir in backend.list_children(folder):
            item = self.tree.insert(parent_item, "end", text=p.name, open=False)
            self._tree_item_to_payload[item] = p
            if is_dir:
                # dummy child for lazy expansion
                dummy = self.tree.insert(item, "end", text="(loading...)")
                self._tree_item_to_payload[dummy] = None

    def build_tree_vm(self, root_id: str, backend):
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
                # one-level eager for now
                self._insert_vm_children(item, cid, backend)

    def vm_path_to_id(self, vm_path: str, backend) -> Optional[str]:
        # expects VM:/a/b
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
            self.controller.refresh_real()
        else:
            self.controller.refresh_vm()

    def _on_toggle_hidden(self):
        if not self.controller:
            return
        v = bool(self.var_show_hidden.get())
        self.controller.real.show_hidden = v
        self.controller.vm.show_hidden = v
        self._on_refresh()

    def _on_search(self):
        # Draft: select first matching node in current tree
        q = (self.search_entry.get() or "").strip().lower()
        if not q:
            return
        if self.tabs.get() == T.TAB_REAL:
            tree = self.tree
        else:
            tree = self.vm_tree
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
        item = sel[0]
        payload = self._tree_item_to_payload.get(item)
        if payload and isinstance(payload, Path):
            self.controller.real_on_select_path(payload)

    def _on_tree_open(self, _evt=None):
        if not self.controller or not self.controller.real.root:
            return
        item = self.tree.focus()
        p = self._tree_item_to_payload.get(item)
        if not p or not isinstance(p, Path):
            return
        if not p.is_dir():
            return
        # If first child is dummy, load real children
        children = self.tree.get_children(item)
        if len(children) == 1 and self.tree.item(children[0], "text") == "(loading...)":
            self._insert_real_children_lazy(item, p, self.controller.real)

    def _on_vm_tree_select(self, _evt=None):
        if not self.controller:
            return
        sel = self.vm_tree.selection()
        if not sel:
            return
        item = sel[0]
        nid = self._vm_tree_item_to_id.get(item)
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
            self.controller.real_copy()
        else:
            self.controller.vm_copy()

    def _on_move(self):
        if not self.controller:
            return
        if self.tabs.get() == T.TAB_REAL:
            self.controller.real_move()
        else:
            self.controller.vm_move()

    def _on_undo(self):
        if self.controller and self.tabs.get() == T.TAB_REAL:
            self.controller.real_undo()

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
