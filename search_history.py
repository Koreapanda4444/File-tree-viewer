from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from search_options import SearchOptions


@dataclass(frozen=True, slots=True)
class SearchRequest:
    query: str
    options: SearchOptions
    root: str | None = None

    def as_dict(self):
        return {"query": self.query, "options": asdict(self.options), "root": self.root}

    @classmethod
    def from_dict(cls, data):
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("query"), str)
            or len(data["query"]) > 512
        ):
            raise ValueError("Invalid saved query")
        options = data.get("options")
        if (
            not isinstance(options, dict)
            or not isinstance(options.get("extensions", []), list)
            or not all(
                isinstance(value, str) for value in options.get("extensions", [])
            )
        ):
            raise ValueError("Invalid saved filters")
        options = dict(options)
        options["extensions"] = tuple(options.get("extensions", []))
        root = data.get("root")
        if root is not None and (
            not isinstance(root, str)
            or len(root) > 4096
            or "\0" in root
            or not Path(root).is_absolute()
        ):
            raise ValueError("Invalid saved root")
        return cls(data["query"], SearchOptions(**options), root)


class SearchHistory:
    def __init__(self, settings):
        self.settings = settings
        self.recent = []
        self.presets = {}
        try:
            raw = settings.value("search/saved", "{}")
            if not isinstance(raw, str) or len(raw) > 1024 * 1024:
                return
            data = json.loads(raw)
            if not isinstance(data, dict) or data.get("version") != 1:
                return
            for item in data.get("recent", [])[:20]:
                self.recent.append(SearchRequest.from_dict(item))
            for name, item in list(data.get("presets", {}).items())[:32]:
                self.validate_name(name)
                self.presets[name] = SearchRequest.from_dict(item)
        except (ValueError, TypeError, AttributeError):
            self.recent = []
            self.presets = {}

    @staticmethod
    def validate_name(name):
        if (
            not isinstance(name, str)
            or not name.strip()
            or len(name) > 128
            or any(ord(char) < 32 for char in name)
        ):
            raise ValueError("Enter a preset name of at most 128 characters")

    def save(self):
        self.settings.setValue(
            "search/saved",
            json.dumps(
                {
                    "version": 1,
                    "recent": [item.as_dict() for item in self.recent],
                    "presets": {
                        key: value.as_dict() for key, value in self.presets.items()
                    },
                }
            ),
        )
        self.settings.sync()
        return self.settings.status() == self.settings.Status.NoError

    def record(self, request):
        self.recent = [request, *(item for item in self.recent if item != request)][:20]
        return self.save()

    def set_preset(self, name, request):
        self.validate_name(name)
        if name not in self.presets and len(self.presets) >= 32:
            raise ValueError("At most 32 search presets can be saved")
        self.presets[name] = SearchRequest(request.query, request.options)
        return self.save()
