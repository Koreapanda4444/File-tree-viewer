from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class NavigationHistory:
    paths: list[Path] = field(default_factory=list)
    position: int = -1
    limit: int = 128

    def visit(self, path: Path):
        if self.position >= 0 and self.paths[self.position] == path:
            return
        del self.paths[self.position + 1 :]
        self.paths.append(path)
        if len(self.paths) > self.limit:
            del self.paths[: len(self.paths) - self.limit]
        self.position = len(self.paths) - 1

    def candidate(self, direction: int):
        index = self.position + direction
        return (index, self.paths[index]) if 0 <= index < len(self.paths) else None

    def discard(self, index: int):
        if index == self.position:
            raise ValueError("Cannot discard the current location")
        del self.paths[index]
        if index < self.position:
            self.position -= 1
