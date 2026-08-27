import json

import pytest

from hitman.core.engines.curl_engine import CurlEngine
from hitman.core.engines.httpx_engine import HttpxEngine
from hitman.core.models import KeyValue, Request


@pytest.fixture(params=["httpx", "curl"])
def engine(request):
    return HttpxEngine() if request.param == "httpx" else CurlEngine()


def test_same_status_for_the_same_request(engine, fixture_server):
    assert engine.send(Request(url=f"{fixture_server}/status/404")).status == 404


def test_same_body_for_the_same_request(engine, fixture_server):
    assert engine.send(Request(url=f"{fixture_server}/json")).body == '{"hello": "world"}'


def test_same_content_type_sent_for_a_json_body(engine, fixture_server):
    response = engine.send(
        Request(method="POST", url=f"{fixture_server}/echo", body_type="json", body="{}")
    )
    assert json.loads(response.body)["content_type"] == "application/json"


def test_neither_engine_sends_a_content_type_for_a_raw_body(engine, fixture_server):
    """curl adds urlencoded to --data by default; the engine must suppress it."""
    response = engine.send(
        Request(method="POST", url=f"{fixture_server}/echo", body_type="raw", body="<xml/>")
    )
    assert json.loads(response.body)["content_type"] is None


def test_same_form_encoding(engine, fixture_server):
    response = engine.send(
        Request(
            method="POST", url=f"{fixture_server}/echo", body_type="form",
            form_fields=[KeyValue("a", "1"), KeyValue("b", "x y")],
        )
    )
    echoed = json.loads(response.body)
    assert echoed["body"] == "a=1&b=x+y"
    assert echoed["content_type"] == "application/x-www-form-urlencoded"


def test_same_params_in_the_url(engine, fixture_server):
    response = engine.send(Request(url=f"{fixture_server}/echo", params=[KeyValue("page", "2")]))
    assert json.loads(response.body)["path"] == "/echo?page=2"


def test_both_report_failure_without_raising(engine, closed_port):
    response = engine.send(Request(url=f"http://127.0.0.1:{closed_port}/"))
    assert response.status is None
    assert "Connection refused" in response.error
