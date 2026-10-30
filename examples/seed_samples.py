"""Add sample requests, scenarios and performance tests to Hitman.

    python3 examples/seed_samples.py                    # local set, into data/hitman.db
    python3 examples/seed_samples.py --set real-world   # public APIs on the internet
    python3 examples/seed_samples.py --db other.db --base-url http://localhost:9100

Two sets:

``local`` (default)
    Requests for examples/sample_api.py, in a "Sample" folder in every tab.
    Put as much load on it as you like: it runs on your machine.

``real-world``
    Real public APIs, one Saved folder per API, plus a "Real world" folder in
    Tests and Performance. Their performance tests are deliberately gentle.
    Only QuickPizza gets real load: Grafana runs it as a shared target for
    practising load tests. The others get latency samples of 10-20 requests,
    which is ordinary use. Do not raise those into load tests: these services
    are free and shared, and hammering one can get your IP blocked.

Only what is missing is added: running a set twice adds nothing, and an item
you have since edited is left as it is. The app can stay running; reload the
page to see the new entries.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path
from typing import Callable

# Run from a checkout with nothing installed, like `python3 -m hitman`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hitman.core.assertions import Assertion  # noqa: E402
from hitman.core.loadtest import LoadTest  # noqa: E402
from hitman.core.models import KeyValue, Request  # noqa: E402
from hitman.core.scenarios import Capture, Scenario, Step  # noqa: E402
from hitman.core.store import Store  # noqa: E402

# (folder, name) -> id of what is stored under it.
Lookup = Callable[[str, str], int]


def _json(data: dict) -> str:
    return json.dumps(data, indent=2)


def _status(code: int) -> Assertion:
    return Assertion(kind="status", op="eq", value=str(code))


def _test(name, folder, users, limit, stop_after="seconds", ramp_up_s=0, **target):
    return LoadTest(
        name=name,
        folder=folder,
        users=users,
        ramp_up_s=ramp_up_s,
        stop_after=stop_after,
        limit=limit,
        **target,
    )


# --- local: examples/sample_api.py --------------------------------------


def local_set(base: str):
    folder = "Sample"
    requests = [
        (folder, "Health", Request(url=f"{base}/health")),
        (
            folder,
            "List places",
            Request(url=f"{base}/places", params=[KeyValue("page", "1"), KeyValue("limit", "3")]),
        ),
        (folder, "Place detail", Request(url=f"{base}/places/1")),
        (folder, "Place not found", Request(url=f"{base}/places/999")),
        (
            folder,
            "Create place",
            Request(
                method="POST",
                url=f"{base}/places",
                body_type="json",
                body=_json({"name": "Kota Tua", "city": "Jakarta"}),
            ),
        ),
        (
            folder,
            "Login",
            Request(
                method="POST",
                url=f"{base}/login",
                body_type="json",
                body=_json({"username": "demo", "password": "demo"}),
            ),
        ),
        # {{token}} comes from the Login step of the scenario; sent on its own
        # from the builder it is a 401, which is the point of the scenario.
        (
            folder,
            "My profile",
            Request(url=f"{base}/me", headers=[KeyValue("Authorization", "Bearer {{token}}")]),
        ),
        (folder, "Slow", Request(url=f"{base}/slow", params=[KeyValue("ms", "300")])),
        (folder, "Flaky", Request(url=f"{base}/flaky", params=[KeyValue("fail", "10")])),
    ]

    def scenarios(req: Lookup) -> list[Scenario]:
        return [
            Scenario(
                name="Login flow",
                description="Log in, then fetch the profile with the token",
                folder=folder,
                steps=[
                    Step(
                        request_id=req(folder, "Login"),
                        assertions=[_status(200)],
                        captures=[Capture(name="token", source="json", path="token")],
                    ),
                    Step(
                        request_id=req(folder, "My profile"),
                        assertions=[
                            _status(200),
                            Assertion(kind="json", target="user.name", op="eq", value="demo"),
                        ],
                    ),
                ],
            )
        ]

    def load_tests(req: Lookup, scn: Lookup) -> list[LoadTest]:
        return [
            _test("Health baseline", folder, 10, 20, target_id=req(folder, "Health")),
            _test(
                "Place detail at 50 users",
                folder,
                50,
                30,
                ramp_up_s=5,
                target_id=req(folder, "Place detail"),
            ),
            _test("Slow endpoint", folder, 20, 15, target_id=req(folder, "Slow")),
            _test(
                "Flaky 10%", folder, 20, 1000, stop_after="iterations", target_id=req(folder, "Flaky")
            ),
            _test(
                "Login flow under load",
                folder,
                10,
                500,
                stop_after="iterations",
                target_kind="scenario",
                target_id=scn(folder, "Login flow"),
            ),
        ]

    return requests, scenarios, load_tests


# --- real-world: public APIs --------------------------------------------

JSONPLACEHOLDER = "https://jsonplaceholder.typicode.com"
DUMMYJSON = "https://dummyjson.com"
HTTPBIN = "https://httpbin.org"
OPEN_METEO = "https://api.open-meteo.com"
QUICKPIZZA = "https://quickpizza.grafana.com"

# QuickPizza's published demo token: the same one its login hands the default
# user, so the request works straight from Saved with no login step.
QUICKPIZZA_TOKEN = "abcdef0123456789"
PIZZA_RULES = {
    "maxCaloriesPerSlice": 1000,
    "mustBeVegetarian": False,
    "excludedIngredients": ["pepperoni"],
    "excludedTools": [],
    "maxNumberOfToppings": 5,
    "minNumberOfToppings": 2,
}


def real_world_set():
    shared = "Real world"  # Tests and Performance folder
    basic = base64.b64encode(b"demo:secret").decode()
    requests = [
        (
            "JSONPlaceholder",
            "List posts",
            Request(url=f"{JSONPLACEHOLDER}/posts", params=[KeyValue("userId", "1")]),
        ),
        ("JSONPlaceholder", "Get post", Request(url=f"{JSONPLACEHOLDER}/posts/1")),
        (
            "JSONPlaceholder",
            "Create post",
            Request(
                method="POST",
                url=f"{JSONPLACEHOLDER}/posts",
                body_type="json",
                body=_json({"title": "Hello from Hitman", "body": "Testing a real API", "userId": 1}),
            ),
        ),
        (
            "DummyJSON",
            "Login",
            Request(
                method="POST",
                url=f"{DUMMYJSON}/auth/login",
                body_type="json",
                body=_json({"username": "emilys", "password": "emilyspass", "expiresInMins": 30}),
            ),
        ),
        # Needs the token from Login: run the "DummyJSON login flow" test.
        (
            "DummyJSON",
            "My profile",
            Request(
                url=f"{DUMMYJSON}/auth/me",
                headers=[KeyValue("Authorization", "Bearer {{token}}")],
            ),
        ),
        (
            "DummyJSON",
            "Search products",
            Request(
                url=f"{DUMMYJSON}/products/search",
                params=[KeyValue("q", "phone"), KeyValue("limit", "5")],
            ),
        ),
        ("httpbin", "Inspect headers", Request(url=f"{HTTPBIN}/headers")),
        (
            "httpbin",
            "Basic auth",
            Request(
                url=f"{HTTPBIN}/basic-auth/demo/secret",
                headers=[KeyValue("Authorization", f"Basic {basic}")],
            ),
        ),
        (
            "Open-Meteo",
            "Jakarta weather",
            Request(
                url=f"{OPEN_METEO}/v1/forecast",
                params=[
                    KeyValue("latitude", "-6.2"),
                    KeyValue("longitude", "106.85"),
                    KeyValue("current", "temperature_2m,relative_humidity_2m,wind_speed_10m"),
                    KeyValue("timezone", "Asia/Jakarta"),
                ],
            ),
        ),
        ("QuickPizza", "Quotes", Request(url=f"{QUICKPIZZA}/api/quotes")),
        (
            "QuickPizza",
            "Recommend pizza",
            Request(
                method="POST",
                url=f"{QUICKPIZZA}/api/pizza",
                headers=[KeyValue("Authorization", f"token {QUICKPIZZA_TOKEN}")],
                body_type="json",
                body=_json(PIZZA_RULES),
            ),
        ),
    ]

    def scenarios(req: Lookup) -> list[Scenario]:
        return [
            Scenario(
                name="DummyJSON login flow",
                description="Log in, then fetch the profile with the access token",
                folder=shared,
                steps=[
                    Step(
                        request_id=req("DummyJSON", "Login"),
                        assertions=[_status(200)],
                        captures=[Capture(name="token", source="json", path="accessToken")],
                    ),
                    Step(
                        request_id=req("DummyJSON", "My profile"),
                        assertions=[
                            _status(200),
                            Assertion(kind="json", target="username", op="eq", value="emilys"),
                        ],
                    ),
                ],
            ),
            Scenario(
                name="QuickPizza smoke",
                description="Quotes load, and a pizza can be recommended",
                folder=shared,
                steps=[
                    Step(
                        request_id=req("QuickPizza", "Quotes"),
                        assertions=[
                            _status(200),
                            Assertion(kind="json", target="quotes", op="exists"),
                        ],
                    ),
                    Step(
                        request_id=req("QuickPizza", "Recommend pizza"),
                        assertions=[
                            _status(200),
                            Assertion(kind="json", target="pizza.name", op="exists"),
                        ],
                    ),
                ],
            ),
        ]

    def load_tests(req: Lookup, scn: Lookup) -> list[LoadTest]:
        return [
            # Real load, on the one target here that is run for it.
            _test(
                "QuickPizza quotes, 10 users",
                shared,
                10,
                30,
                ramp_up_s=10,
                target_id=req("QuickPizza", "Quotes"),
            ),
            _test(
                "QuickPizza recommend, 5 users",
                shared,
                5,
                30,
                ramp_up_s=5,
                target_id=req("QuickPizza", "Recommend pizza"),
            ),
            _test(
                "QuickPizza smoke flow, 5 users",
                shared,
                5,
                100,
                stop_after="iterations",
                target_kind="scenario",
                target_id=scn(shared, "QuickPizza smoke"),
            ),
            # Latency samples: how fast a real API answers from here.
            _test(
                "DummyJSON latency, 20 requests",
                shared,
                2,
                20,
                stop_after="iterations",
                target_id=req("DummyJSON", "Search products"),
            ),
            _test(
                "DummyJSON login flow, 10 passes",
                shared,
                1,
                10,
                stop_after="iterations",
                target_kind="scenario",
                target_id=scn(shared, "DummyJSON login flow"),
            ),
            _test(
                "Open-Meteo latency, 10 requests",
                shared,
                1,
                10,
                stop_after="iterations",
                target_id=req("Open-Meteo", "Jakarta weather"),
            ),
        ]

    return requests, scenarios, load_tests


# --- seeding ------------------------------------------------------------


def seed(store: Store, requests, scenarios, load_tests) -> list[str]:
    """Add whatever is missing. Returns one line per item, saying which."""
    report = []

    saved = {(item.folder, item.name): item.id for item in store.list_requests()}
    for folder, name, request in requests:
        if (folder, name) in saved:
            report.append(f"  Saved        {folder} / {name}  (already there)")
        else:
            saved[(folder, name)] = store.save_request(name, request, folder)
            report.append(f"+ Saved        {folder} / {name}")

    stored = {(item.folder, item.name): item.id for item in store.list_scenarios()}
    for scenario in scenarios(lambda folder, name: saved[(folder, name)]):
        key = (scenario.folder, scenario.name)
        if key in stored:
            report.append(f"  Tests        {scenario.folder} / {scenario.name}  (already there)")
        else:
            stored[key] = store.save_scenario(scenario)
            report.append(f"+ Tests        {scenario.folder} / {scenario.name}")

    tests = {(item.folder, item.name) for item in store.list_load_tests()}
    for test in load_tests(
        lambda folder, name: saved[(folder, name)], lambda folder, name: stored[(folder, name)]
    ):
        if (test.folder, test.name) in tests:
            report.append(f"  Performance  {test.folder} / {test.name}  (already there)")
        else:
            store.save_load_test(test)
            report.append(f"+ Performance  {test.folder} / {test.name}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Add sample requests, tests and load tests.")
    parser.add_argument("--set", choices=("local", "real-world"), default="local")
    parser.add_argument(
        "--db",
        default=os.environ.get("HITMAN_DB", "data/hitman.db"),
        help="SQLite file (default: data/hitman.db, as the app uses)",
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:9090",
        help="where examples/sample_api.py runs (local set only)",
    )
    args = parser.parse_args()

    if args.set == "local":
        parts = local_set(args.base_url.rstrip("/"))
        hint = "Start the API with: python3 examples/sample_api.py"
    else:
        parts = real_world_set()
        hint = "These call public APIs, so you need an internet connection."

    store = Store(args.db)
    try:
        lines = seed(store, *parts)
    finally:
        store.close()
    print(f"Database: {Path(args.db).resolve()}")
    print("\n".join(lines))
    print(f"\nReload Hitman in the browser. {hint}")


if __name__ == "__main__":
    main()
