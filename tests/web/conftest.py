import pytest
from fastapi.testclient import TestClient

from hitman.web.app import create_app


@pytest.fixture
def app(tmp_path):
    return create_app(tmp_path / "test.db")


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        yield client


@pytest.fixture
def base_form():
    """The fields the request form always submits."""
    return {
        "method": "GET",
        "url": "",
        "body_type": "none",
        "body": "",
        "timeout": "30",
        "follow_redirects": "1",
        "verify_tls": "1",
        "engine": "httpx",
    }
