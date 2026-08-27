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


def test_list_requests_is_alphabetical(store):
    """Ordering changed when folders arrived: name order beats insertion order,
    because a list you have organised into folders has to stay navigable.
    History is unaffected and stays newest-first."""
    store.save_request("zebra", make_request())
    store.save_request("alpha", make_request())
    assert [item.name for item in store.list_requests()] == ["alpha", "zebra"]


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


# --- folders and duplication --------------------------------------------


def test_a_saved_request_defaults_to_no_folder(store):
    saved_id = store.save_request("x", make_request())
    assert store.get_request(saved_id).folder == ""


def test_a_request_can_be_saved_into_a_folder(store):
    saved_id = store.save_request("x", make_request(), folder="Users")
    assert store.get_request(saved_id).folder == "Users"


def test_folder_names_are_trimmed(store):
    saved_id = store.save_request("x", make_request(), folder="  Users  ")
    assert store.get_request(saved_id).folder == "Users"


def test_list_folders_is_distinct_and_sorted_and_skips_unfiled(store):
    store.save_request("a", make_request(), folder="Users")
    store.save_request("b", make_request(), folder="Auth")
    store.save_request("c", make_request(), folder="Users")
    store.save_request("d", make_request())
    assert store.list_folders() == ["Auth", "Users"]


def test_grouped_puts_folders_first_and_unfiled_last(store):
    store.save_request("loose", make_request())
    store.save_request("filed", make_request(), folder="Users")
    assert [folder for folder, _ in store.grouped_requests()] == ["Users", ""]


def test_grouped_sorts_items_by_name_within_a_folder(store):
    store.save_request("zebra", make_request(), folder="Users")
    store.save_request("alpha", make_request(), folder="Users")
    _, items = store.grouped_requests()[0]
    assert [i.name for i in items] == ["alpha", "zebra"]


def test_update_can_move_a_request_between_folders(store):
    saved_id = store.save_request("x", make_request(), folder="Users")
    store.update_request(saved_id, "x", make_request(), folder="Auth")
    assert store.get_request(saved_id).folder == "Auth"


def test_duplicate_copies_the_request_and_the_folder(store):
    original = store.save_request("Get users", make_request(), folder="Users")
    copy_id = store.duplicate_request(original)
    copy = store.get_request(copy_id)
    assert copy.name == "Get users (copy)"
    assert copy.folder == "Users"
    assert copy.request == store.get_request(original).request


def test_duplicating_twice_does_not_collide(store):
    original = store.save_request("Get users", make_request())
    store.duplicate_request(original)
    second = store.duplicate_request(original)
    assert store.get_request(second).name == "Get users (copy 2)"


def test_duplicate_names_only_collide_within_a_folder(store):
    store.save_request("Get users (copy)", make_request(), folder="Other")
    original = store.save_request("Get users", make_request(), folder="Users")
    copy_id = store.duplicate_request(original)
    assert store.get_request(copy_id).name == "Get users (copy)"


def test_duplicating_something_that_does_not_exist_returns_none(store):
    assert store.duplicate_request(9999) is None


def test_a_database_without_the_folder_column_is_migrated(tmp_path):
    """Someone already using the app must not lose their saved requests."""
    import sqlite3

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(
        """
        CREATE TABLE saved_requests (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL,
          request_json TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        """
    )
    old.execute(
        "INSERT INTO saved_requests (name, request_json, created_at, updated_at)"
        " VALUES ('legacy', '{\"url\": \"http://x.test/\"}', 'then', 'then')"
    )
    old.commit()
    old.close()

    store = Store(path)
    try:
        item = store.list_requests()[0]
        assert item.name == "legacy"
        assert item.folder == ""
        assert item.request.url == "http://x.test/"
    finally:
        store.close()
