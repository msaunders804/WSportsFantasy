import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from undertow import notify
from undertow.config import get_settings
from undertow.data.models import League, User
from undertow.fantasy import auth, leagues
from undertow.web import accounts
from undertow.web import app as web_app

pytestmark = pytest.mark.db
PASSWORD = "a long enough password"


@pytest.fixture(autouse=True)
def fresh_limiters(monkeypatch):
    monkeypatch.setattr(notify, "send_signup_alert", lambda *a, **k: True)
    for name in ("login_ip_limiter", "login_email_limiter", "register_limiter", "join_limiter"):
        getattr(accounts, name)._events.clear()


@pytest.fixture
def client(fantasy_db):
    return TestClient(web_app.create_app(), follow_redirects=False)


def csrf(client, path) -> str:
    return re.search(r'name="csrf" value="([0-9a-f]+)"', client.get(path).text).group(1)


def register(client, email="fan@example.com", name="Sam"):
    resp = client.post(
        "/register",
        data={
            "csrf": csrf(client, "/register"),
            "email": email,
            "display_name": name,
            "password": PASSWORD,
        },
    )
    assert resp.status_code == 303
    return resp


def post(client, path, data, page="/account"):
    return client.post(path, data={"csrf": csrf(client, page), **data})


def make_admin(engine) -> None:
    with Session(engine) as s:
        user = auth.register_user(
            s, email="admin@example.com", display_name="Admin", password=PASSWORD
        ).user
        user.is_admin = True
        s.commit()


def make_league(engine, size=4) -> SimpleNamespace:
    with Session(engine) as s:
        admin = s.scalar(select(User).where(User.is_admin))
        league, _ = leagues.create_league(s, admin, name="Alpha", season_id=11, size=size)
        return SimpleNamespace(id=league.id, code=league.invite_code)


def join(client, league, team="Ice Queens"):
    path = f"/join/{league.code}"
    return post(client, path, {"team_name": team}, path)


# ---- the front page and routing ----


def test_visitors_still_see_the_signup_page(client):
    resp = client.get("/")
    assert resp.status_code == 200 and 'name="email"' in resp.text


def test_signed_in_members_are_sent_home_from_the_front_page(client):
    register(client)
    resp = client.get("/")
    assert resp.status_code == 303 and resp.headers["location"] == "/home"


def test_logging_out_brings_the_front_page_back(client):
    register(client)
    post(client, "/logout", {})
    assert client.get("/").status_code == 200


def test_a_bad_session_cookie_still_shows_the_front_page(client):
    client.cookies.set("ut_session", "not-real")
    assert client.get("/").status_code == 200


