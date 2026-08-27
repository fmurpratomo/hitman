"""Translate the browser form payload into a :class:`Request`."""

from __future__ import annotations

from hitman.core.models import BODY_TYPES, DEFAULT_TIMEOUT, KeyValue, Request

MIN_TIMEOUT = 0.1
MAX_TIMEOUT = 600.0


def _rows(form, prefix: str) -> list[KeyValue]:
    """Read the parallel key/value/enabled arrays one table submits.

    The three arrays stay aligned because the template always emits all three
    inputs per row — including a hidden ``_enabled`` field, since an unchecked
    checkbox submits nothing and would shift the array.
    """
    keys = form.getlist(f"{prefix}_key")
    values = form.getlist(f"{prefix}_value")
    flags = form.getlist(f"{prefix}_enabled")

    rows = []
    for index, key in enumerate(keys):
        value = values[index] if index < len(values) else ""
        flag = flags[index] if index < len(flags) else "1"
        if not key.strip() and not str(value).strip():
            continue
        rows.append(KeyValue(key.strip(), str(value), flag == "1"))
    return rows


def request_from_form(form) -> Request:
    body_type = str(form.get("body_type") or "none")
    if body_type not in BODY_TYPES:
        body_type = "none"

    try:
        timeout = float(form.get("timeout") or DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT

    return Request(
        method=str(form.get("method") or "GET").upper(),
        url=str(form.get("url") or "").strip(),
        params=_rows(form, "param"),
        headers=_rows(form, "header"),
        body_type=body_type,
        body=str(form.get("body") or ""),
        form_fields=_rows(form, "field"),
        follow_redirects=form.get("follow_redirects") == "1",
        verify_tls=form.get("verify_tls") == "1",
        timeout=max(MIN_TIMEOUT, min(timeout, MAX_TIMEOUT)),
    )
