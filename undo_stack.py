from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Optional, List

@dataclass
class UndoAction:
    label: str
    undo: Callable[[], Optional[str]]

class UndoStack:
    def __init__(self, maxlen: int = 50):
        self.maxlen = maxlen
        self._stack: List[UndoAction] = []

    def push(self, action: UndoAction) -> None:
        self._stack.append(action)
        if len(self._stack) > self.maxlen:
            self._stack.pop(0)

    def pop(self) -> Optional[UndoAction]:
        return self._stack.pop() if self._stack else None

    def peek(self) -> Optional[UndoAction]:
        return self._stack[-1] if self._stack else None

    def clear(self) -> None:
        self._stack.clear()

    def __len__(self) -> int:
        return len(self._stack)
