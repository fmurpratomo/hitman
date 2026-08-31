import pytest

from hitman.core.jsonpath import MISSING, extract, render, segments

DOC = {
    "token": "abc123",
    "user": {"id": 7, "name": "Ada", "admin": True, "manager": None},
    "roles": ["admin", "dev"],
    "items": [{"id": 1}, {"id": 2}],
    "count": 0,
}


@pytest.mark.parametrize(
    "path,expected",
    [
        ("token", "abc123"),
        ("user.id", 7),
        ("user.admin", True),
        ("user.manager", None),
        ("roles.0", "admin"),
        ("roles[1]", "dev"),
        ("items.1.id", 2),
        ("items[0].id", 1),
        ("$.user.name", "Ada"),
        ("$user.name", "Ada"),
        ("count", 0),
    ],
)
def test_paths_people_actually_type(path, expected):
    assert extract(DOC, path) == expected


def test_an_empty_path_is_the_whole_document():
    assert extract(DOC, "") is DOC
    assert extract(DOC, "$") is DOC


@pytest.mark.parametrize(
    "path", ["nope", "user.nope", "roles.5", "roles.-9", "roles.name", "token.length"]
)
def test_a_path_that_leads_nowhere_is_missing(path):
    assert extract(DOC, path) is MISSING


def test_a_null_value_is_not_missing():
    """`exists` has to tell a key holding null apart from an absent key."""
    assert extract(DOC, "user.manager") is None
    assert extract(DOC, "user.manager") is not MISSING


def test_negative_indices_reach_from_the_end():
    assert extract(DOC, "roles.-1") == "dev"


def test_segments_normalises_both_index_spellings():
    assert segments("items[0].id") == ["items", "0", "id"]
    assert segments("  $.items.0.id  ") == ["items", "0", "id"]


def test_render_leaves_strings_unquoted():
    """A captured token has to go into the next request as abc123, not "abc123"."""
    assert render("abc123") == "abc123"


@pytest.mark.parametrize(
    "value,expected",
    [(7, "7"), (True, "true"), (None, "null"), ({"a": 1}, '{"a": 1}'), ([1, 2], "[1, 2]")],
)
def test_render_writes_everything_else_as_json(value, expected):
    assert render(value) == expected


def test_render_of_missing_is_empty():
    assert render(MISSING) == ""


def test_missing_is_falsy_and_prints_readably():
    assert not MISSING
    assert repr(MISSING) == "<missing>"