def test_the_front_page_survives_a_failing_session_lookup(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(web_app.auth, "user_for_token", boom)
    client.cookies.set("ut_session", "anything")
    resp = client.get("/")
    assert resp.status_code == 200 and 'name="email"' in resp.text


def test_wordmark_goes_home_when_signed_in_and_to_the_front_page_when_not(client):
    assert 'class="wordmark" href="/"' in client.get("/about").text
    register(client)
    assert 'class="wordmark" href="/home"' in client.get("/about").text


def test_home_needs_login(client):
    resp = client.get("/home")
    assert resp.status_code == 303 and resp.headers["location"] == "/login?next=/home"


def test_signed_in_visitors_skip_the_login_and_register_forms(client):
    register(client)
    for path in ("/login", "/register"):
        resp = client.get(path)
        assert resp.status_code == 303 and resp.headers["location"] == "/home"


# ---- home with no league yet ----


def test_home_without_a_league_shows_progress_links_and_the_share_card(client):
    register(client)
    html = client.get("/home").text
    assert "Welcome, Sam!" in html
    assert 'class="done"' in html and 'aria-current="step"' in html
    for text in ("by Nov 20", "Nov 30 to Dec 4", "Dec 5"):
        assert text in html
    assert 'href="/preview/draft"' in html and 'href="/fantasy-101"' in html
    assert "data-share" in html and "Copy link" in html
    assert 'value="http://localhost:8000/"' in html


def test_share_link_follows_base_url(fantasy_db, monkeypatch):
    monkeypatch.setenv("BASE_URL", "https://example.test/")
    get_settings.cache_clear()
    try:
        # Cookies are marked Secure for an https BASE_URL, so the test client must use https too.
        client = TestClient(
            web_app.create_app(), base_url="https://example.test", follow_redirects=False
        )
        register(client)
        assert 'value="https://example.test/"' in client.get("/home").text
        assert 'value="https://example.test/"' in client.get("/account").text
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def test_the_account_page_is_now_the_profile_page(client):
    register(client)
    html = client.get("/account").text
    assert "My account" in html and "fan@example.com" in html
    assert "personalize your team" in html
    assert 'href="/home"' in html and "Create a league" not in html


def test_staff_and_members_find_the_league_registration_link(client, fantasy_db):
    make_admin(fantasy_db)
    client.post(
        "/login",
        data={"csrf": csrf(client, "/login"), "email": "admin@example.com", "password": PASSWORD},
    )
    assert 'href="/leagues/new"' in client.get("/account").text
    assert "house leagues" in client.get("/account").text  # staff-only hint
    assert 'href="/leagues/new"' in client.get("/home").text


# ---- home with a league ----


def test_home_with_a_league_before_the_draft(client, fantasy_db):
    make_admin(fantasy_db)
    league = make_league(fantasy_db)
    register(client)
    join(client, league)
    html = client.get("/home").text
    assert "<h1>Alpha</h1>" in html and "Ice Queens" in html and "(you)" in html
    assert "1 of 4 teams joined" in html
    assert "Draft time to be announced" in html and "Nov 30 to Dec 4" in html
    assert "Welcome, Sam" not in html  # the waiting view is gone
    assert "Manage this league" not in html  # a plain member
    assert 'href="/leagues/' in html and "data-share" in html


def test_home_shows_the_draft_time_in_eastern_with_a_countdown(client, fantasy_db):
    make_admin(fantasy_db)
    league = make_league(fantasy_db)
    register(client)
    join(client, league)
    soon = datetime.now(UTC) + timedelta(days=3, hours=2)
    with Session(fantasy_db) as s:
        s.get(League, league.id).draft_at = soon
        s.commit()
    html = client.get("/home").text
    assert "Draft time to be announced" not in html
    assert 'time data-local datetime="' in html and "ET</time>" in html
    assert "Starts in 3 days." in html


def test_home_after_the_scheduled_time_says_so(client, fantasy_db):
    make_admin(fantasy_db)
    league = make_league(fantasy_db)
    register(client)
    join(client, league)
    with Session(fantasy_db) as s:
        s.get(League, league.id).draft_at = datetime.now(UTC) - timedelta(hours=2)
        s.commit()
    assert "The scheduled start time has passed." in client.get("/home").text


def test_home_for_a_started_league_uses_the_active_view(client, fantasy_db):
    make_admin(fantasy_db)
    league = make_league(fantasy_db)
    register(client)
    join(client, league)
    with Session(fantasy_db) as s:
        s.get(League, league.id).status = "in_season"
        s.commit()
    html = client.get("/home").text
    assert "Your league is in season" in html and "Your draft" not in html


def test_commissioners_get_a_manage_link_on_home(client, fantasy_db):
    make_admin(fantasy_db)
    league = make_league(fantasy_db)
    register(client)
    join(client, league)
    with Session(fantasy_db) as s:
        admin = s.scalar(select(User).where(User.is_admin))
        uid = s.scalar(select(User.id).where(User.email == "fan@example.com"))
        leagues.set_role(s, admin, league.id, uid, "commissioner")
    assert "Manage this league" in client.get("/home").text


# ---- setting the draft time ----


def test_commissioner_sets_the_draft_time_in_eastern(client, fantasy_db):
    make_admin(fantasy_db)
    league = make_league(fantasy_db)
    register(client)
    join(client, league)
    with Session(fantasy_db) as s:
        admin = s.scalar(select(User).where(User.is_admin))
        uid = s.scalar(select(User.id).where(User.email == "fan@example.com"))
        leagues.set_role(s, admin, league.id, uid, "commissioner")
    page = f"/leagues/{league.id}"
    resp = post(
        client,
        f"{page}/draft-time",
        {"draft_at": "2026-11-30T19:00", "zone": "America/New_York"},
        page,
    )
    assert resp.status_code == 200 and "Settings saved." in resp.text
    assert "Mon, Nov 30 at 7:00 PM ET" in resp.text
    assert 'value="2026-11-30T19:00"' in resp.text  # the form shows what was saved
    with Session(fantasy_db) as s:
        assert s.get(League, league.id).draft_at == datetime(2026, 12, 1, 0, 0, tzinfo=UTC)
    assert "Mon, Nov 30 at 7:00 PM ET" in client.get("/home").text


def test_blank_draft_time_clears_it(client, fantasy_db):
    make_admin(fantasy_db)
    league = make_league(fantasy_db)
    client.post(
        "/login",
        data={"csrf": csrf(client, "/login"), "email": "admin@example.com", "password": PASSWORD},
    )
    page = f"/leagues/{league.id}"
    post(client, f"{page}/draft-time", {"draft_at": "2026-11-30T19:00", "zone": "UTC"}, page)
    post(client, f"{page}/draft-time", {"draft_at": "", "zone": "UTC"}, page)
    with Session(fantasy_db) as s:
        assert s.get(League, league.id).draft_at is None


@pytest.mark.parametrize(
    "data",
    [
        {"draft_at": "next tuesday", "zone": "America/New_York"},
        {"draft_at": "2026-11-30T19:00", "zone": "Mars/Base"},
        {"draft_at": "2026-11-30T19:00", "zone": ""},
    ],
)
def test_bad_draft_time_input_shows_an_error_and_saves_nothing(client, fantasy_db, data):
    make_admin(fantasy_db)
    league = make_league(fantasy_db)
    client.post(
        "/login",
        data={"csrf": csrf(client, "/login"), "email": "admin@example.com", "password": PASSWORD},
    )
    page = f"/leagues/{league.id}"
    resp = post(client, f"{page}/draft-time", data, page)
    assert resp.status_code == 422 and 'role="alert"' in resp.text
    with Session(fantasy_db) as s:
        assert s.get(League, league.id).draft_at is None


def test_members_cannot_set_the_draft_time(client, fantasy_db):
    make_admin(fantasy_db)
    league = make_league(fantasy_db)
    register(client)
    join(client, league)
    page = f"/leagues/{league.id}"
    assert "draft_at" not in client.get(page).text  # no form for a plain member
    resp = post(
        client, f"{page}/draft-time", {"draft_at": "2026-11-30T19:00", "zone": "UTC"}, "/account"
    )
    assert resp.status_code == 403
    with Session(fantasy_db) as s:
        assert s.get(League, league.id).draft_at is None


def test_non_members_get_a_404_on_the_draft_time_route(client, fantasy_db):
    make_admin(fantasy_db)
    league = make_league(fantasy_db)
    register(client)
    resp = post(
        client,
        f"/leagues/{league.id}/draft-time",
        {"draft_at": "2026-11-30T19:00", "zone": "UTC"},
        "/account",
    )
    assert resp.status_code == 404


# ---- the practice draft in production ----


def test_the_practice_draft_is_served_in_production_once_accounts_open(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("SECRET_KEY", "a-long-random-production-secret")
    monkeypatch.setenv("ACCOUNTS_ENABLED", "true")
    monkeypatch.delenv("PREVIEW_ENABLED", raising=False)
    get_settings.cache_clear()
    try:
        c = TestClient(web_app.create_app(), follow_redirects=False)
        draft = c.get("/preview/draft")
        assert draft.status_code == 200
        # The exit link must not point at a mockup page that is not served here.
        assert 'href="/">' in draft.text or 'href="/home">' in draft.text
        assert "/preview/league" not in draft.text
        for hidden in ("/preview/matchup", "/preview/team", "/preview/players", "/preview/league"):
            assert c.get(hidden).status_code == 404, hidden
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
        web_app.create_app()


def test_nothing_under_preview_is_served_in_production_with_accounts_off(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("SECRET_KEY", "a-long-random-production-secret")
    monkeypatch.delenv("ACCOUNTS_ENABLED", raising=False)
    monkeypatch.delenv("PREVIEW_ENABLED", raising=False)
    get_settings.cache_clear()
    try:
        c = TestClient(web_app.create_app(), follow_redirects=False)
        assert c.get("/preview/draft").status_code == 404
        assert c.get("/home").status_code == 404
        assert c.get("/").status_code == 200  # and no redirect to a home that is off
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
        web_app.create_app()


def test_the_practice_draft_exit_link_goes_home_for_members_in_dev_preview_mode(client):
    assert 'href="/preview/league"' in client.get("/preview/draft").text
