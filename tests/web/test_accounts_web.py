import re
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from undertow.config import get_settings
from undertow.data.models import League, LeagueSettings, User
from undertow.fantasy import auth, leagues
from undertow.web import accounts
from undertow.web import app as web_app

PASSWORD = "a long enough password"


@pytest.fixture(autouse=True)
def fresh_limiters():
    for limiter in (
        accounts.login_ip_limiter,
        accounts.login_email_limiter,
        accounts.register_limiter,
        accounts.join_limiter,
        accounts.league_register_limiter,
        accounts.invite_limiter,
    ):
        limiter._events.clear()


@pytest.fixture
def client(fantasy_db):
    return TestClient(web_app.create_app(), follow_redirects=False)


def csrf(client, path="/login") -> str:
    html = client.get(path).text
    return re.search(r'name="csrf" value="([0-9a-f]+)"', html).group(1)


def register(client, email="fan@example.com", name="Sam", next_url="", password=PASSWORD):
    return client.post(
        "/register",
        data={
            "csrf": csrf(client, "/register"),
            "email": email,
            "display_name": name,
            "password": password,
            "next": next_url,
        },
    )


def login(client, email="fan@example.com", password=PASSWORD, next_url=""):
    return client.post(
        "/login",
        data={"csrf": csrf(client), "email": email, "password": password, "next": next_url},
    )


def post(client, path, data, page="/leagues"):
    return client.post(path, data={"csrf": csrf(client, page), **data})


def make_admin(engine, email="admin@example.com") -> None:
    with Session(engine) as s:
        user = auth.register_user(s, email=email, display_name="Admin", password=PASSWORD).user
        user.is_admin = True
        s.commit()


def make_league(engine, size=4) -> SimpleNamespace:
    with Session(engine) as s:
        admin = s.scalar(select(User).where(User.is_admin))
        league, _ = leagues.create_league(s, admin, name="Alpha", season_id=11, size=size)
        return SimpleNamespace(id=league.id, invite_code=league.invite_code)


pytestmark = pytest.mark.db


def test_register_logs_in_and_sets_a_hardened_cookie(client):
    resp = register(client)
    assert resp.status_code == 303 and resp.headers["location"] == "/home"
    cookie = next(c for c in resp.headers.get_list("set-cookie") if c.startswith("ut_session="))
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/" in cookie
    page = client.get("/home")
    assert page.status_code == 200 and "Welcome, Sam!" in page.text


def test_post_without_csrf_token_is_refused(client):
    client.get("/register")
    resp = client.post(
        "/register", data={"email": "a@example.com", "display_name": "A", "password": PASSWORD}
    )
    assert resp.status_code == 403
    resp = client.post(
        "/register",
        data={
            "csrf": "0" * 64,
            "email": "a@example.com",
            "display_name": "A",
            "password": PASSWORD,
        },
    )
    assert resp.status_code == 403


def test_csrf_token_from_another_browser_is_refused(client, fantasy_db):
    other = TestClient(web_app.create_app(), follow_redirects=False)
    stolen = csrf(other, "/register")
    client.get("/register")
    resp = client.post(
        "/register",
        data={"csrf": stolen, "email": "a@example.com", "display_name": "A", "password": PASSWORD},
    )
    assert resp.status_code == 403


def test_register_validation_errors_rerender(client):
    resp = register(client, email="nope", password="short")
    assert resp.status_code == 422
    assert "Enter a valid email address." in resp.text
    assert "Use at least 10 characters." in resp.text
    assert "ut_session" not in resp.headers.get("set-cookie", "")


def test_duplicate_registration_is_refused(client):
    register(client)
    other = TestClient(web_app.create_app(), follow_redirects=False)
    resp = register(other, email="FAN@example.com")
    assert resp.status_code == 422 and "already exists" in resp.text


