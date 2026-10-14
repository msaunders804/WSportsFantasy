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
    assert 'href="/static/tokens.css?v=' in resp.text
    assert 'href="/static/site.css?v=' in resp.text
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
        assert TestClient(create_app()).get("/preview/draft").status_code == 404
        assert (
            TestClient(create_app()).post("/preview/draft/step", json={"picks": []}).status_code
            == 404
        )
        monkeypatch.setenv("PREVIEW_ENABLED", "true")
        get_settings.cache_clear()
        assert TestClient(create_app()).get("/preview/matchup").status_code == 200
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def test_preview_data_is_internally_consistent():
    from undertow.web import preview_data as d

    assert len(d.TEAMS) == 10 and len(d.MATCHUPS) == 5
    assert d.MATCHUPS[0]["a"] == d.MY_SLUG  # matchup 1 is the viewer's
    in_matchups = [slug for m in d.MATCHUPS for slug in (m["a"], m["b"])]
    assert sorted(in_matchups) == sorted(d.TEAMS)
    seen = set()
    for t in d.TEAMS.values():
        assert [slot for slot, _ in t["lineup"]] == ["F", "F", "F", "D", "D", "UTIL", "G"]
        assert len(t["bench"]) == 4
        # The scoreboard total is the sum of the lineup's points, rounded for display.
        assert t["score"] == pytest.approx(sum(p["pts"] or 0 for _, p in t["lineup"]), abs=0.11)
        assert t["bench_score"] == pytest.approx(sum(p["pts"] or 0 for p in t["bench"]), abs=0.11)
        for p in [p for _, p in t["lineup"]] + t["bench"]:
            assert p["pos"] in {"F", "D", "G"} and p["status"] in {"final", "yet"}
            assert p["id"] not in seen  # nobody is on two teams
            seen.add(p["id"])
            assert str(p["id"]) in d.PLAYER_PAGES  # every name links to a real page
    # Nobody is on injured reserve: we do not invent injuries for real players.
    assert d.IR == []
    assert any(row[6] for row in d.PLAYERS) and not all(row[6] for row in d.PLAYERS)
    assert str(d.FEATURED_PLAYER_ID) in d.PLAYER_PAGES


def test_featured_player_is_top_forward_on_my_team():
    from undertow.web import preview_data as d

    mine = d.TEAMS[d.MY_SLUG]
    forwards = [p for _, p in mine["lineup"] if p["pos"] == "F"] + [
        p for p in mine["bench"] if p["pos"] == "F"
    ]
    page = d.PLAYER_PAGES[str(d.FEATURED_PLAYER_ID)]
    assert page["pos"] == "F" and page["mine"]
    assert page["id"] in {p["id"] for p in forwards}


def test_matchup_shows_both_benches_and_links_players():
    resp = TestClient(app).get("/preview/matchup")
    assert resp.status_code == 200
    assert 'aria-label="Bench"' in resp.text
    assert "Points left on the bench" in resp.text
    assert 'href="/preview/player/' in resp.text


def test_rival_matchup_page_and_bounds():
    client = TestClient(app)
    resp = client.get("/preview/matchup/2")
    assert resp.status_code == 200
    assert "League matchup" in resp.text
    assert 'href="/preview/league"' in resp.text  # way back
    assert client.get("/preview/matchup/0").status_code == 404
    assert client.get("/preview/matchup/99").status_code == 404


def test_team_page_explains_games_column_and_links_players():
    resp = TestClient(app).get("/preview/team")
    assert "Games this week: how many games" in resp.text
    assert 'class="help-btn"' in resp.text
    assert 'href="/preview/player/' in resp.text


def test_player_page_has_info_weeks_and_headlines_placeholder():
    from undertow.web import preview_data as d

    client = TestClient(app)
    resp = client.get(f"/preview/player/{d.FEATURED_PLAYER_ID}")
    page = d.PLAYER_PAGES[str(d.FEATURED_PLAYER_ID)]
    assert resp.status_code == 200
    assert page["name"] in resp.text
    assert "Week by week" in resp.text and "Recent games" in resp.text
    assert "Headlines" in resp.text
    assert len(page["weekly"]) == d.LEAGUE["week"] - 1  # only weeks before the demo week
    assert client.get("/preview/player/1").status_code == 404


def test_players_tab_has_trending_sort_and_trend_reasons():
    from undertow.web import preview_data as d

    resp = TestClient(app).get("/preview/players")
    assert 'data-sort="trend" aria-pressed="true"' in resp.text
    assert 'data-sort="fpg"' in resp.text
    # Opens in trending order, and includes a goalie fresh off a shutout.
    trends = [r[7] for r in sorted(d.PLAYERS, key=lambda r: -r[7])]
    assert trends == sorted(trends, reverse=True)
    assert any("shutout" in r[8].lower() for r in d.PLAYERS if r[1] == "G")


def test_league_has_no_invite_and_links_every_matchup():
    resp = TestClient(app).get("/preview/league")
    assert "Invite" not in resp.text and "invite" not in resp.text
    for n in range(1, 6):
        assert f'href="/preview/matchup/{n}"' in resp.text


def test_landing_shows_app_screenshots_and_every_image_exists():
    import re

    client = TestClient(app)
    html = client.get("/").text
    assert "A peek at the app" in html
    images = re.findall(r'src="(/static/demo/[^"]+)"', html)
    assert len(images) >= 5
    for src in images:
        resp = client.get(src)
        assert resp.status_code == 200 and resp.headers["content-type"] == "image/webp", src
    # Every screenshot has real alt text, and the sample-data note is on the page.
    assert all(alt.strip() for alt in re.findall(r'<img [^>]*alt="([^"]*)"', html))
    assert "made up" in html


def test_static_asset_urls_carry_a_content_version():
    import re

    client = TestClient(app)
    versions = set()
    for path in ("/", "/preview/matchup", "/preview/draft", "/privacy"):
        html = client.get(path).text
        links = re.findall(r'(?:href|src)="(/static/[^"]+)"', html)
        assert links and all("?v=" in link for link in links), path
        versions |= {link.split("?v=")[1] for link in links}
    assert len(versions) == 1  # one version for the whole deploy
    assert client.get("/static/site.css?v=anything").status_code == 200
