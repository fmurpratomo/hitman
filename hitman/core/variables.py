"""Substitute ``{{name}}`` placeholders in a request before it is sent.

Substitution is a single pass: a variable's *value* is never rescanned for
further placeholders. That keeps the result predictable, makes cycles
impossible, and stops a value fetched from somewhere else from smuggling in a
reference to a different variable.

An unknown name is left in place rather than replaced with an empty string.
Sending a request to ``{{base_url}}/users`` fails in an obvious way; sending it
to ``/users`` fails in a baffling one. The names that could not be resolved are
returned so the UI can say which ones are missing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from hitman.core.models import KeyValue, Request

# {{name}}, tolerating inner whitespace. Names look like identifiers, extended
# with dot and dash so api.key and base-url both work.
PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*\}\}")


@dataclass
class Substitution:
    request: Request
    unresolved: list[str]


def find_names(text: str) -> list[str]:
    """Every variable name referenced in a string, in order of appearance."""
    return PLACEHOLDER.findall(text or "")


def _apply(text: str, variables: dict[str, str], missing: list[str]) -> str:
    def swap(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in variables:
            return variables[name]
        if name not in missing:
            missing.append(name)
        return match.group(0)  # leave the placeholder visible

    return PLACEHOLDER.sub(swap, text or "")


def _apply_rows(
    rows: list[KeyValue], variables: dict[str, str], missing: list[str]
) -> list[KeyValue]:
    # Disabled rows are never sent, so they are never resolved either — and a
    # name used only by a disabled row must not be reported as missing.
    return [
        KeyValue(
            key=_apply(row.key, variables, missing),
            value=_apply(row.value, variables, missing),
            enabled=True,
        )
        if row.enabled
        else row
        for row in rows
    ]


def substitute(request: Request, variables: dict[str, str]) -> Substitution:
    """Resolve every placeholder in the parts of the request that get sent."""
    missing: list[str] = []
    resolved = replace(
        request,
        url=_apply(request.url, variables, missing),
        params=_apply_rows(request.params, variables, missing),
        headers=_apply_rows(request.headers, variables, missing),
        body=_apply(request.body, variables, missing)
        if request.body_type in ("json", "raw")
        else request.body,
        form_fields=_apply_rows(request.form_fields, variables, missing),
    )
    return Substitution(request=resolved, unresolved=missing)