def test_login_logout_roundtrip(client):
    register(client)
    client.cookies.clear()
    assert client.get("/leagues").status_code == 303  # logged out
    resp = login(client)
    assert resp.status_code == 303
    assert client.get("/leagues").status_code == 200
    out = post(client, "/logout", {})
    assert out.status_code == 303
    client.cookies.delete("ut_session")
    assert client.get("/leagues").status_code == 303


def test_wrong_password_and_unknown_email_give_the_same_answer(client):
    register(client)
    client.cookies.clear()
    wrong = login(client, password="not the password")
    unknown = login(client, email="nobody@example.com")
    assert wrong.status_code == unknown.status_code == 401
    assert "do not match" in wrong.text and "do not match" in unknown.text
    assert "ut_session" not in wrong.headers.get("set-cookie", "")


def test_login_is_rate_limited_per_email(client):
    register(client)
    client.cookies.clear()
    codes = [login(client, password="wrong wrong wrong").status_code for _ in range(7)]
    assert codes[:5] == [401] * 5
    assert codes[5:] == [429, 429]
    assert login(client).status_code == 429  # even the right password waits out the window


@pytest.mark.parametrize(
    "target", ["https://evil.example", "//evil.example", "/\\evil.example", "javascript:alert(1)"]
)
def test_login_does_not_redirect_off_site(client, target):
    register(client)
    client.cookies.clear()
    resp = login(client, next_url=target)
    assert resp.headers["location"] == "/home"


def test_login_redirects_back_to_a_same_site_page(client):
    register(client)
    client.cookies.clear()
    assert login(client, next_url="/join/abc").headers["location"] == "/join/abc"


def test_protected_pages_redirect_to_login_with_next(client):
    resp = client.get("/leagues")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login?next=/leagues"
    resp = client.get("/join/abc?x=1")
    assert resp.headers["location"] == "/login?next=/join/abc%3Fx%3D1"


def test_members_can_open_league_registration_but_not_make_house_leagues(client, fantasy_db):
    register(client)
    form = client.get("/leagues/new")
    assert form.status_code == 200 and "League type (staff)" not in form.text
    # A member who sends kind=house still gets a private league they commission.
    resp = post(
        client,
        "/leagues/new",
        {"name": "X", "size": "10", "team_name": "Mine", "kind": "house"},
        "/leagues/new",
    )
    assert resp.status_code == 303
    with Session(fantasy_db) as s:
        league = s.scalar(select(League))
        assert league.kind == "private"


def test_member_registers_a_private_league_and_sees_invite_tools(client, fantasy_db):
    register(client)
    resp = post(
        client,
        "/leagues/new",
        {"name": "Friends", "size": "4", "team_name": "Ice Queens"},
        "/leagues/new",
    )
    assert resp.status_code == 303
    page = client.get(resp.headers["location"])
    assert "Friends" in page.text and "Ice Queens" in page.text
    assert "Invite your friends" in page.text and "Send invites" in page.text
    assert "3 spots open" in page.text


def test_league_registration_requires_a_team_name_and_even_size(client, fantasy_db):
    register(client)
    no_team = post(client, "/leagues/new", {"name": "F", "size": "4"}, "/leagues/new")
    assert no_team.status_code == 422 and "team a name" in no_team.text
    odd = post(client, "/leagues/new", {"name": "F", "size": "5", "team_name": "T"}, "/leagues/new")
    assert odd.status_code == 422 and "even number" in odd.text


def test_commissioner_invites_by_email_and_duplicates_are_skipped(client, fantasy_db, monkeypatch):
    sent = []
    monkeypatch.setattr(
        accounts.notify,
        "send_league_invite_email",
        lambda settings, to, inviter, league_name, code, client=None: sent.append(to) or True,
    )
    register(client)
    resp = post(
        client, "/leagues/new", {"name": "F", "size": "4", "team_name": "T"}, "/leagues/new"
    )
    path = resp.headers["location"]
    first = post(client, f"{path}/invites", {"emails": "a@example.com, b@example.com"}, path)
    assert first.status_code == 200 and "Invited 2." in first.text
    again = post(client, f"{path}/invites", {"emails": "a@example.com\nnot-an-email"}, path)
    assert "No new invites." in again.text
    assert "already invited" in again.text and "not a valid email" in again.text
    assert sent == ["a@example.com", "b@example.com"]


