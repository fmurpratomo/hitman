"""Performance tests: a saved request or a scenario, sent by many users at once.

A *virtual user* is a thread that repeats one *iteration* until the test's stop
condition is met. An iteration is one send of a saved request, or one full pass
through a scenario with its checks and captures — so a scenario that logs in
and then fetches a page is measured as that flow under load, not as loose
requests that would fail without the token.

Each user gets its own engine from ``new_engine``. Connections are kept alive
per user, as a real client keeps them, and never shared between users.

Results are summarised as they arrive rather than kept. A long test at a few
thousand requests a second is millions of samples; :class:`Latency` holds them
in a few hundred counters instead, which is what makes an hour-long test safe
to run and a live report cheap to draw every second.

Like the scenario runner, nothing here imports from ``hitman.web``. The caller
builds the iteration (:func:`request_iteration`, :func:`scenario_iteration`)
from whatever it looked up, and hands in a factory for engines.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import asdict, dataclass, field, fields
from typing import Callable

from hitman.core.models import Request, Response
from hitman.core.scenarios import Scenario, ScenarioResult, run_scenario

TARGET_KINDS = ("request", "scenario")
STOP_AFTER = ("seconds", "iterations")

MAX_USERS = 200
MAX_SECONDS = 3600
MAX_ITERATIONS = 1_000_000

# The timeline is drawn as at most this many bars. A longer test is folded into
# wider slices rather than drawn as thousands of slivers.
TIMELINE_BARS = 60

# Distinct error messages kept per run. Past this, new ones are counted
# together, so a server that puts a request id in every error message cannot
# grow the report without bound.
ERROR_KINDS = 20
OTHER_ERRORS = "Other errors"

Send = Callable[[Request], Response]


@dataclass
class LoadTest:
    """Carries no id: identity belongs to the storage layer, as with Scenario."""

    name: str = ""
    description: str = ""
    folder: str = ""
    target_kind: str = "request"
    target_id: int | None = None
    users: int = 10
    ramp_up_s: int = 0
    stop_after: str = "seconds"
    limit: int = 30

    @property
    def target_key(self) -> str:
        """The value the editor's target picker uses, e.g. ``request:3``."""
        return f"{self.target_kind}:{self.target_id}" if self.target_id else ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> LoadTest:
        raw_id = data.get("target_id")
        kind = data.get("target_kind", "request")
        stop_after = data.get("stop_after", "seconds")
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            folder=data.get("folder", ""),
            target_kind=kind if kind in TARGET_KINDS else "request",
            target_id=int(raw_id) if raw_id not in (None, "") else None,
            users=int(data.get("users", 10)),
            ramp_up_s=int(data.get("ramp_up_s", 0)),
            stop_after=stop_after if stop_after in STOP_AFTER else "seconds",
            limit=int(data.get("limit", 30)),
        )


# --- measuring ----------------------------------------------------------


@dataclass
class LatencySummary:
    """Milliseconds, except count."""

    count: int = 0
    mean: float = 0.0
    min: float = 0.0
    p50: float = 0.0
    p90: float = 0.0
    p95: float = 0.0
    p99: float = 0.0
    max: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> LatencySummary:
        return cls(**{item.name: data.get(item.name, 0) for item in fields(cls)})


_GROWTH = math.log(1.01)


class Latency:
    """A running summary of response times, in bounded memory.

    Each sample lands in a logarithmic bucket 1% wide, so a percentile is read
    off a few hundred counters and is within 1% of the exact value. Count, min,
    max and mean are exact.
    """

    def __init__(self) -> None:
        self.count = 0
        self.total = 0.0
        self.low = math.inf
        self.high = 0.0
        self._buckets: dict[int, int] = {}

    def add(self, ms: float) -> None:
        ms = max(float(ms), 0.001)
        self.count += 1
        self.total += ms
        self.low = min(self.low, ms)
        self.high = max(self.high, ms)
        # Bucketed in microseconds, so every sample from 1µs up has an index >= 0.
        index = int(math.log(ms * 1000) / _GROWTH)
        self._buckets[index] = self._buckets.get(index, 0) + 1

    def percentile(self, p: float) -> float:
        if not self.count:
            return 0.0
        rank = max(1, math.ceil(self.count * p / 100))
        seen = 0
        for index in sorted(self._buckets):
            seen += self._buckets[index]
            if seen >= rank:
                # The bucket's upper edge, but never outside what was observed.
                edge = math.exp((index + 1) * _GROWTH) / 1000
                return min(max(edge, self.low), self.high)
        return self.high

    def summary(self) -> LatencySummary:
        if not self.count:
            return LatencySummary()
        return LatencySummary(
            count=self.count,
            mean=self.total / self.count,
            min=self.low,
            p50=self.percentile(50),
            p90=self.percentile(90),
            p95=self.percentile(95),
            p99=self.percentile(99),
            max=self.high,
        )


