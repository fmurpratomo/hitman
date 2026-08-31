"""Scenarios: saved requests run in order, each with its own checks.

Sequence is the point. A scenario logs in, pulls the token out of the response,
and the next step sends it — so the runner threads a single variable mapping
through every step, seeded from the active environment and overlaid by each
step's captures. That mapping is fed to the existing
:func:`hitman.core.variables.substitute`, which means a captured value reaches
the next request by exactly the same route an environment variable does, and a
saved request written as ``{{base_url}}/me`` works in both.

A step names a *saved request* rather than embedding one. Requests are already
nameable, foldered and duplicable; a scenario that copied them would fork the
moment you fixed a URL in one place and not the other.

Nothing here imports from ``hitman.web``. The two things the runner cannot do
alone — find a saved request, and send one — are injected by the caller:

``lookup(request_id)``
    returns something with ``.name`` and ``.request``, or ``None``.
``send(request)``
    returns a :class:`Response`. Never raises; a failure is a Response with
    ``error`` set, which is the contract both engines already keep.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Callable

from hitman.core.assertions import Assertion, CheckResult, run_checks
from hitman.core.jsonpath import MISSING, extract, render
from hitman.core.models import Request, Response
from hitman.core.variables import substitute

CAPTURE_SOURCES = ("json", "header", "status", "body")
ON_FAILURE = ("stop", "continue")

# Outcomes a step can reach. "failed" covers both a broken assertion and a
# request that never completed: the report distinguishes them with `error`,
# but for counting and for deciding whether to halt they are the same thing.
OUTCOMES = ("passed", "failed", "skipped")


@dataclass
class Capture:
    """Pull one value out of a response and bind it to ``{{name}}``."""

    name: str = ""
    source: str = "json"
    path: str = ""
    enabled: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Capture:
        return cls(
            name=data.get("name", ""),
            source=data.get("source", "json"),
            path=data.get("path", ""),
            enabled=data.get("enabled", True),
        )


@dataclass
class Step:
    name: str = ""
    request_id: int | None = None
    assertions: list[Assertion] = field(default_factory=list)
    captures: list[Capture] = field(default_factory=list)
    enabled: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Step:
        raw_id = data.get("request_id")
        return cls(
            name=data.get("name", ""),
            request_id=int(raw_id) if raw_id not in (None, "") else None,
            assertions=[Assertion.from_dict(item) for item in data.get("assertions") or []],
            captures=[Capture.from_dict(item) for item in data.get("captures") or []],
            enabled=data.get("enabled", True),
        )


@dataclass
class Scenario:
    """Carries no id: identity belongs to the storage layer, as with Request."""

    name: str = ""
    description: str = ""
    steps: list[Step] = field(default_factory=list)
    on_failure: str = "stop"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Scenario:
        on_failure = data.get("on_failure", "stop")
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            steps=[Step.from_dict(item) for item in data.get("steps") or []],
            on_failure=on_failure if on_failure in ON_FAILURE else "stop",
        )


@dataclass
class StepResult:
    name: str = ""
    request: Request | None = None
    response: Response | None = None
    checks: list[CheckResult] = field(default_factory=list)
    captured: list[tuple[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    outcome: str = "skipped"
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> StepResult:
        request = data.get("request")
        response = data.get("response")
        return cls(
            name=data.get("name", ""),
            request=Request.from_dict(request) if request else None,
            response=Response.from_dict(response) if response else None,
            checks=[CheckResult.from_dict(item) for item in data.get("checks") or []],
            captured=[tuple(pair) for pair in data.get("captured") or []],
            notes=list(data.get("notes") or []),
            outcome=data.get("outcome", "skipped"),
            error=data.get("error"),
        )


@dataclass
class ScenarioResult:
    name: str = ""
    engine: str = "httpx"
    environment: str = ""
    steps: list[StepResult] = field(default_factory=list)
    elapsed_ms: float = 0.0

    def count(self, outcome: str) -> int:
        return sum(1 for step in self.steps if step.outcome == outcome)

    @property
    def passed(self) -> bool:
        """Green only if something ran and nothing failed.

        A scenario whose every step is disabled is not a pass — it is a
        scenario that did not test anything, and reporting that as success is
        how a suite quietly stops protecting you.
        """
        return self.count("failed") == 0 and self.count("passed") > 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ScenarioResult:
        return cls(
            name=data.get("name", ""),
            engine=data.get("engine", "httpx"),
            environment=data.get("environment", ""),
            steps=[StepResult.from_dict(item) for item in data.get("steps") or []],
            elapsed_ms=data.get("elapsed_ms", 0.0),
        )


def capture_value(capture: Capture, response: Response) -> tuple[str | None, str | None]:
    """``(value, problem)`` — exactly one of the two is ever set."""
    source = capture.source
    if source == "status":
        if response.status is None:
            return None, "the request did not complete"
        return str(response.status), None
    if source == "body":
        return response.body, None
    if source == "header":
        wanted = capture.path.strip().lower()
        if not wanted:
            return None, "no header name given"
        for key, value in response.headers:
            if key.lower() == wanted:
                return value, None
        return None, f"no {capture.path.strip()} header in the response"
    if source == "json":
        try:
            data = json.loads(response.body)
        except ValueError:
            return None, "the response body is not JSON"
        found = extract(data, capture.path)
        if found is MISSING:
            return None, f"{capture.path.strip() or '$'} was not found in the response"
        return render(found), None
    return None, f"unknown capture source {source!r}"


def _run_captures(
    captures: list[Capture], response: Response, values: dict[str, str]
) -> tuple[list[tuple[str, str]], list[str]]:
    """Bind each capture into ``values``. Mutating it is the chaining."""
    taken: list[tuple[str, str]] = []
    problems: list[str] = []
    for capture in captures:
        if not capture.enabled:
            continue
        name = capture.name.strip()
        if not name:
            continue
        value, problem = capture_value(capture, response)
        if problem is not None:
            # A capture that misses is a failure, not a warning. Left as a
            # warning, the next step sends the literal text "{{token}}" and
            # fails somewhere far away from the actual cause.
            problems.append(f"Could not capture {{{{{name}}}}}: {problem}")
            continue
        values[name] = value
        taken.append((name, value))
    return taken, problems


def run_scenario(
    scenario: Scenario,
    *,
    lookup: Callable[[int], object],
    send: Callable[[Request], Response],
    variables: dict | None = None,
    engine_name: str = "httpx",
    environment: str = "",
) -> ScenarioResult:
    """Run every step in order and report what each one did."""
    values = dict(variables or {})
    results: list[StepResult] = []
    halted = False
    started = time.perf_counter()

    for index, step in enumerate(scenario.steps, start=1):
        saved = lookup(step.request_id) if step.request_id else None
        label = step.name.strip() or getattr(saved, "name", "") or f"Step {index}"

        if not step.enabled:
            results.append(StepResult(name=label, outcome="skipped", error="Disabled."))
            continue
        if halted:
            results.append(
                StepResult(
                    name=label,
                    outcome="skipped",
                    error="Skipped after an earlier failure.",
                )
            )
            continue
        if saved is None:
            results.append(
                StepResult(
                    name=label,
                    outcome="failed",
                    error=(
                        "No saved request is chosen for this step."
                        if step.request_id is None
                        else "The saved request for this step no longer exists."
                    ),
                )
            )
            halted = scenario.on_failure == "stop"
            continue

        outcome = substitute(saved.request, values)
        notes = []
        if outcome.unresolved:
            notes.append(
                "No value for "
                + ", ".join("{{" + name + "}}" for name in outcome.unresolved)
                + " — sent as written."
            )

        if not outcome.request.url.strip():
            results.append(
                StepResult(
                    name=label,
                    request=outcome.request,
                    notes=notes,
                    outcome="failed",
                    error="The saved request has no URL.",
                )
            )
            halted = scenario.on_failure == "stop"
            continue

        response = send(outcome.request)
        result = StepResult(
            name=label, request=outcome.request, response=response, notes=notes
        )

        if response.error:
            # The request never completed, so there is nothing to assert about.
            result.outcome = "failed"
            result.error = response.error
        else:
            result.checks = run_checks(step.assertions, response)
            taken, problems = _run_captures(step.captures, response, values)
            result.captured = taken
            failed_check = any(not item.passed for item in result.checks)
            result.outcome = "failed" if (failed_check or problems) else "passed"
            if problems:
                result.error = " ".join(problems)

        results.append(result)
        if result.outcome == "failed" and scenario.on_failure == "stop":
            halted = True

    return ScenarioResult(
        name=scenario.name,
        engine=engine_name,
        environment=environment,
        steps=results,
        elapsed_ms=(time.perf_counter() - started) * 1000,
    )
