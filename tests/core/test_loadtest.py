"""The load runner, exercised with fake engines.

As with the scenario runner, sending is injected so the rules worth pinning
down — how many iterations run, what counts as a failure, how results are
folded — can be tested without a server.
"""

import random
import threading
from dataclasses import dataclass
from typing import ClassVar

from hitman.core.assertions import Assertion
from hitman.core.loadtest import (
    ERROR_KINDS,
    OTHER_ERRORS,
    TIMELINE_BARS,
    Iteration,
    Latency,
    LoadResult,
    LoadRun,
    LoadTest,
    request_iteration,
    scenario_iteration,
)
from hitman.core.models import Request, Response
from hitman.core.scenarios import Scenario, Step


@dataclass
class Saved:
    name: str
    request: Request


class FakeEngine:
    """Answers every send with the same response, and counts what it sent."""

    instances: ClassVar[list] = []

    def __init__(self, status=200, error=None, elapsed_ms=5.0):
        self.status = status
        self.error = error
        self.elapsed_ms = elapsed_ms
        self.sent = []
        self.closed = False
        FakeEngine.instances.append(self)

    def send(self, request):
        self.sent.append(request)
        if self.error:
            return Response(engine="fake", error=self.error, elapsed_ms=self.elapsed_ms)
        return Response(
            engine="fake", status=self.status, body='{"ok": true}', elapsed_ms=self.elapsed_ms
        )

    def close(self):
        self.closed = True


def run_to_end(test, iteration, new_engine=FakeEngine, **options):
    run = LoadRun(test, iteration=iteration, new_engine=new_engine, target="GET · x", **options)
    run.start()
    assert run.wait(timeout=10), "the run did not finish"
    return run.snapshot()


def by_iterations(count, users=4, **fields):
    return LoadTest(name="t", users=users, stop_after="iterations", limit=count, **fields)


GET = request_iteration("Ping", Request(url="http://x.test/ping"))


# --- latency ------------------------------------------------------------


def test_percentiles_are_within_one_percent():
    generator = random.Random(7)
    samples = [generator.lognormvariate(3, 1) for _ in range(20_000)]
    latency = Latency()
    for value in samples:
        latency.add(value)

    ordered = sorted(samples)
    summary = latency.summary()
    for p, got in ((50, summary.p50), (90, summary.p90), (95, summary.p95), (99, summary.p99)):
        exact = ordered[int(len(ordered) * p / 100) - 1]
        assert abs(got - exact) / exact < 0.011, (p, got, exact)


def test_min_max_and_mean_are_exact():
    latency = Latency()
    for value in (2.0, 4.0, 9.0):
        latency.add(value)
    summary = latency.summary()
    assert (summary.min, summary.max, summary.mean) == (2.0, 9.0, 5.0)


def test_a_percentile_never_leaves_the_observed_range():
    latency = Latency()
    latency.add(10.0)
    assert latency.percentile(50) == 10.0
    assert latency.percentile(99) == 10.0


def test_an_empty_summary_is_zeros():
    assert Latency().summary().p95 == 0.0


# --- stop conditions ----------------------------------------------------


def test_an_iteration_limit_is_exact_across_users():
    FakeEngine.instances = []
    result = run_to_end(by_iterations(103, users=7), GET)
    assert result.requests == 103
    assert result.iterations == 103
    assert sum(len(engine.sent) for engine in FakeEngine.instances) == 103
    assert result.state == "finished"


def test_every_user_gets_its_own_engine_and_closes_it():
    FakeEngine.instances = []
    run_to_end(by_iterations(20, users=5), GET)
    assert len(FakeEngine.instances) == 5
    assert all(engine.closed for engine in FakeEngine.instances)


def test_a_duration_ends_the_run():
    result = run_to_end(LoadTest(users=2, stop_after="seconds", limit=1), GET)
    assert result.state == "finished"
    assert 1000 <= result.elapsed_ms < 3000
    assert result.requests > 0


def test_stop_ends_the_run_early_and_says_so():
    started = threading.Event()

    def slow(send):
        started.set()
        threading.Event().wait(0.01)
        return GET(send)

    run = LoadRun(
        LoadTest(users=3, stop_after="seconds", limit=60),
        iteration=slow,
        new_engine=FakeEngine,
        target="x",
    )
    run.start()
    assert started.wait(5)
    run.stop()
    assert run.wait(timeout=5)
    result = run.snapshot()
    assert result.state == "stopped"
    assert result.elapsed_ms < 60_000


def test_stopping_a_finished_run_does_not_relabel_it():
    run = LoadRun(by_iterations(3), iteration=GET, new_engine=FakeEngine, target="x")
    run.start()
    assert run.wait(timeout=5)
    run.stop()
    assert run.snapshot().state == "finished"


def test_on_finish_sees_the_final_state_once():
    seen = []
    run = LoadRun(
        by_iterations(5),
        iteration=GET,
        new_engine=FakeEngine,
        target="x",
        on_finish=lambda run: seen.append(run.snapshot()),
    )
    run.start()
    assert run.wait(timeout=5)
    assert len(seen) == 1
    assert seen[0].state == "finished"
    assert seen[0].requests == 5


# --- what counts as failed ----------------------------------------------