class _Tally:
    __slots__ = ("count", "failed", "latency")

    def __init__(self) -> None:
        self.count = 0
        self.failed = 0
        self.latency = Latency()

    def add(self, ms: float, failed: bool) -> None:
        self.count += 1
        self.failed += int(failed)
        self.latency.add(ms)


def is_failure(response: Response) -> bool:
    """A send that never completed, or one the server answered with 4xx/5xx."""
    return response.error is not None or (response.status or 0) >= 400


# --- results ------------------------------------------------------------


@dataclass
class LabelStats:
    """One row of the per-request table: the request, or one scenario step."""

    label: str = ""
    requests: int = 0
    failed: int = 0
    latency: LatencySummary = field(default_factory=LatencySummary)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> LabelStats:
        return cls(
            label=data.get("label", ""),
            requests=data.get("requests", 0),
            failed=data.get("failed", 0),
            latency=LatencySummary.from_dict(data.get("latency") or {}),
        )


@dataclass
class TimeSlice:
    """Requests that completed in ``[start_s, start_s + width_s)``."""

    start_s: int = 0
    width_s: int = 1
    requests: int = 0
    failed: int = 0
    mean_ms: float = 0.0

    @property
    def rps(self) -> float:
        return self.requests / self.width_s

    @property
    def failed_rps(self) -> float:
        return self.failed / self.width_s

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> TimeSlice:
        return cls(**{item.name: data[item.name] for item in fields(cls) if item.name in data})


# Where a run is. "stopping" is the gap between pressing Stop and the last user
# finishing the iteration it was on.
STATES = ("running", "stopping", "finished", "stopped")


@dataclass
class LoadResult:
    name: str = ""
    target: str = ""
    target_kind: str = "request"
    engine: str = "httpx"
    environment: str = ""
    users: int = 0
    ramp_up_s: int = 0
    stop_after: str = "seconds"
    limit: int = 0
    state: str = "finished"
    elapsed_ms: float = 0.0
    requests: int = 0
    failed: int = 0
    latency: LatencySummary = field(default_factory=LatencySummary)
    statuses: list[tuple[str, int]] = field(default_factory=list)
    errors: list[tuple[str, int]] = field(default_factory=list)
    labels: list[LabelStats] = field(default_factory=list)
    iterations: int = 0
    iterations_failed: int = 0
    iteration_latency: LatencySummary = field(default_factory=LatencySummary)
    timeline: list[TimeSlice] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def live(self) -> bool:
        return self.state in ("running", "stopping")

    @property
    def rps(self) -> float:
        seconds = self.elapsed_ms / 1000
        return self.requests / seconds if seconds > 0 else 0.0

    @property
    def failed_pct(self) -> float:
        return 100 * self.failed / self.requests if self.requests else 0.0

    @property
    def progress(self) -> float:
        """How far through its stop condition the run is, from 0 to 1."""
        if not self.limit:
            return 1.0
        if self.stop_after == "iterations":
            return min(1.0, self.iterations / self.limit)
        return min(1.0, self.elapsed_ms / 1000 / self.limit)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> LoadResult:
        return cls(
            name=data.get("name", ""),
            target=data.get("target", ""),
            target_kind=data.get("target_kind", "request"),
            engine=data.get("engine", "httpx"),
            environment=data.get("environment", ""),
            users=data.get("users", 0),
            ramp_up_s=data.get("ramp_up_s", 0),
            stop_after=data.get("stop_after", "seconds"),
            limit=data.get("limit", 0),
            state=data.get("state", "finished"),
            elapsed_ms=data.get("elapsed_ms", 0.0),
            requests=data.get("requests", 0),
            failed=data.get("failed", 0),
            latency=LatencySummary.from_dict(data.get("latency") or {}),
            # JSON has no tuples; restore them so a result compares equal.
            statuses=[tuple(pair) for pair in data.get("statuses") or []],
            errors=[tuple(pair) for pair in data.get("errors") or []],
            labels=[LabelStats.from_dict(item) for item in data.get("labels") or []],
            iterations=data.get("iterations", 0),
            iterations_failed=data.get("iterations_failed", 0),
            iteration_latency=LatencySummary.from_dict(data.get("iteration_latency") or {}),
            timeline=[TimeSlice.from_dict(item) for item in data.get("timeline") or []],
            notes=list(data.get("notes") or []),
        )