def test_members_cannot_send_invites_and_strangers_cannot_see_the_league(client, fantasy_db):
    register(client, email="boss@example.com")
    resp = post(
        client, "/leagues/new", {"name": "F", "size": "4", "team_name": "T"}, "/leagues/new"
    )
    path = resp.headers["location"]
    code = client.get(path).text.split("/join/")[1].split('"')[0]
    client.post("/logout", data={"csrf": csrf(client, "/leagues")})
    register(client, email="member@example.com")
    post(client, f"/join/{code}", {"team_name": "Mem"}, f"/join/{code}")
    denied = post(client, f"{path}/invites", {"emails": "x@example.com"}, "/leagues")
    assert denied.status_code == 403


def test_staff_create_a_league_and_see_the_invite_link(client, fantasy_db):
    make_admin(fantasy_db)
    login(client, email="admin@example.com")
    resp = post(client, "/leagues/new", {"name": "Alpha", "size": "10"}, "/leagues/new")
    assert resp.status_code == 303
    page = client.get(resp.headers["location"])
    assert "Alpha" in page.text
    with Session(fantasy_db) as s:
        code = s.scalar(select(League.invite_code))
    assert f"/join/{code}" in page.text


def test_league_creation_errors_rerender(client, fantasy_db):
    make_admin(fantasy_db)
    login(client, email="admin@example.com")
    odd = post(client, "/leagues/new", {"name": "Alpha", "size": "9"}, "/leagues/new")
    assert odd.status_code == 422 and "even number" in odd.text
    blank = post(client, "/leagues/new", {"name": "Alpha", "size": ""}, "/leagues/new")
    assert blank.status_code == 422 and "Enter the number of teams" in blank.text


def test_join_flow_through_an_invite_link(client, fantasy_db):
    make_admin(fantasy_db)
    league = make_league(fantasy_db)
    register(client)
    path = f"/join/{league.invite_code}"
    assert "Join Alpha" in client.get(path).text
    resp = post(client, path, {"team_name": "Ice Queens"}, path)
    assert resp.status_code == 303 and resp.headers["location"] == f"/leagues/{league.id}"
    page = client.get(resp.headers["location"])
    assert "Ice Queens" in page.text
    assert "Invite link" not in page.text  # members cannot see or reshare the code
    assert league.invite_code not in page.text


def test_join_with_a_taken_team_name_shows_the_error(client, fantasy_db):
    make_admin(fantasy_db)
    league = make_league(fantasy_db)
    path = f"/join/{league.invite_code}"
    register(client, email="a@example.com")
    post(client, path, {"team_name": "Ice Queens"}, path)
    other = TestClient(web_app.create_app(), follow_redirects=False)
    register(other, email="b@example.com")
    resp = post(other, path, {"team_name": "ice queens"}, path)
    assert resp.status_code == 422 and "already has a team" in resp.text


def test_unknown_invite_code_is_a_404(client):
    register(client)
    assert client.get("/join/not-a-real-code").status_code == 404
    assert post(client, "/join/not-a-real-code", {"team_name": "X"}).status_code == 404


def test_non_members_cannot_see_a_league(client, fantasy_db):
    make_admin(fantasy_db)
    league = make_league(fantasy_db)
    register(client)
    assert client.get(f"/leagues/{league.id}").status_code == 404
    resp = post(client, f"/leagues/{league.id}/settings", {"roster_bench": "9"})
    assert resp.status_code == 404
    assert client.get("/leagues/99999").status_code == 404  # same answer as a hidden league


