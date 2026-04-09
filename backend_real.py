from __future__ import annotations

import shutil
from pathlib import Path
from typing import List, Optional, Tuple

from models import FileMeta, UndoDelete
from utils import ensure_under_root, safe_join, is_hidden_path, human_type_from_name

TRASH_DIRNAME = ".ftv_trash"


class RealFSBackend:
    def __init__(self):
        self.root: Optional[Path] = None
        self.show_hidden: bool = False

    def set_root(self, root: Path) -> None:
        self.root = root.resolve()

    def _require_root(self) -> Path:
        if not self.root:
            raise ValueError("Root folder not selected.")
        return self.root

    def list_children(self, folder: Path) -> List[Tuple[Path, bool]]:
        root = self._require_root()
        ensure_under_root(root, folder)
        items: List[Tuple[Path, bool]] = []
        try:
            for entry in folder.iterdir():
                if not self.show_hidden and is_hidden_path(entry):
                    continue
                if not self.show_hidden and entry.name == TRASH_DIRNAME:
                    continue
                items.append((entry, entry.is_dir()))
        except PermissionError:
            return []
        items.sort(key=lambda x: (not x[1], x[0].name.lower()))
        return items

    def get_meta(self, p: Path) -> FileMeta:
        root = self._require_root()
        ensure_under_root(root, p)
        is_dir = p.is_dir()
        size = None
        if not is_dir:
            try:
                size = p.stat().st_size
            except Exception:
                size = None
        try:
            mtime = p.stat().st_mtime
        except Exception:
            mtime = None
        return FileMeta(
            name=p.name,
            path=str(p),
            type=human_type_from_name(p.name, is_dir),
            size_bytes=size,
            modified_ts=mtime,
        )

    def make_folder(self, parent: Path, name: str) -> Path:
        root = self._require_root()
        ensure_under_root(root, parent)
        newp = safe_join(parent, name)
        ensure_under_root(root, newp)
        newp.mkdir(parents=False, exist_ok=False)
        return newp

    def make_file(self, parent: Path, name: str, content: str = "") -> Path:
        root = self._require_root()
        ensure_under_root(root, parent)
        newp = safe_join(parent, name)
        ensure_under_root(root, newp)
        newp.write_text(content, encoding="utf-8")
        return newp

    def rename(self, p: Path, new_name: str) -> Path:
        root = self._require_root()
        ensure_under_root(root, p)
        newp = safe_join(p.parent, new_name)
        ensure_under_root(root, newp)
        p.rename(newp)
        return newp

    def _trash_dir(self) -> Path:
        root = self._require_root()
        trash = root / TRASH_DIRNAME
        trash.mkdir(exist_ok=True)
        return trash

    def delete_to_trash(self, p: Path) -> UndoDelete:
        root = self._require_root()
        ensure_under_root(root, p)
        trash = self._trash_dir()

        base = p.name
        candidate = trash / base
        i = 1
        while candidate.exists():
            candidate = trash / f"{base}__{i}"
            i += 1

        is_dir = p.is_dir()
        shutil.move(str(p), str(candidate))
        return UndoDelete(src_path=str(p), trash_path=str(candidate), is_dir=is_dir)

    def restore_from_trash(self, undo: UndoDelete) -> Path:
        root = self._require_root()
        src = Path(undo.src_path)
        trash = Path(undo.trash_path)

        restore_parent = src.parent if src.parent.exists() else root
        restore_parent.mkdir(parents=True, exist_ok=True)

        target = restore_parent / src.name
        i = 1
        while target.exists():
            target = restore_parent / f"{src.stem}__restored_{i}{src.suffix}"
            i += 1

        shutil.move(str(trash), str(target))
        return target

    def move(self, src: Path, dst_folder: Path) -> Path:
        root = self._require_root()
        ensure_under_root(root, src)
        ensure_under_root(root, dst_folder)
        target = dst_folder / src.name
        ensure_under_root(root, target)
        shutil.move(str(src), str(target))
        return target

    def copy(self, src: Path, dst_folder: Path) -> Path:
        root = self._require_root()
        ensure_under_root(root, src)
        ensure_under_root(root, dst_folder)
        target = dst_folder / src.name
        ensure_under_root(root, target)

        if src.is_dir():
            i = 1
            candidate = target
            while candidate.exists():
                candidate = dst_folder / f"{src.name}__copy_{i}"
                i += 1
            shutil.copytree(str(src), str(candidate))
            return candidate

        i = 1
        candidate = target
        while candidate.exists():
            candidate = dst_folder / f"{src.stem}__copy_{i}{src.suffix}"
            i += 1
        shutil.copy2(str(src), str(candidate))
        return candidate

    def delete_permanently(self, p: Path) -> None:
        root = self._require_root()
        ensure_under_root(root, p)
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=False)
        else:
            p.unlink(missing_ok=True)