# --- what each user repeats ---------------------------------------------


@dataclass
class Iteration:
    """What one pass by one user produced."""

    sends: list[tuple[str, Response]] = field(default_factory=list)
    passed: bool = True
    error: str | None = None


def request_iteration(label: str, request: Request) -> Callable[[Send], Iteration]:
    """Send one request. It arrives already resolved: every pass sends the same."""

    def iterate(send: Send) -> Iteration:
        response = send(request)
        return Iteration(
            sends=[(label, response)], passed=not is_failure(response), error=response.error
        )

    return iterate


def scenario_iteration(
    scenario: Scenario, *, lookup: Callable[[int], object], variables: dict
) -> Callable[[Send], Iteration]:
    """Run the whole scenario once.

    Captures stay inside the pass: each one starts again from ``variables``, so
    every pass logs in for itself the way a separate person would.
    """

    def iterate(send: Send) -> Iteration:
        result = run_scenario(scenario, lookup=lookup, send=send, variables=variables)
        return Iteration(
            sends=[(step.name, step.response) for step in result.steps if step.response],
            passed=result.passed,
            error=_first_failure(result),
        )

    return iterate


def _first_failure(result: ScenarioResult) -> str | None:
    """Why a pass failed, worded to group: no response values, no timings."""
    for step in result.steps:
        if step.outcome != "failed":
            continue
        if step.error:
            return f"{step.name}: {step.error}"
        check = next((item for item in step.checks if not item.passed), None)
        return f"{step.name}: {check.label} failed" if check else f"{step.name}: failed"
    if not result.passed:
        return "Nothing ran — every step is disabled."
    return None


# --- running ------------------------------------------------------------


