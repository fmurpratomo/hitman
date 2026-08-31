"""Read one value out of a decoded JSON body by a short path.

A deliberately small subset of JSONPath: dotted keys and numeric indices, so
``data.items.0.id`` and ``data.items[0].id`` both work, and a leading ``$`` is
tolerated because that is what people type. No filters, no wildcards, no
recursive descent — those need a real parser, and an assertion nobody can read
at a glance is not worth the dependency.

The consequence of that cut is that a key containing a dot cannot be reached.
That is rare enough in real APIs to be worth the simplicity.

A path that leads nowhere returns :data:`MISSING` rather than ``None``, because
a key holding JSON ``null`` and a key that is absent are different facts and an
``exists`` check has to tell them apart.
"""

from __future__ import annotations

import json
import re

_INDEX = re.compile(r"\[\s*(-?\d+)\s*\]")


class _Missing:
    """Sentinel for "nothing at that path". Falsy, and prints readably."""

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "<missing>"


MISSING = _Missing()


def segments(path: str) -> list[str]:
    """Split a path into its steps: ``items[0].id`` -> ``['items', '0', 'id']``."""
    cleaned = _INDEX.sub(r".\1", (path or "").strip().removeprefix("$"))
    return [part for part in cleaned.split(".") if part]


def extract(data: object, path: str) -> object:
    """Walk ``path`` through decoded JSON. An empty path is the whole document."""
    current = data
    for step in segments(path):
        if isinstance(current, dict):
            if step not in current:
                return MISSING
            current = current[step]
        elif isinstance(current, list):
            try:
                index = int(step)
            except ValueError:
                return MISSING
            try:
                current = current[index]
            except IndexError:
                return MISSING
        else:
            # A scalar has no members, so the path outlived the document.
            return MISSING
    return current


def render(value: object) -> str:
    """Display form of an extracted value.

    Strings pass through unquoted — a captured token must go into the next
    request as ``abc123``, never as ``"abc123"``. Everything else is JSON, so
    ``true``, ``null`` and ``{"a": 1}`` read the way the API wrote them.
    """
    if value is MISSING:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)