def test_a_server_error_is_a_failure_and_is_counted_by_status():
    result = run_to_end(by_iterations(10), GET, new_engine=lambda: FakeEngine(status=503))
    assert result.failed == 10
    assert result.statuses == [("503", 10)]
    assert result.failed_pct == 100.0


def test_a_redirect_is_not_a_failure():
    result = run_to_end(by_iterations(4), GET, new_engine=lambda: FakeEngine(status=302))
    assert result.failed == 0


def test_a_transport_error_is_grouped_by_message():
    result = run_to_end(
        by_iterations(6), GET, new_engine=lambda: FakeEngine(error="Connection refused")
    )
    assert result.statuses == [("ERR", 6)]
    assert result.errors == [("Connection refused", 6)]


def test_distinct_errors_are_capped():
    counter = iter(range(1000))
    lock = threading.Lock()

    def unique(send):
        with lock:
            number = next(counter)
        return Iteration(passed=False, error=f"request {number} failed")

    result = run_to_end(by_iterations(ERROR_KINDS + 10), unique)
    assert len(result.errors) == ERROR_KINDS + 1
    assert dict(result.errors)[OTHER_ERRORS] == 10


def test_an_iteration_that_raises_is_recorded_and_the_user_carries_on():
    def broken(send):
        raise ValueError("bad")

    result = run_to_end(by_iterations(5, users=1), broken)
    assert result.iterations == 5
    assert result.iterations_failed == 5
    assert result.errors == [("ValueError: bad", 5)]


# --- scenarios ----------------------------------------------------------


def test_a_scenario_pass_reports_each_step_and_the_pass():
    lookup = {
        1: Saved("Log in", Request(method="POST", url="http://x.test/login")),
        2: Saved("Profile", Request(url="http://x.test/me")),
    }.get
    scenario = Scenario(
        name="Flow",
        steps=[
            Step(request_id=1),
            Step(request_id=2, assertions=[Assertion(kind="status", op="eq", value="201")]),
        ],
    )
    result = run_to_end(
        LoadTest(target_kind="scenario", users=2, stop_after="iterations", limit=6),
        scenario_iteration(scenario, lookup=lookup, variables={}),
    )
    assert result.iterations == 6
    assert result.requests == 12
    assert [(row.label, row.requests) for row in result.labels] == [("Log in", 6), ("Profile", 6)]
    # Every response was a 200, so no request failed; the check on step 2 did.
    assert result.failed == 0
    assert result.iterations_failed == 6
    assert result.errors == [("Profile: status eq 201 failed", 6)]


def test_captures_do_not_leak_between_passes():
    lookup = {1: Saved("Who", Request(url="http://x.test/{{token}}"))}.get
    scenario = Scenario(steps=[Step(request_id=1)])
    variables = {"token": "seed"}
    FakeEngine.instances = []
    run_to_end(
        LoadTest(target_kind="scenario", users=1, stop_after="iterations", limit=3),
        scenario_iteration(scenario, lookup=lookup, variables=variables),
    )
    assert variables == {"token": "seed"}
    urls = {request.url for request in FakeEngine.instances[0].sent}
    assert urls == {"http://x.test/seed"}


# --- the shape of the result --------------------------------------------


def test_a_long_duration_is_folded_into_a_bounded_timeline():
    run = LoadRun(
        LoadTest(stop_after="seconds", limit=3600), iteration=GET, new_engine=FakeEngine, target="x"
    )
    timeline = run.snapshot().timeline
    assert len(timeline) == TIMELINE_BARS
    assert sum(item.width_s for item in timeline) == 3600


def test_a_short_duration_gets_a_bar_per_second_from_the_start():
    run = LoadRun(
        LoadTest(stop_after="seconds", limit=30), iteration=GET, new_engine=FakeEngine, target="x"
    )
    timeline = run.snapshot().timeline
    assert len(timeline) == 30
    assert {item.width_s for item in timeline} == {1}


def test_a_live_timeline_leaves_out_the_second_under_way():
    """Half a second of requests drawn as a whole one would read as a collapse."""
    now = [100.0]
    calls = []
    paused = threading.Event()
    release = threading.Event()

    def staged(send):
        calls.append(None)
        if len(calls) == 2:
            now[0] = 101.5  # recorded in second 1, which is not over yet
        if len(calls) == 3:
            paused.set()
            release.wait(5)
        return GET(send)

    run = LoadRun(
        by_iterations(3, users=1),
        iteration=staged,
        new_engine=FakeEngine,
        target="x",
        clock=lambda: now[0],
    )
    run.start()
    assert paused.wait(5)
    live = run.snapshot()
    assert live.state == "running"
    assert [item.requests for item in live.timeline] == [1]

    release.set()
    assert run.wait(timeout=5)
    assert [item.requests for item in run.snapshot().timeline] == [1, 2]


def test_a_result_round_trips_through_json():
    result = run_to_end(by_iterations(8), GET, new_engine=lambda: FakeEngine(status=500))
    assert LoadResult.from_dict(result.to_dict()) == result


def test_a_load_test_round_trips_and_rejects_unknown_values():
    test = LoadTest(name="x", target_kind="scenario", target_id=4, users=20, limit=90)
    assert LoadTest.from_dict(test.to_dict()) == test
    odd = LoadTest.from_dict({"target_kind": "mystery", "stop_after": "forever"})
    assert (odd.target_kind, odd.stop_after) == ("request", "seconds")
