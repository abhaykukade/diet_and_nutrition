from fastapi.testclient import TestClient

from nutrition.main import create_app


def test_healthz():
    client = TestClient(create_app())
    assert client.get("/healthz").json() == {"status": "ok"}
