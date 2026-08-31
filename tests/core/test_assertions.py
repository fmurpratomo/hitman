import pytest

from hitman.core.assertions import Assertion, check, run_checks
from hitman.core.models import Response

BODY = '{"token": "abc123", "user": {"id": 7, "admin": true}, "roles": ["admin", "dev"]}'


def make_response(**kwargs):
    defaults = {
        "engine": "httpx",
        "status": 200,
        "reason": "OK",
        "headers": [("Content-Type", "application/json"), ("X-Fixture", "hitman")],
        "body": BODY,
        "elapsed_ms": 42.5,
    }
    return Response(**{**defaults, **kwargs})


def ran(kind, target, op, value=""):
    return check(Assertion(kind=kind, target=target, op=op, value=value), make_response())


# --- status -------------------------------------------------------------


def test_status_matches_the_number_as_typed():
    """The expected value arrives as text and the actual one as an int."""
    assert ran("status", "", "eq", "200").passed


def test_status_mismatch_reports_what_arrived():
    result = check(Assertion(op="eq", value="200"), make_response(status=404))
    assert not result.passed
    assert result.detail == "404"
    assert result.label == "status eq 200"


def test_status_ranges_use_the_ordered_operators():
    assert ran("status", "", "lt", "300").passed
    assert ran("status", "", "gte", "200").passed
    assert not ran("status", "", "gte", "300").passed


def test_a_request_that_never_completed_has_no_status():
    failed = make_response(status=None, error="Connection refused")
    assert not check(Assertion(op="eq", value="200"), failed).passed
    assert check(Assertion(op="not_exists"), failed).passed


# --- json ---------------------------------------------------------------


def test_json_path_equality_across_types():
    assert ran("json", "token", "eq", "abc123").passed
    assert ran("json", "user.id", "eq", "7").passed
    assert ran("json", "user.admin", "eq", "true").passed
    assert ran("json", "roles.0", "eq", "admin").passed


def test_json_exists_and_missing():
    assert ran("json", "token", "exists").passed
    assert ran("json", "nope", "not_exists").passed
    assert not ran("json", "nope", "exists").passed


def test_a_missing_path_fails_every_comparison_with_a_readable_reason():
    result = ran("json", "user.email", "eq", "a@b.test")
    assert not result.passed
    assert result.detail == "not found"


def test_contains_looks_inside_a_list():
    assert ran("json", "roles", "contains", "dev").passed
    assert ran("json", "roles", "not_contains", "owner").passed


def test_a_body_that_is_not_json_says_so_rather_than_failing_silently():
    result = check(Assertion(kind="json", target="id", op="eq", value="1"),
                   make_response(body="<html>nope</html>"))
    assert not result.passed
    assert "not JSON" in result.detail


def test_comparing_a_string_field_with_an_ordered_operator_is_a_clear_failure():
    result = ran("json", "token", "gt", "5")
    assert not result.passed
    assert "not a number" in result.detail


# --- headers ------------------------------------------------------------


def test_header_lookup_ignores_case():
    assert ran("header", "content-type", "contains", "json").passed
    assert ran("header", "CONTENT-TYPE", "contains", "json").passed


def test_a_missing_header_does_not_exist():
    assert ran("header", "X-Nope", "not_exists").passed
    assert not ran("header", "X-Nope", "exists").passed


def test_a_header_check_without_a_name_is_rejected():
    result = ran("header", "", "exists")
    assert not result.passed
    assert "no header name" in result.detail


# --- body, timing and regex ---------------------------------------------


def test_body_contains_and_matches():
    assert ran("body", "", "contains", "abc123").passed
    assert ran("body", "", "matches", r'"token":\s*"\w+"').passed


def test_an_invalid_regular_expression_fails_the_check_rather_than_raising():
    result = ran("body", "", "matches", "(unclosed")
    assert not result.passed
    assert "invalid regular expression" in result.detail


def test_elapsed_time_is_checked_in_milliseconds():
    assert ran("time_ms", "", "lt", "1000").passed
    assert not ran("time_ms", "", "lt", "10").passed


def test_an_unknown_kind_fails_loudly():
    result = ran("wat", "", "eq", "1")
    assert not result.passed
    assert "unknown check" in result.detail


# --- the assertion list -------------------------------------------------


def test_a_disabled_assertion_is_not_a_check_at_all():
    checks = run_checks(
        [
            Assertion(kind="status", op="eq", value="200"),
            Assertion(kind="status", op="eq", value="500", enabled=False),
        ],
        make_response(),
    )
    assert len(checks) == 1
    assert checks[0].passed


@pytest.mark.parametrize(
    "assertion,expected",
    [
        (Assertion(kind="status", op="eq", value="200"), "status eq 200"),
        (Assertion(kind="json", target="user.id", op="exists"), "json user.id exists"),
        (Assertion(kind="header", target="ETag", op="not_exists"), "header ETag not exists"),
        (Assertion(kind="body", op="not_contains", value="error"), "body not contains error"),
    ],
)
def test_describe_reads_as_a_sentence(assertion, expected):
    assert assertion.describe() == expected
