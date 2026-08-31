"""Checks run against a :class:`Response` after a scenario step has been sent.

An assertion is data, not code. There is no scripting language here on purpose:
a stored test that can execute arbitrary JavaScript is a different security
model from the one in the design, and the four things people actually assert
about an HTTP response — its status, a field in its JSON, a header, and how
long it took — fit in a dropdown.

Comparison is deliberately forgiving about types, because the expected value
arrives from a text input and the actual one from ``json.loads``. Typing
``200`` must match the integer ``200``, and typing ``true`` must match the
boolean. See :func:`_equal`.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

from hitman.core.jsonpath import MISSING, extract, render
from hitman.core.models import Response

KINDS = ("status", "json", "header", "body", "time_ms")

OPS = (
    "eq",
    "ne",
    "contains",
    "not_contains",
    "matches",
    "exists",
    "not_exists",
    "lt",
    "lte",
    "gt",
    "gte",
)

# Ops that ask only whether a value is there, ignoring the expected value.
_UNARY = ("exists", "not_exists")
_ORDERED = ("lt", "lte", "gt", "gte")

# What the target box means, per kind. Empty means the kind has no target.
TARGET_HINT = {
    "status": "",
    "time_ms": "",
    "body": "",
    "header": "header name",
    "json": "json path, e.g. data.0.id",
}


@dataclass
class Assertion:
    kind: str = "status"
    target: str = ""
    op: str = "eq"
    value: str = ""
    enabled: bool = True

    def describe(self) -> str:
        """One line naming what is being checked, for the report."""
        subject = self.kind
        if self.target.strip():
            subject = f"{self.kind} {self.target.strip()}"
        operator = self.op.replace("_", " ")
        if self.op in _UNARY:
            return f"{subject} {operator}"
        return f"{subject} {operator} {self.value}"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Assertion:
        return cls(
            kind=data.get("kind", "status"),
            target=data.get("target", ""),
            op=data.get("op", "eq"),
            value=data.get("value", ""),
            enabled=data.get("enabled", True),
        )


@dataclass
class CheckResult:
    """The outcome of one assertion, as plain strings so it stores as JSON."""

    label: str = ""
    passed: bool = False
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> CheckResult:
        return cls(
            label=data.get("label", ""),
            passed=data.get("passed", False),
            detail=data.get("detail", ""),
        )


def _number(value: object) -> float | None:
    # bool is an int in Python; treating True as 1.0 in a `<` comparison would
    # silently answer a question nobody asked.
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _equal(actual: object, expected: str) -> bool:
    """Compare a decoded value against typed text, JSON first then as a string."""
    text = expected.strip()
    try:
        if actual == json.loads(text):
            return True
    except ValueError:
        pass  # not JSON, so it was meant literally
    return render(actual) == text


def _contains(actual: object, expected: str) -> bool:
    if isinstance(actual, (list, tuple)):
        return any(_equal(item, expected) for item in actual)
    if isinstance(actual, dict):
        return expected in actual
    return expected in render(actual)


def _compare(op: str, actual: object, expected: str) -> tuple[bool, str]:
    shown = "not found" if actual is MISSING else render(actual)

    if op == "exists":
        return actual is not MISSING, shown
    if op == "not_exists":
        return actual is MISSING, shown
    if actual is MISSING:
        # Every remaining op needs something to compare against.
        return False, "not found"

    if op in _ORDERED:
        left = _number(actual)
        right = _number(expected)
        if left is None:
            return False, f"{shown} is not a number"
        if right is None:
            return False, f"expected {expected!r}, which is not a number"
        return {
            "lt": left < right,
            "lte": left <= right,
            "gt": left > right,
            "gte": left >= right,
        }[op], shown

    if op in ("eq", "ne"):
        same = _equal(actual, expected)
        return (same if op == "eq" else not same), shown

    if op in ("contains", "not_contains"):
        found = _contains(actual, expected)
        return (found if op == "contains" else not found), shown

    if op == "matches":
        try:
            pattern = re.compile(expected)
        except re.error as exc:
            return False, f"invalid regular expression: {exc}"
        return pattern.search(render(actual)) is not None, shown

    return False, f"unknown comparison {op!r}"


def _actual(assertion: Assertion, response: Response) -> tuple[object, str | None]:
    """The value under test, or a reason it could not be read at all."""
    kind = assertion.kind
    if kind == "status":
        return (MISSING if response.status is None else response.status), None
    if kind == "time_ms":
        return round(response.elapsed_ms, 1), None
    if kind == "body":
        return response.body, None
    if kind == "header":
        wanted = assertion.target.strip().lower()
        if not wanted:
            return MISSING, "no header name given"
        for key, value in response.headers:
            if key.lower() == wanted:
                return value, None
        return MISSING, None
    if kind == "json":
        try:
            data = json.loads(response.body)
        except ValueError:
            return MISSING, "the response body is not JSON"
        return extract(data, assertion.target), None
    return MISSING, f"unknown check {kind!r}"


def check(assertion: Assertion, response: Response) -> CheckResult:
    label = assertion.describe()
    actual, problem = _actual(assertion, response)
    if problem is not None:
        return CheckResult(label=label, passed=False, detail=problem)
    passed, detail = _compare(assertion.op, actual, assertion.value)
    return CheckResult(label=label, passed=passed, detail=detail)


def run_checks(assertions: list[Assertion], response: Response) -> list[CheckResult]:
    """Every enabled assertion, in order. A disabled one is not a check."""
    return [check(item, response) for item in assertions if item.enabled]


def blank_assertion() -> Assertion:
    """The row the editor offers for a new check."""
    return Assertion(kind="status", target="", op="eq", value="200", enabled=True)