class LoadRun:
    """One performance test in flight.

    start() returns at once; the users run on threads of their own. snapshot()
    may be called from any thread at any time, and is what both the live report
    and the stored record are drawn from.
    """

    def __init__(
        self,
        test: LoadTest,
        *,
        iteration: Callable[[Send], Iteration],
        new_engine: Callable[[], object],
        target: str,
        engine_name: str = "httpx",
        environment: str = "",
        notes: list[str] | None = None,
        on_finish: Callable[[LoadRun], None] | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.test = test
        self.target = target
        self.engine_name = engine_name
        self.environment = environment
        self.notes = list(notes or [])
        self._iteration = iteration
        self._new_engine = new_engine
        self._on_finish = on_finish
        self._clock = clock

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._done = threading.Event()
        self._started: float | None = None
        self._ended: float | None = None
        self._claimed = 0

        self._requests = _Tally()
        self._iterations = _Tally()
        self._labels: dict[str, _Tally] = {}
        self._statuses: dict[str, int] = {}
        self._errors: dict[str, int] = {}
        # [requests, failed, total ms] for each second since the start.
        self._seconds: list[list] = []

    def start(self) -> None:
        self._started = self._clock()
        threading.Thread(target=self._run, name="load-test", daemon=True).start()

    def stop(self) -> None:
        """Ask every user to finish the iteration it is on and go no further."""
        with self._lock:
            if self._ended is None:
                self._stop.set()

    @property
    def done(self) -> bool:
        """Every user has finished and on_finish has returned."""
        return self._done.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._done.wait(timeout)

    def _run(self) -> None:
        users = [
            threading.Thread(target=self._user, args=(index,), daemon=True)
            for index in range(max(1, self.test.users))
        ]
        for user in users:
            user.start()
        for user in users:
            user.join()
        with self._lock:
            self._ended = self._clock()
        try:
            if self._on_finish is not None:
                self._on_finish(self)
        finally:
            self._done.set()

    def _user(self, index: int) -> None:
        # Users join evenly spread over the ramp-up, the first one at once.
        delay = self.test.ramp_up_s * index / max(1, self.test.users)
        if delay and self._stop.wait(delay):
            return
        engine = self._new_engine()
        try:
            while self._claim():
                began = self._clock()
                try:
                    outcome = self._iteration(engine.send)
                except Exception as exc:  # noqa: BLE001 - one bad pass must not end the user
                    outcome = Iteration(passed=False, error=f"{type(exc).__name__}: {exc}")
                self._record(outcome, (self._clock() - began) * 1000)
        finally:
            close = getattr(engine, "close", None)
            if close is not None:
                close()

    def _claim(self) -> bool:
        """Whether this user may start another iteration, counting it if so."""
        if self._stop.is_set():
            return False
        with self._lock:
            if self.test.stop_after == "iterations":
                if self._claimed >= self.test.limit:
                    return False
            elif self._clock() - self._started >= self.test.limit:
                return False
            self._claimed += 1
            return True

    def _record(self, outcome: Iteration, elapsed_ms: float) -> None:
        with self._lock:
            # Placed by when the iteration finished. For a scenario every step
            # lands in that second, which is close enough for a chart.
            second = int(self._clock() - self._started)
            while len(self._seconds) <= second:
                self._seconds.append([0, 0, 0.0])
            slot = self._seconds[second]

            for label, response in outcome.sends:
                failed = is_failure(response)
                self._requests.add(response.elapsed_ms, failed)
                self._labels.setdefault(label, _Tally()).add(response.elapsed_ms, failed)
                key = "ERR" if response.status is None else str(response.status)
                self._statuses[key] = self._statuses.get(key, 0) + 1
                slot[0] += 1
                slot[1] += int(failed)
                slot[2] += response.elapsed_ms

            self._iterations.add(elapsed_ms, not outcome.passed)
            if outcome.error:
                message = outcome.error
                if message not in self._errors and len(self._errors) >= ERROR_KINDS:
                    message = OTHER_ERRORS
                self._errors[message] = self._errors.get(message, 0) + 1

    def snapshot(self) -> LoadResult:
        with self._lock:
            ended = self._ended
            if self._started is None:
                elapsed = 0.0
            else:
                elapsed = (ended if ended is not None else self._clock()) - self._started
            stopping = self._stop.is_set()
            if ended is None:
                state = "stopping" if stopping else "running"
            else:
                state = "stopped" if stopping else "finished"
            return LoadResult(
                name=self.test.name,
                target=self.target,
                target_kind=self.test.target_kind,
                engine=self.engine_name,
                environment=self.environment,
                users=self.test.users,
                ramp_up_s=self.test.ramp_up_s,
                stop_after=self.test.stop_after,
                limit=self.test.limit,
                state=state,
                elapsed_ms=elapsed * 1000,
                requests=self._requests.count,
                failed=self._requests.failed,
                latency=self._requests.latency.summary(),
                # Numeric codes in order, transport failures last.
                statuses=sorted(self._statuses.items(), key=lambda pair: (pair[0] == "ERR", pair[0])),
                errors=sorted(self._errors.items(), key=lambda pair: -pair[1]),
                labels=[
                    LabelStats(label, tally.count, tally.failed, tally.latency.summary())
                    for label, tally in self._labels.items()
                ],
                iterations=self._iterations.count,
                iterations_failed=self._iterations.failed,
                iteration_latency=self._iterations.latency.summary(),
                # While running, the second under way is left out: half a
                # second of requests drawn as a whole one reads as a collapse.
                timeline=self._timeline(
                    self._seconds if ended is not None else self._seconds[: int(elapsed)]
                ),
                notes=list(self.notes),
            )

    def _timeline(self, seconds: list[list]) -> list[TimeSlice]:
        """Per-second counts, folded into at most TIMELINE_BARS slices.

        A test with a set duration is laid out over that whole duration from the
        start, so the live chart fills in from the left instead of rescaling
        every second.
        """
        span = len(seconds)
        if self.test.stop_after == "seconds":
            span = max(span, self.test.limit)
        if not span:
            return []
        width = math.ceil(span / TIMELINE_BARS)
        slices = []
        for start in range(0, span, width):
            chunk = seconds[start : start + width]
            requests = sum(item[0] for item in chunk)
            slices.append(
                TimeSlice(
                    start_s=start,
                    width_s=min(width, span - start),
                    requests=requests,
                    failed=sum(item[1] for item in chunk),
                    mean_ms=sum(item[2] for item in chunk) / requests if requests else 0.0,
                )
            )
        return slices
