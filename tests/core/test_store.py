import pytest

from hitman.core.models import KeyValue, Request, Response
from hitman.core.store import HISTORY_LIMIT, STORED_BODY_LIMIT, Store


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "test.db")
    yield store
    store.close()


def make_request(url="http://localhost:3000/api"):
    return Request(method="POST", url=url, body_type="json", body='{"a": 1}')


def make_response(**kwargs):
    defaults = {
        "engine": "httpx", "status": 200, "reason": "OK",
        "headers": [("Content-Type", "application/json")],
        "body": '{"ok": true}', "size_bytes": 12, "elapsed_ms": 42.5,
    }
    return Response(**{**defaults, **kwargs})


def test_saved_request_survives_a_round_trip(store):
    saved_id = store.save_request("Create user", make_request())
    loaded = store.get_request(saved_id)
    assert loaded.name == "Create user"
    assert loaded.request.method == "POST"
    assert loaded.request.body == '{"a": 1}'


def test_get_request_returns_none_for_unknown_id(store):
    assert store.get_request(9999) is None


def test_list_requests_is_newest_first(store):
    store.save_request("first", make_request())
    store.save_request("second", make_request())
    assert [item.name for item in store.list_requests()] == ["second", "first"]


def test_update_request_changes_name_and_payload(store):
    saved_id = store.save_request("old", make_request())
    store.update_request(saved_id, "new", Request(method="GET", url="http://x.test/"))
    loaded = store.get_request(saved_id)
    assert loaded.name == "new"
    assert loaded.request.method == "GET"


def test_delete_request_removes_it(store):
    saved_id = store.save_request("gone", make_request())
    store.delete_request(saved_id)
    assert store.get_request(saved_id) is None


def test_saved_requests_are_normalised(store):
    saved_id = store.save_request("q", Request(url="http://x.test/api?page=2"))
    loaded = store.get_request(saved_id)
    assert loaded.request.url == "http://x.test/api"
    assert [(kv.key, kv.value) for kv in loaded.request.params] == [("page", "2")]


def test_history_round_trip_preserves_response_headers_as_tuples(store):
    entry_id = store.add_history(make_request(), make_response())
    entry = store.get_history(entry_id)
    assert entry.response.headers == [("Content-Type", "application/json")]
    assert entry.response.status == 200
    assert entry.response.elapsed_ms == 42.5


def test_failed_sends_are_recorded_too(store):
    store.add_history(
        make_request(),
        Response(engine="curl", status=None, error="Connection refused", curl_exit_code=7),
    )
    entry = store.list_history()[0]
    assert entry.response.error == "Connection refused"
    assert entry.response.status is None
    assert entry.response.curl_exit_code == 7


def test_history_is_newest_first(store):
    store.add_history(Request(url="http://a.test/"), make_response())
    store.add_history(Request(url="http://b.test/"), make_response())
    assert store.list_history()[0].request.url == "http://b.test/"


def test_history_is_trimmed_to_the_limit(store):
    for index in range(HISTORY_LIMIT + 10):
        store.add_history(Request(url=f"http://x.test/{index}"), make_response())
    assert len(store.list_history(limit=10_000)) == HISTORY_LIMIT
    assert store.list_history(limit=1)[0].request.url.endswith(str(HISTORY_LIMIT + 9))


def test_stored_response_body_is_capped(store):
    entry_id = store.add_history(make_request(), make_response(body="x" * (STORED_BODY_LIMIT + 500)))
    assert len(store.get_history(entry_id).response.body) == STORED_BODY_LIMIT


def test_clear_history_empties_it(store):
    store.add_history(make_request(), make_response())
    store.clear_history()
    assert store.list_history() == []


def test_store_creates_missing_parent_directory(tmp_path):
    store = Store(tmp_path / "nested" / "deeper" / "hitman.db")
    store.save_request("x", make_request())
    store.close()
    assert (tmp_path / "nested" / "deeper" / "hitman.db").exists()


# --- environments -------------------------------------------------------


def make_vars():
    return [KeyValue("base_url", "http://localhost:3000"), KeyValue("token", "abc")]


def test_environment_round_trip(store):
    env_id = store.save_environment("Local", make_vars())
    env = store.get_environment(env_id)
    assert env.name == "Local"
    assert [(v.key, v.value) for v in env.variables] == [
        ("base_url", "http://localhost:3000"),
        ("token", "abc"),
    ]


def test_environments_are_listed_alphabetically(store):
    store.save_environment("Staging", [])
    store.save_environment("Local", [])
    assert [e.name for e in store.list_environments()] == ["Local", "Staging"]


def test_as_mapping_skips_disabled_variables(store):
    env_id = store.save_environment(
        "Local", [KeyValue("a", "1"), KeyValue("b", "2", enabled=False)]
    )
    assert store.get_environment(env_id).as_mapping() == {"a": "1"}


def test_update_environment_replaces_name_and_variables(store):
    env_id = store.save_environment("Local", make_vars())
    store.update_environment(env_id, "Prod", [KeyValue("base_url", "https://api.live")])
    env = store.get_environment(env_id)
    assert env.name == "Prod"
    assert env.as_mapping() == {"base_url": "https://api.live"}


def test_no_active_environment_by_default(store):
    assert store.active_environment() is None


def test_active_environment_is_remembered(store):
    env_id = store.save_environment("Local", make_vars())
    store.set_active_environment(env_id)
    assert store.active_environment().name == "Local"


def test_setting_active_twice_does_not_duplicate(store):
    first = store.save_environment("Local", [])
    second = store.save_environment("Prod", [])
    store.set_active_environment(first)
    store.set_active_environment(second)
    assert store.active_environment().name == "Prod"


def test_active_can_be_cleared(store):
    store.set_active_environment(store.save_environment("Local", []))
    store.set_active_environment(None)
    assert store.active_environment() is None


def test_deleting_the_active_environment_clears_the_pointer(store):
    """Otherwise the setting dangles and every later send explodes."""
    env_id = store.save_environment("Local", make_vars())
    store.set_active_environment(env_id)
    store.delete_environment(env_id)
    assert store.get_environment(env_id) is None
    assert store.active_environment() is None


def test_deleting_a_different_environment_leaves_the_pointer(store):
    keep = store.save_environment("Local", [])
    other = store.save_environment("Prod", [])
    store.set_active_environment(keep)
    store.delete_environment(other)
    assert store.active_environment().id == keep
