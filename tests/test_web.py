import pytest
from fastapi.testclient import TestClient

from undertow.web.app import app


def test_landing_renders():
    resp = TestClient(app).get("/")
    assert resp.status_code == 200
    assert 'name="viewport"' in resp.text
    assert "Fantasy for people who love women's hockey" in resp.text
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


def test_asset_links_are_relative_so_https_proxies_cannot_break_them():
    # Behind Railway's TLS proxy an absolute url_for() link came out as http:// and was blocked
    # as mixed content, leaving the page unstyled.
    resp = TestClient(app, base_url="http://x.up.railway.app").get(
        "/", headers={"x-forwarded-proto": "https"}
    )
    assert 'href="/static/tokens.css"' in resp.text
    assert 'href="/static/site.css"' in resp.text
    assert "http://x.up.railway.app" not in resp.text


def test_static_assets_served():
    client = TestClient(app)
    assert client.get("/static/tokens.css").status_code == 200
    assert client.get("/static/site.css").status_code == 200


@pytest.mark.db
def test_healthz_ok():
    resp = TestClient(app).get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "db": True}


@pytest.mark.parametrize("tab", ["matchup", "team", "players", "league"])
def test_preview_tabs_render_with_sample_banner_and_marker(tab):
    resp = TestClient(app).get(f"/preview/{tab}")
    assert resp.status_code == 200
    assert 'name="robots" content="noindex"' in resp.text
    assert "Sample data for design preview" in resp.text
    assert "Unofficial and unaffiliated" in resp.text
    # All four tabs are linked, with the current one marked.
    for other in ("matchup", "team", "players", "league"):
        assert f'href="/preview/{other}"' in resp.text
    assert f'href="/preview/{tab}" aria-current="page"' in resp.text


def test_preview_hidden_in_prod_unless_enabled(monkeypatch):
    from undertow.config import get_settings
    from undertow.web.app import create_app

    monkeypatch.setenv("ENVIRONMENT", "prod")
    get_settings.cache_clear()
    try:
        assert TestClient(create_app()).get("/preview/matchup").status_code == 404
        monkeypatch.setenv("PREVIEW_ENABLED", "true")
        get_settings.cache_clear()
        assert TestClient(create_app()).get("/preview/matchup").status_code == 200
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def test_preview_data_is_internally_consistent():
    from undertow.web import preview_data as d

    assert [slot for slot, _ in d.MY_LINEUP] == ["F", "F", "F", "D", "D", "UTIL", "G"]
    assert [slot for slot, _ in d.OPP_LINEUP] == [slot for slot, _ in d.MY_LINEUP]
    # The scoreboard total is the sum of the lineup's points, rounded for display.
    for team, lineup in ((d.MY_TEAM, d.MY_LINEUP), (d.OPP_TEAM, d.OPP_LINEUP)):
        assert team["score"] == pytest.approx(sum(p["pts"] or 0 for _, p in lineup), abs=0.11)
    for _, p in d.MY_LINEUP + d.OPP_LINEUP:
        assert p["pos"] in {"F", "D", "G"}
        assert p["status"] in {"final", "yet"}
    # Nobody is on injured reserve: we do not invent injuries for real players.
    assert d.IR == []
    names = [p["name"] for _, p in d.MY_LINEUP + d.OPP_LINEUP] + [p["name"] for p in d.BENCH]
    assert len(names) == len(set(names))
    assert any(row[6] for row in d.PLAYERS) and not all(row[6] for row in d.PLAYERS)
