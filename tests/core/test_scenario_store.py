import pytest

from hitman.core.assertions import Assertion
from hitman.core.models import Request, Response
from hitman.core.scenarios import Capture, Scenario, ScenarioResult, Step, StepResult
from hitman.core.store import RUN_BODY_LIMIT, SCENARIO_RUN_LIMIT, Store


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "test.db")
    yield store
    store.close()


def make_scenario(name="Login flow"):
    return Scenario(
        name=name,
        description="proves the token round trip",
        on_failure="continue",
        steps=[
            Step(
                name="Log in",
                request_id=1,
                assertions=[Assertion(kind="json", target="token", op="exists")],
                captures=[Capture(name="token", source="json", path="token")],
            ),
            Step(name="Fetch profile", request_id=2),
        ],
    )


def make_result(body='{"ok": true}', outcome="passed"):
    return ScenarioResult(
        name="Login flow",
        engine="httpx",
        environment="Local",
        elapsed_ms=120.0,
        steps=[
            StepResult(
                name="Log in",
                request=Request(url="http://x.test/login"),
                response=Response(engine="httpx", status=200, body=body),
                outcome=outcome,
            )
        ],
    )


def test_a_scenario_survives_a_round_trip(store):
    scenario_id = store.save_scenario(make_scenario())
    loaded = store.get_scenario(scenario_id)
    assert loaded.scenario == make_scenario()
    assert loaded.name == "Login flow"


def test_update_replaces_it(store):
    scenario_id = store.save_scenario(make_scenario())
    store.update_scenario(scenario_id, Scenario(name="Renamed"))
    assert store.get_scenario(scenario_id).scenario.name == "Renamed"
    assert store.get_scenario(scenario_id).scenario.steps == []


def test_delete_removes_it(store):
    scenario_id = store.save_scenario(make_scenario())
    store.delete_scenario(scenario_id)
    assert store.get_scenario(scenario_id) is None
    assert store.list_scenarios() == []


def test_an_unknown_scenario_is_none(store):
    assert store.get_scenario(9999) is None


def test_duplicate_finds_a_free_name(store):
    scenario_id = store.save_scenario(make_scenario())
    store.duplicate_scenario(scenario_id)
    store.duplicate_scenario(scenario_id)
    assert {item.name for item in store.list_scenarios()} == {
        "Login flow",
        "Login flow (copy)",
        "Login flow (copy 2)",
    }


def test_duplicating_an_unknown_scenario_returns_none(store):
    assert store.duplicate_scenario(9999) is None


def test_a_run_survives_a_round_trip(store):
    scenario_id = store.save_scenario(make_scenario())
    run_id = store.add_scenario_run(scenario_id, make_result())
    loaded = store.get_scenario_run(run_id)
    assert loaded.scenario_id == scenario_id
    assert loaded.result == make_result()
    assert loaded.passed is True


def test_deleting_a_scenario_keeps_its_runs(store):
    """A run is a record of what happened; deleting the scenario cannot unhappen it."""
    scenario_id = store.save_scenario(make_scenario())
    run_id = store.add_scenario_run(scenario_id, make_result())
    store.delete_scenario(scenario_id)
    run = store.get_scenario_run(run_id)
    assert run is not None
    assert run.scenario_id is None
    assert run.result.name == "Login flow"


def test_a_run_can_belong_to_no_scenario_at_all(store):
    """Running an unsaved scenario from the editor still records the result."""
    run_id = store.add_scenario_run(None, make_result())
    assert store.get_scenario_run(run_id).scenario_id is None


def test_a_long_response_body_is_capped_in_storage(store):
    run_id = store.add_scenario_run(None, make_result(body="x" * (RUN_BODY_LIMIT + 5000)))
    stored = store.get_scenario_run(run_id).result.steps[0].response
    assert len(stored.body) == RUN_BODY_LIMIT
    assert stored.body_truncated is True


def test_capping_does_not_mutate_the_result_the_caller_still_holds(store):
    result = make_result(body="x" * (RUN_BODY_LIMIT + 5000))
    store.add_scenario_run(None, result)
    assert len(result.steps[0].response.body) == RUN_BODY_LIMIT + 5000


def test_runs_are_trimmed_to_the_limit(store):
    for _ in range(SCENARIO_RUN_LIMIT + 5):
        store.add_scenario_run(None, make_result())
    assert len(store.list_scenario_runs(limit=1000)) == SCENARIO_RUN_LIMIT


