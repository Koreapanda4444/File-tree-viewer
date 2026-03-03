from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Literal

NodeType = Literal["file", "dir"]

@dataclass
class FileMeta:
    name: str
    path: str
    type: str
    size_bytes: Optional[int] = None
    modified_ts: Optional[float] = None

@dataclass
class UndoDelete:
    src_path: str
    trash_path: str
    is_dir: bool
