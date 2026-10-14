import pytest
from fastapi.testclient import TestClient

from undertow.web.app import app


def test_landing_renders():
    resp = TestClient(app).get("/")
    assert resp.status_code == 200
    assert 'name="viewport"' in resp.text
    assert "Fantasy hockey for people who love women's hockey" in resp.text
    assert 'name="email"' in resp.text
    # The unofficial marker is on every page.
    assert "Unofficial and unaffiliated" in resp.text


def test_landing_carries_utm_into_form():
    resp = TestClient(app).get("/?utm_source=tiktok&utm_campaign=launch")
    assert 'name="utm_source" value="tiktok"' in resp.text
    assert 'name="utm_campaign" value="launch"' in resp.text


@pytest.mark.parametrize("path", ["/thanks", "/privacy"])
def test_static_pages_have_unofficial_marker(path):
    resp = TestClient(app).get(path)
    assert resp.status_code == 200
    assert "Unofficial and unaffiliated" in resp.text


def test_static_assets_served():
    client = TestClient(app)
    assert client.get("/static/tokens.css").status_code == 200
    assert client.get("/static/site.css").status_code == 200


@pytest.mark.db
def test_healthz_ok():
    resp = TestClient(app).get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "db": True}
