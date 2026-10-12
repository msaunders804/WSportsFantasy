import pytest
from fastapi.testclient import TestClient

from undertow.web.app import app


def test_index_renders():
    resp = TestClient(app).get("/")
    assert resp.status_code == 200
    assert "Undertow Sports" in resp.text
    assert 'name="viewport"' in resp.text


@pytest.mark.db
def test_healthz_ok():
    resp = TestClient(app).get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "db": True}
