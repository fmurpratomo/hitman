"""The runner, exercised with a fake lookup and a fake send.

Both are injected precisely so this can be tested without a server: the
sequencing rules — what runs, in what order, with which variables — are the
part worth pinning down, and they are pure.
"""

from dataclasses import dataclass

from hitman.core.assertions import Assertion
from hitman.core.models import KeyValue, Request, Response
from hitman.core.scenarios import (
    Capture,
    Scenario,
    ScenarioResult,
    Step,
    run_scenario,
)


@dataclass
class Saved:
    """Stands in for store.SavedRequest: a name and a request."""

    name: str
    request: Request


class Recorder:
    """A send() that answers from a script and remembers what it was sent."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []

    def __call__(self, request):
        self.sent.append(request)
        return self.replies.pop(0) if self.replies else ok()


def ok(body='{"ok": true}', status=200, headers=None):
    return Response(
        engine="httpx",
        status=status,
        body=body,
        headers=headers or [("Content-Type", "application/json")],
        elapsed_ms=5.0,
    )


def library(**requests):
    """A lookup over a fixed set of saved requests keyed by id."""
    table = {index: Saved(name, request) for index, (name, request) in enumerate(
        requests.items(), start=1
    )}
    return table.get


def test_steps_run_top_to_bottom():
    lookup = library(first=Request(url="http://x.test/1"), second=Request(url="http://x.test/2"))
    send = Recorder([ok(), ok()])
    result = run_scenario(
        Scenario(name="Two", steps=[Step(request_id=1), Step(request_id=2)]),
        lookup=lookup,
        send=send,
    )
    assert [request.url for request in send.sent] == ["http://x.test/1", "http://x.test/2"]
    assert [step.outcome for step in result.steps] == ["passed", "passed"]
    assert result.passed


def test_a_capture_reaches_the_next_step():
    """The whole point of running these in sequence."""
    lookup = library(
        login=Request(method="POST", url="http://x.test/login"),
        profile=Request(
            url="http://x.test/me", headers=[KeyValue("Authorization", "Bearer {{token}}")]
        ),
    )
    send = Recorder([ok('{"token": "abc123"}'), ok()])
    result = run_scenario(
        Scenario(
            steps=[
                Step(request_id=1, captures=[Capture(name="token", source="json", path="token")]),
                Step(request_id=2),
            ]
        ),
        lookup=lookup,
        send=send,
    )
    assert send.sent[1].headers[0].value == "Bearer abc123"
    assert result.steps[0].captured == [("token", "abc123")]


def test_a_capture_can_read_a_header_or_the_status():
    lookup = library(one=Request(url="http://x.test/"))
    send = Recorder([ok(headers=[("Location", "/next")])])
    result = run_scenario(
        Scenario(
            steps=[
                Step(
                    request_id=1,
                    captures=[
                        Capture(name="next", source="header", path="location"),
                        Capture(name="code", source="status"),
                    ],
                )
            ]
        ),
        lookup=lookup,
        send=send,
    )
    assert result.steps[0].captured == [("next", "/next"), ("code", "200")]


def test_a_capture_that_finds_nothing_fails_the_step():
    """Left as a warning, the next step sends the literal text {{token}}."""
    lookup = library(one=Request(url="http://x.test/"))
    result = run_scenario(
        Scenario(
            steps=[
                Step(request_id=1, captures=[Capture(name="token", source="json", path="token")])
            ]
        ),
        lookup=lookup,
        send=Recorder([ok('{"nothing": 1}')]),
    )
    assert result.steps[0].outcome == "failed"
    assert "Could not capture {{token}}" in result.steps[0].error


def test_environment_variables_seed_the_run_and_captures_win():
    lookup = library(
        one=Request(url="{{base}}/a"),
        two=Request(url="{{base}}/{{id}}"),
    )
    send = Recorder([ok('{"id": "42"}'), ok()])
    run_scenario(
        Scenario(
            steps=[
                Step(request_id=1, captures=[Capture(name="id", source="json", path="id")]),
                Step(request_id=2),
            ]
        ),
        lookup=lookup,
        send=send,
        variables={"base": "http://x.test", "id": "unused"},
    )
    assert send.sent[1].url == "http://x.test/42"


def test_an_unresolved_variable_is_a_note_not_a_failure():
    """Same rule as a single send: it goes out as written and says so."""
    lookup = library(one=Request(url="http://x.test/{{missing}}"))
    result = run_scenario(
        Scenario(steps=[Step(request_id=1)]), lookup=lookup, send=Recorder([ok()])
    )
    assert result.steps[0].outcome == "passed"
    assert "{{missing}}" in result.steps[0].notes[0]


def test_a_failing_assertion_stops_the_rest_by_default():
    lookup = library(one=Request(url="http://x.test/1"), two=Request(url="http://x.test/2"))
    send = Recorder([ok(status=500), ok()])
    result = run_scenario(
        Scenario(
            steps=[
                Step(request_id=1, assertions=[Assertion(op="eq", value="200")]),
                Step(request_id=2),
            ]
        ),
        lookup=lookup,
        send=send,
    )
    assert [step.outcome for step in result.steps] == ["failed", "skipped"]
    assert len(send.sent) == 1
    assert "Skipped after an earlier failure" in result.steps[1].error


def test_continue_runs_every_step_anyway():
    lookup = library(one=Request(url="http://x.test/1"), two=Request(url="http://x.test/2"))
    send = Recorder([ok(status=500), ok()])
    result = run_scenario(
        Scenario(
            on_failure="continue",
            steps=[
                Step(request_id=1, assertions=[Assertion(op="eq", value="200")]),
                Step(request_id=2),
            ],
        ),
        lookup=lookup,
        send=send,
    )
    assert [step.outcome for step in result.steps] == ["failed", "passed"]
    assert len(send.sent) == 2


def test_a_disabled_step_is_skipped_without_being_sent():
    lookup = library(one=Request(url="http://x.test/1"), two=Request(url="http://x.test/2"))
    send = Recorder([ok()])
    result = run_scenario(
        Scenario(steps=[Step(request_id=1, enabled=False), Step(request_id=2)]),
        lookup=lookup,
        send=send,
    )
    assert [step.outcome for step in result.steps] == ["skipped", "passed"]
    assert result.steps[0].error == "Disabled."
    assert len(send.sent) == 1


def test_a_step_pointing_at_a_deleted_request_fails_with_a_reason():
    result = run_scenario(
        Scenario(steps=[Step(name="Gone", request_id=99)]),
        lookup=lambda _: None,
        send=Recorder([]),
    )
    assert result.steps[0].outcome == "failed"
    assert "no longer exists" in result.steps[0].error


def test_a_step_with_nothing_chosen_says_so_rather_than_blaming_a_deletion():
    result = run_scenario(
        Scenario(steps=[Step()]), lookup=lambda _: None, send=Recorder([])
    )
    assert result.steps[0].error == "No saved request is chosen for this step."


def test_a_transport_failure_fails_the_step_without_pretending_to_check_it():
    lookup = library(one=Request(url="http://x.test/1"))
    dead = Response(engine="httpx", error="Connection refused — is anything listening?")
    result = run_scenario(
        Scenario(steps=[Step(request_id=1, assertions=[Assertion(op="eq", value="200")])]),
        lookup=lookup,
        send=Recorder([dead]),
    )
    assert result.steps[0].outcome == "failed"
    assert result.steps[0].checks == []
    assert "Connection refused" in result.steps[0].error


def test_a_saved_request_with_no_url_is_caught_before_sending():
    lookup = library(one=Request(url=""))
    send = Recorder([])
    result = run_scenario(Scenario(steps=[Step(request_id=1)]), lookup=lookup, send=send)
    assert result.steps[0].outcome == "failed"
    assert result.steps[0].error == "The saved request has no URL."
    assert send.sent == []


def test_a_step_takes_its_name_from_the_saved_request_when_unnamed():
    lookup = library(**{"Log in": Request(url="http://x.test/")})
    result = run_scenario(
        Scenario(steps=[Step(request_id=1)]), lookup=lookup, send=Recorder([ok()])
    )
    assert result.steps[0].name == "Log in"


def test_a_scenario_that_ran_nothing_is_not_a_pass():
    """All-skipped must not read as green, or the suite quietly stops testing."""
    result = run_scenario(
        Scenario(steps=[Step(request_id=1, enabled=False)]),
        lookup=lambda _: None,
        send=Recorder([]),
    )
    assert result.count("skipped") == 1
    assert result.passed is False


def test_an_empty_scenario_is_not_a_pass():
    result = run_scenario(Scenario(), lookup=lambda _: None, send=Recorder([]))
    assert result.passed is False


def test_a_result_survives_a_json_round_trip():
    lookup = library(one=Request(url="http://x.test/1"))
    result = run_scenario(
        Scenario(
            name="Round trip",
            steps=[
                Step(
                    request_id=1,
                    assertions=[Assertion(op="eq", value="200")],
                    captures=[Capture(name="ok", source="json", path="ok")],
                )
            ],
        ),
        lookup=lookup,
        send=Recorder([ok()]),
    )
    assert ScenarioResult.from_dict(result.to_dict()) == result


def test_a_scenario_survives_a_json_round_trip():
    scenario = Scenario(
        name="Flow",
        description="why",
        folder="Auth",
        on_failure="continue",
        steps=[
            Step(
                name="Log in",
                request_id=3,
                assertions=[Assertion(kind="json", target="token", op="exists")],
                captures=[Capture(name="token", source="json", path="token")],
            )
        ],
    )
    assert Scenario.from_dict(scenario.to_dict()) == scenario


def test_an_unknown_on_failure_value_falls_back_to_stopping():
    assert Scenario.from_dict({"on_failure": "explode"}).on_failure == "stop"
