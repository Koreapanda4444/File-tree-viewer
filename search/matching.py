from __future__ import annotations

import fnmatch
import re

import regex


class SearchPatternError(ValueError):
    pass


class NameMatcher:
    def __init__(self, query: str, mode="literal", case_sensitive=False):
        if len(query) > 512:
            raise SearchPatternError("Search expressions are limited to 512 characters")
        self.full = mode == "wildcard"
        if mode == "literal":
            expression = regex.escape(query)
        elif mode == "wildcard":
            expression = fnmatch.translate(query)
        elif mode == "regex":
            if any(
                int(value) > 1000 for value in re.findall(r"\{(\d+)(?:,\d*)?\}", query)
            ) or any(
                int(value) > 1000 for value in re.findall(r"\{\d*,(\d+)\}", query)
            ):
                raise SearchPatternError("Regex repetition bounds cannot exceed 1000")
            expression = query
        else:
            raise SearchPatternError("Unknown name matching mode")
        try:
            self.pattern = regex.compile(
                expression, regex.VERSION1 | (0 if case_sensitive else regex.IGNORECASE)
            )
        except (regex.error, OverflowError, RuntimeError) as error:
            raise SearchPatternError(f"Invalid search expression: {error}") from error

    def find(self, text):
        try:
            method = self.pattern.fullmatch if self.full else self.pattern.search
            match = method(text, timeout=0.02, concurrent=True)
        except TimeoutError as error:
            raise SearchPatternError(
                "Search expression exceeded its time limit; simplify the expression"
            ) from error
        return match.span() if match is not None else None

    def matches(self, text):
        return self.find(text) is not None
