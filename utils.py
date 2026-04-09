from __future__ import annotations

import os
import time
import subprocess
import sys
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
    return p.name.startswith(".")


def ensure_under_root(root: Path, target: Path) -> None:
    root = root.resolve()
    target = target.resolve()
    try:
        target.relative_to(root)
    except Exception as exc:
        raise ValueError("Target path is outside of selected root folder.") from exc


def safe_join(parent: Path, name: str) -> Path:
    if any(sep in name for sep in ("/", "\\")) or name in (".", "..") or not name.strip():
        raise ValueError("Invalid name.")
    return parent / name


def human_type_from_name(name: str, is_dir: bool) -> str:
    if is_dir:
        return "Folder"
    ext = Path(name).suffix.lower()
    if ext:
        return f"{ext[1:].upper()} File"
    return "File"


def looks_like_text(ext: str) -> bool:
    ext = ext.lower()
    return ext in {
        ".txt",
        ".md",
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".json",
        ".yml",
        ".yaml",
        ".ini",
        ".cfg",
        ".log",
        ".csv",
        ".html",
        ".css",
        ".c",
        ".cpp",
        ".h",
        ".hpp",
        ".java",
        ".rs",
        ".go",
        ".php",
        ".sh",
        ".bat",
        ".ps1",
        ".toml",
        ".xml",
    }


def is_image_ext(ext: str) -> bool:
    ext = ext.lower()
    return ext in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


def read_text_head(path: Path, max_bytes: int = 50_000) -> str:
    data = path.read_bytes()[:max_bytes]
    return data.decode("utf-8", errors="replace")


def read_text_full(path: Path, max_bytes: int = 5_000_000) -> str:
    data = path.read_bytes()
    if len(data) > max_bytes:
        data = data[:max_bytes]
    return data.decode("utf-8", errors="replace")


def write_text_full(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def open_path_default(path: Path) -> None:
    p = str(path)
    if sys.platform.startswith("win"):
        os.startfile(p)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.run(["open", p], check=False)
    else:
        subprocess.run(["xdg-open", p], check=False)


def reveal_in_explorer(path: Path) -> None:
    p = str(path)
    if sys.platform.startswith("win"):
        subprocess.run(["explorer", "/select,", p], check=False)
    elif sys.platform == "darwin":
        subprocess.run(["open", "-R", p], check=False)
    else:
        subprocess.run(["xdg-open", str(path.parent)], check=False)
