from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SearchOptions:
    extensions: tuple[str, ...] = ()
    kind: str = "all"
    minimum_size: int | None = None
    maximum_size: int | None = None
    modified_after: int | None = None
    modified_before: int | None = None
    created_after: int | None = None
    created_before: int | None = None
    scope: str = "recursive"
    name_mode: str = "literal"
    case_sensitive: bool = False

    def __post_init__(self):
        if (
            self.name_mode not in {"literal", "wildcard", "regex"}
            or type(self.case_sensitive) is not bool
        ):
            raise ValueError("Invalid matching mode or case option")
        if self.kind not in {"all", "files", "folders"} or self.scope not in {
            "current",
            "recursive",
            "filesystem",
        }:
            raise ValueError("Invalid search type or scope")
        extensions = tuple(
            sorted(
                {
                    value.strip().casefold().lstrip(".")
                    for value in self.extensions
                    if value.strip()
                }
            )
        )
        if any(
            not value or len(value) > 128 or any(char in value for char in "/\\*?;")
            for value in extensions
        ):
            raise ValueError("Enter extensions separated by semicolons, such as txt;py")
        object.__setattr__(self, "extensions", extensions)
        for lower, upper in (
            ("minimum_size", "maximum_size"),
            ("modified_after", "modified_before"),
            ("created_after", "created_before"),
        ):
            first, last = getattr(self, lower), getattr(self, upper)
            if any(
                value is not None and (type(value) is not int or value < 0)
                for value in (first, last)
            ):
                raise ValueError("Search ranges must be non-negative integers")
            if first is not None and last is not None and first > last:
                raise ValueError("A search range starts after its end")

    @property
    def active(self):
        return self != SearchOptions()

    def matches(self, name, directory, details):
        if (self.kind == "files" and directory) or (
            self.kind == "folders" and not directory
        ):
            return False
        if self.extensions and (
            directory
            or not any(
                name.casefold().endswith("." + extension)
                for extension in self.extensions
            )
        ):
            return False
        if (self.minimum_size is not None or self.maximum_size is not None) and (
            directory
            or not in_range(details.st_size, self.minimum_size, self.maximum_size)
        ):
            return False
        if not in_range(details.st_mtime_ns, self.modified_after, self.modified_before):
            return False
        created = getattr(details, "st_birthtime_ns", None)
        if created is None and hasattr(details, "st_birthtime"):
            created = int(details.st_birthtime * 1_000_000_000)
        return in_range(created, self.created_after, self.created_before)


def in_range(value, lower, upper):
    if lower is None and upper is None:
        return True
    return (
        value is not None
        and (lower is None or value >= lower)
        and (upper is None or value <= upper)
    )
