"""Translate the browser form payload into a :class:`Request` or :class:`Scenario`."""

from __future__ import annotations

from hitman.core.assertions import KINDS, OPS, Assertion
from hitman.core.models import BODY_TYPES, DEFAULT_TIMEOUT, KeyValue, Request
from hitman.core.scenarios import CAPTURE_SOURCES, ON_FAILURE, Capture, Scenario, Step

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
        # Absent means "not specified", which must not silently weaken the
        # request. Only an explicit "0" turns these off — a dropped field
        # would otherwise disable TLS verification without anyone asking.
        follow_redirects=str(form.get("follow_redirects", "1")) != "0",
        verify_tls=str(form.get("verify_tls", "1")) != "0",
        timeout=max(MIN_TIMEOUT, min(timeout, MAX_TIMEOUT)),
    )


# --- scenarios ----------------------------------------------------------
#
# A scenario is a list of steps, and each step holds two lists of its own, so
# the flat parallel arrays a form submits cannot express the nesting on their
# own. Every step therefore carries a *uid* — an opaque string minted by the
# template for a saved step and by app.js for a new one — and each assertion
# and capture row carries the uid of the step it belongs to. Order comes from
# the DOM order of the step rows, so adding, deleting and reordering steps all
# work without renumbering anything.


def _ordered(form, prefix: str, fields: tuple) -> list[dict]:
    """Zip the parallel arrays of one row type back into dicts, in order."""
    columns = {name: form.getlist(f"{prefix}_{name}") for name in fields}
    length = max((len(values) for values in columns.values()), default=0)
    rows = []
    for index in range(length):
        rows.append(
            {
                name: str(values[index]) if index < len(values) else ""
                for name, values in columns.items()
            }
        )
    return rows


_UNARY_OPS = ("exists", "not_exists")


def _assertions(form) -> dict[str, list[Assertion]]:
    grouped: dict[str, list[Assertion]] = {}
    for row in _ordered(
        form, "assert", ("step", "enabled", "kind", "target", "op", "value")
    ):
        op = row["op"] if row["op"] in OPS else "eq"
        # An untouched row the user added and abandoned would otherwise be
        # stored as "status eq <nothing>" and fail every run from then on.
        if not row["target"].strip() and not row["value"].strip() and op not in _UNARY_OPS:
            continue
        grouped.setdefault(row["step"], []).append(
            Assertion(
                kind=row["kind"] if row["kind"] in KINDS else "status",
                target=row["target"].strip(),
                op=op,
                value=row["value"],
                enabled=row["enabled"] != "0",
            )
        )
    return grouped


def _captures(form) -> dict[str, list[Capture]]:
    grouped: dict[str, list[Capture]] = {}
    for row in _ordered(form, "capture", ("step", "enabled", "name", "source", "path")):
        if not row["name"].strip():
            continue  # a capture with no variable name binds nothing
        grouped.setdefault(row["step"], []).append(
            Capture(
                name=row["name"].strip(),
                source=row["source"] if row["source"] in CAPTURE_SOURCES else "json",
                path=row["path"].strip(),
                enabled=row["enabled"] != "0",
            )
        )
    return grouped


def scenario_from_form(form) -> Scenario:
    assertions = _assertions(form)
    captures = _captures(form)

    steps = []
    for row in _ordered(form, "step", ("uid", "enabled", "name", "request_id")):
        raw_id = row["request_id"].strip()
        uid = row["uid"]
        steps.append(
            Step(
                name=row["name"].strip(),
                request_id=int(raw_id) if raw_id.isdigit() else None,
                # pop, not get: two steps sharing a uid must not both claim
                # the same checks.
                assertions=assertions.pop(uid, []),
                captures=captures.pop(uid, []),
                enabled=row["enabled"] != "0",
            )
        )

    on_failure = str(form.get("on_failure") or "stop")
    return Scenario(
        name=str(form.get("scenario_name") or "").strip() or "Untitled scenario",
        description=str(form.get("scenario_description") or "").strip(),
        steps=steps,
        on_failure=on_failure if on_failure in ON_FAILURE else "stop",
    )
