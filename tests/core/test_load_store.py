import pytest

from hitman.core.loadtest import LoadResult, LoadTest, TimeSlice
from hitman.core.store import LOAD_RUN_LIMIT, Store


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "test.db")
    yield store
    store.close()


def make_test(name="Place detail", folder=""):
    return LoadTest(
        name=name, folder=folder, target_kind="request", target_id=3, users=25, limit=60
    )


def test_a_load_test_round_trips(store):
    load_test_id = store.save_load_test(make_test())
    assert store.get_load_test(load_test_id).test == make_test()


def test_update_replaces_the_test(store):
    load_test_id = store.save_load_test(make_test())
    store.update_load_test(load_test_id, make_test(name="Renamed"))
    assert store.get_load_test(load_test_id).name == "Renamed"


def test_tests_group_by_folder_with_unfiled_last(store):
    store.save_load_test(make_test("b"))
    store.save_load_test(make_test("a", folder="Places"))
    groups = store.grouped_load_tests()
    assert [(folder, [item.name for item in items]) for folder, items in groups] == [
        ("Places", ["a"]),
        ("", ["b"]),
    ]
    assert store.list_load_test_folders() == ["Places"]


def test_duplicate_picks_a_free_name(store):
    load_test_id = store.save_load_test(make_test())
    store.duplicate_load_test(load_test_id)
    store.duplicate_load_test(load_test_id)
    names = sorted(item.name for item in store.list_load_tests())
    assert names == ["Place detail", "Place detail (copy 2)", "Place detail (copy)"]


def test_a_run_round_trips(store):
    result = LoadResult(
        name="Place detail",
        requests=120,
        failed=3,
        statuses=[("200", 117), ("500", 3)],
        timeline=[TimeSlice(start_s=0, requests=120, failed=3, mean_ms=8.5)],
    )
    run_id = store.add_load_run(None, result)
    assert store.get_load_run(run_id).result == result


def test_deleting_a_test_keeps_its_runs(store):
    load_test_id = store.save_load_test(make_test())
    run_id = store.add_load_run(load_test_id, LoadResult(name="Place detail"))
    store.delete_load_test(load_test_id)
    run = store.get_load_run(run_id)
    assert run is not None
    assert run.load_test_id is None


def test_runs_are_capped_and_listed_newest_first(store):
    for index in range(LOAD_RUN_LIMIT + 5):
        store.add_load_run(None, LoadResult(name=f"run {index}"))
    runs = store.list_load_runs(LOAD_RUN_LIMIT + 10)
    assert len(runs) == LOAD_RUN_LIMIT
    assert runs[0].result.name == f"run {LOAD_RUN_LIMIT + 4}"


def test_clear_removes_every_run(store):
    store.add_load_run(None, LoadResult(name="x"))
    store.clear_load_runs()
    assert store.list_load_runs() == []