def test_members_see_settings_read_only_and_cannot_change_them(client, fantasy_db):
    make_admin(fantasy_db)
    league = make_league(fantasy_db)
    register(client)
    path = f"/join/{league.invite_code}"
    post(client, path, {"team_name": "T"}, path)
    page = client.get(f"/leagues/{league.id}")
    assert "Bench slots" in page.text and 'name="roster_bench"' not in page.text
    resp = post(client, f"/leagues/{league.id}/settings", {"roster_bench": "9"})
    assert resp.status_code == 403
    with Session(fantasy_db) as s:
        assert s.get(LeagueSettings, league.id).roster_bench == 4


def test_commissioner_edits_settings_and_bad_values_are_rejected(client, fantasy_db):
    make_admin(fantasy_db)
    league = make_league(fantasy_db, size=10)
    register(client)
    path = f"/join/{league.invite_code}"
    post(client, path, {"team_name": "T"}, path)
    with Session(fantasy_db) as s:
        admin = s.scalar(select(User).where(User.is_admin))
        user = s.scalar(select(User).where(User.email == "fan@example.com"))
        leagues.set_role(s, admin, league.id, user.id, "commissioner")
    page_path = f"/leagues/{league.id}"
    assert 'name="roster_bench"' in client.get(page_path).text
    ok = post(
        client, page_path + "/settings", {"roster_bench": "6", "waiver_hours": "24"}, page_path
    )
    assert ok.status_code == 200 and "Settings saved." in ok.text
    bad = post(client, page_path + "/settings", {"roster_g": "0"}, page_path)
    assert bad.status_code == 422
    text = post(client, page_path + "/settings", {"roster_bench": "lots"}, page_path)
    assert text.status_code == 422 and "whole number" in text.text
    with Session(fantasy_db) as s:
        row = s.get(LeagueSettings, league.id)
        assert (row.roster_bench, row.waiver_hours, row.roster_g) == (6, 24, 1)


def test_only_staff_can_assign_roles_through_the_web(client, fantasy_db):
    make_admin(fantasy_db)
    league = make_league(fantasy_db)
    register(client)
    path = f"/join/{league.invite_code}"
    post(client, path, {"team_name": "T"}, path)
    with Session(fantasy_db) as s:
        uid = s.scalar(select(User.id).where(User.email == "fan@example.com"))
    resp = post(
        client,
        f"/leagues/{league.id}/roles",
        {"user_id": str(uid), "role": "commissioner"},
        "/leagues",
    )
    assert resp.status_code == 403


def test_pages_escape_user_supplied_names(client, fantasy_db):
    make_admin(fantasy_db)
    league = make_league(fantasy_db)
    register(client, name="<script>alert(1)</script>")
    path = f"/join/{league.invite_code}"
    post(client, path, {"team_name": "<img src=x onerror=alert(1)>"}, path)
    page = client.get(f"/leagues/{league.id}").text
    assert "<script>alert(1)" not in page and "<img src=x" not in page
    assert "&lt;script&gt;" in page


def test_account_pages_are_off_in_production_until_enabled(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("SECRET_KEY", "a-long-random-production-secret")
    monkeypatch.delenv("ACCOUNTS_ENABLED", raising=False)
    get_settings.cache_clear()
    try:
        off = TestClient(web_app.create_app())
        assert off.get("/login").status_code == 404
        assert off.get("/register").status_code == 404
        assert "My leagues" not in off.get("/").text
        monkeypatch.setenv("ACCOUNTS_ENABLED", "true")
        get_settings.cache_clear()
        assert TestClient(web_app.create_app()).get("/login").status_code == 200
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
        web_app.create_app()  # restore the template flag for later tests


def test_production_refuses_to_open_accounts_with_the_default_secret(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("ACCOUNTS_ENABLED", "true")
    monkeypatch.setenv("SECRET_KEY", "dev-only-change-me")
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="SECRET_KEY"):
            web_app.create_app()
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
