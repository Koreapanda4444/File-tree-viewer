from __future__ import annotations
import os
import time
from pathlib import Path

def now_ts() -> float:
    return time.time()

def format_ts(ts: float | None) -> str:
    if not ts:
        return "-"
    return time.strftime("%Y-%m-%d %I:%M %p", time.localtime(ts))

def format_bytes(n: int | None) -> str:
    if n is None:
        return "-"
    size = float(n)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024.0 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} {unit}"
        size /= 1024.0
    return f"{n} B"

def is_hidden_path(p: Path) -> bool:
    name = p.name
    if name.startswith("."):
        return True
    # Windows hidden attribute check is skipped (cross-platform draft).
    return False

def ensure_under_root(root: Path, target: Path) -> None:
    root = root.resolve()
    target = target.resolve()
    try:
        target.relative_to(root)
    except Exception:
        raise ValueError("Target path is outside of selected root folder.")

def safe_join(parent: Path, name: str) -> Path:
    # Disallow path traversal
    if any(sep in name for sep in ("/", "\\")) or name in (".", ".."):
        raise ValueError("Invalid name.")
    return parent / name

def human_type_from_name(name: str, is_dir: bool) -> str:
    if is_dir:
        return "Folder"
    ext = Path(name).suffix.lower()
    if ext:
        return f"{ext[1:].upper()} File"
    return "File"