def test_runs_come_back_newest_first(store):
    store.add_scenario_run(None, ScenarioResult(name="first"))
    store.add_scenario_run(None, ScenarioResult(name="second"))
    assert [run.result.name for run in store.list_scenario_runs()] == ["second", "first"]


def test_clear_runs_empties_the_list(store):
    store.add_scenario_run(None, make_result())
    store.clear_scenario_runs()
    assert store.list_scenario_runs() == []


def test_a_failed_run_is_recorded_as_failed(store):
    run_id = store.add_scenario_run(None, make_result(outcome="failed"))
    assert store.get_scenario_run(run_id).passed is False


# --- folders ------------------------------------------------------------


def test_a_scenario_defaults_to_no_folder(store):
    assert store.get_scenario(store.save_scenario(Scenario(name="x"))).folder == ""


def test_a_scenario_can_be_saved_into_a_folder(store):
    scenario_id = store.save_scenario(Scenario(name="x", folder="Checkout"))
    assert store.get_scenario(scenario_id).folder == "Checkout"


def test_folder_names_are_trimmed(store):
    scenario_id = store.save_scenario(Scenario(name="x", folder="  Checkout  "))
    saved = store.get_scenario(scenario_id)
    assert saved.folder == "Checkout"
    # Trimmed in the JSON too, not just the column the ordering reads.
    assert saved.scenario.folder == "Checkout"


def test_update_can_move_a_scenario_between_folders(store):
    scenario_id = store.save_scenario(Scenario(name="x", folder="Checkout"))
    store.update_scenario(scenario_id, Scenario(name="x", folder="Auth"))
    assert store.get_scenario(scenario_id).folder == "Auth"


def test_update_can_take_a_scenario_out_of_its_folder(store):
    scenario_id = store.save_scenario(Scenario(name="x", folder="Checkout"))
    store.update_scenario(scenario_id, Scenario(name="x"))
    assert store.get_scenario(scenario_id).folder == ""


def test_list_scenario_folders_is_distinct_and_sorted_and_skips_unfiled(store):
    store.save_scenario(Scenario(name="a", folder="Checkout"))
    store.save_scenario(Scenario(name="b", folder="Auth"))
    store.save_scenario(Scenario(name="c", folder="Checkout"))
    store.save_scenario(Scenario(name="d"))
    assert store.list_scenario_folders() == ["Auth", "Checkout"]


def test_grouped_puts_folders_first_and_unfiled_last(store):
    store.save_scenario(Scenario(name="loose"))
    store.save_scenario(Scenario(name="filed", folder="Checkout"))
    assert [folder for folder, _ in store.grouped_scenarios()] == ["Checkout", ""]


def test_grouped_sorts_scenarios_by_name_within_a_folder(store):
    store.save_scenario(Scenario(name="zebra", folder="Checkout"))
    store.save_scenario(Scenario(name="apple", folder="Checkout"))
    _, items = store.grouped_scenarios()[0]
    assert [item.name for item in items] == ["apple", "zebra"]


def test_duplicate_lands_in_the_same_folder(store):
    scenario_id = store.save_scenario(Scenario(name="Login", folder="Auth"))
    copy = store.get_scenario(store.duplicate_scenario(scenario_id))
    assert copy.folder == "Auth"
    assert copy.name == "Login (copy)"


def test_the_same_name_in_another_folder_does_not_force_a_suffix(store):
    """Folders are namespaces: two "Smoke" scenarios in different ones is fine."""
    store.save_scenario(Scenario(name="Smoke (copy)", folder="Other"))
    scenario_id = store.save_scenario(Scenario(name="Smoke", folder="Auth"))
    assert store.get_scenario(store.duplicate_scenario(scenario_id)).name == "Smoke (copy)"


def test_a_database_without_the_scenario_folder_column_is_migrated(tmp_path):
    """Anyone who built scenarios before folders existed must keep them."""
    import sqlite3

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(
        """
        CREATE TABLE scenarios (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL,
          scenario_json TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        """
    )
    old.execute(
        "INSERT INTO scenarios (name, scenario_json, created_at, updated_at)"
        " VALUES ('legacy', '{\"name\": \"legacy\"}', 'then', 'then')"
    )
    old.commit()
    old.close()

    store = Store(path)
    try:
        item = store.list_scenarios()[0]
        assert item.name == "legacy"
        assert item.folder == ""
    finally:
        store.close()

