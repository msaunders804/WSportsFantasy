import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from undertow import notify
from undertow.config import get_settings
from undertow.data import get_engine
from undertow.data.models import User, WaitlistSignup
from undertow.fantasy import auth
from undertow.web import accounts
from undertow.web import app as web_app

pytestmark = pytest.mark.db
PASSWORD = "a long enough password"


@pytest.fixture(autouse=True)
def fresh_limiters():
    for name in (
        "login_ip_limiter",
        "login_email_limiter",
        "register_limiter",
        "join_limiter",
        "forgot_ip_limiter",
        "forgot_email_limiter",
        "set_password_limiter",
    ):
        getattr(accounts, name)._events.clear()


@pytest.fixture
def emails(monkeypatch):
    """Capture the emails instead of sending them."""
    sent = []
    monkeypatch.setattr(
        notify,
        "send_welcome_email",
        lambda settings, to, first, token, days, **_: sent.append(("welcome", to, token)),
    )
    monkeypatch.setattr(
        notify,
        "send_password_reset_email",
        lambda settings, to, first, token, hours, **_: sent.append(("reset", to, token)),
    )
    monkeypatch.setattr(notify, "send_signup_alert", lambda *a, **k: True)
    return sent


@pytest.fixture
def client(fantasy_db, emails):
    return TestClient(web_app.create_app(), follow_redirects=False)


def csrf(client, path) -> str:
    return re.search(r'name="csrf" value="([0-9a-f]+)"', client.get(path).text).group(1)


def join_list(client, email="fan@example.com", first_name="Sam"):
    return client.post("/signup", data={"email": email, "consent": "on", "first_name": first_name})


def set_password(client, token, password=PASSWORD, page=None):
    page = page or f"/account/set-password?token={token}"
    return client.post(
        "/account/set-password",
        data={"csrf": csrf(client, page), "token": token, "password": password},
    )


def count(engine, model) -> int:
    with Session(engine) as s:
        return s.scalar(select(func.count()).select_from(model))


def test_signup_creates_an_account_and_emails_a_link(client, emails, fantasy_db):
    resp = join_list(client)
    assert resp.status_code == 303 and resp.headers["location"] == "/thanks"
    assert [(k, to) for k, to, _ in emails] == [("welcome", "fan@example.com")]
    with Session(fantasy_db) as s:
        user = s.scalar(select(User))
        assert user.display_name == "Sam" and user.password_hash is None


def test_signup_without_a_name_uses_the_email_name(client, fantasy_db):
    join_list(client, email="pat@example.com", first_name="")
    with Session(fantasy_db) as s:
        assert s.scalar(select(User.display_name)) == "pat"


def test_repeat_signup_makes_no_second_account_or_email(client, emails, fantasy_db):
    join_list(client)
    join_list(client, email="FAN@example.com")
    assert len(emails) == 1
    assert count(fantasy_db, User) == 1 and count(fantasy_db, WaitlistSignup) == 1


def test_invalid_signup_makes_no_account(client, emails, fantasy_db):
    client.post("/signup", data={"email": "nope", "consent": "on"})
    assert emails == [] and count(fantasy_db, User) == 0


def test_a_failing_email_does_not_break_signup(client, monkeypatch, fantasy_db):
    def boom(*a, **k):
        raise RuntimeError("mail down")

    monkeypatch.setattr(notify, "send_welcome_email", boom)
    resp = join_list(client)
    assert resp.status_code == 303 and resp.headers["location"] == "/thanks"
    assert count(fantasy_db, WaitlistSignup) == 1  # they are still on the list


def test_full_flow_from_signup_to_the_account_page(client, emails):
    join_list(client)
    token = emails[0][2]
    page = client.get(f"/account/set-password?token={token}")
    assert page.status_code == 200 and "Choose a password" in page.text
    resp = set_password(client, token)
    assert resp.status_code == 303 and resp.headers["location"] == "/account"
    account = client.get("/account")
    assert account.status_code == 200
    assert "Hi, Sam" in account.text and "fan@example.com" in account.text
    assert "We'll place you in a league" in account.text
    assert "Nov 20" in account.text and "Nov 30 to Dec 4" in account.text
    # And they can log in normally afterwards.
    client.cookies.clear()
    login = client.post(
        "/login",
        data={"csrf": csrf(client, "/login"), "email": "fan@example.com", "password": PASSWORD},
    )
    assert login.status_code == 303 and login.headers["location"] == "/account"


def test_set_password_page_keeps_the_link_secret(client, emails):
    join_list(client)
    token = emails[0][2]
    resp = client.get(f"/account/set-password?token={token}")
    assert resp.headers["referrer-policy"] == "no-referrer"
    assert resp.headers["cache-control"] == "no-store"
    assert 'name="referrer" content="no-referrer"' in resp.text
    bad = client.get("/account/set-password?token=nope")
    assert bad.headers["referrer-policy"] == "no-referrer"


def test_a_link_cannot_be_used_twice(client, emails):
    join_list(client)
    token = emails[0][2]
    assert set_password(client, token).status_code == 303
    other = TestClient(web_app.create_app(), follow_redirects=False)
    assert other.get(f"/account/set-password?token={token}").status_code == 410
    page = "/password/forgot"
    resp = other.post(
        "/account/set-password",
        data={"csrf": csrf(other, page), "token": token, "password": "someone else's try"},
    )
    assert resp.status_code == 410 and "expired" in resp.text


def test_bad_or_missing_token_shows_the_expired_page(client):
    for path in ("/account/set-password", "/account/set-password?token=bogus"):
        resp = client.get(path)
        assert resp.status_code == 410
        assert 'href="/password/forgot"' in resp.text


def test_weak_password_keeps_the_link_usable(client, emails):
    join_list(client)
    token = emails[0][2]
    resp = set_password(client, token, password="short")
    assert resp.status_code == 422 and "at least 10 characters" in resp.text
    assert 'name="token"' in resp.text  # the form is shown again with the same link
    assert set_password(client, token).status_code == 303


def test_set_password_needs_a_csrf_token(client, emails):
    join_list(client)
    token = emails[0][2]
    client.get(f"/account/set-password?token={token}")
    resp = client.post("/account/set-password", data={"token": token, "password": PASSWORD})
    assert resp.status_code == 403
    with Session(get_engine()) as s:
        assert auth.token_is_valid(s, token)  # nothing was spent


def test_account_page_needs_login(client):
    resp = client.get("/account")
    assert resp.status_code == 303 and resp.headers["location"] == "/login?next=/account"


def test_forgot_password_gives_the_same_answer_for_everyone(client, emails, fantasy_db):
    join_list(client)
    emails.clear()
    answers = []
    for email in ("fan@example.com", "nobody@example.com"):
        c = TestClient(web_app.create_app(), follow_redirects=False)
        resp = c.post(
            "/password/forgot",
            data={"csrf": csrf(c, "/password/forgot"), "email": email},
        )
        answers.append((resp.status_code, re.sub(r"\s+", " ", resp.text)))
    assert answers[0][0] == answers[1][0] == 200
    assert "If that email has an Undertow account" in answers[0][1]
    # Only the real account got an email.
    assert [(k, to) for k, to, _ in emails] == [("reset", "fan@example.com")]


def test_forgot_password_link_replaces_the_welcome_link(client, emails):
    join_list(client)
    welcome = emails[0][2]
    client.post(
        "/password/forgot",
        data={"csrf": csrf(client, "/password/forgot"), "email": "fan@example.com"},
    )
    reset = emails[1][2]
    assert client.get(f"/account/set-password?token={welcome}").status_code == 410
    assert client.get(f"/account/set-password?token={reset}").status_code == 200


def test_forgot_password_validates_the_address(client, emails):
    resp = client.post(
        "/password/forgot", data={"csrf": csrf(client, "/password/forgot"), "email": "nope"}
    )
    assert resp.status_code == 422 and emails == []


def test_forgot_password_is_rate_limited_per_email_and_stays_silent(client, emails):
    join_list(client)
    emails.clear()
    for _ in range(5):
        resp = client.post(
            "/password/forgot",
            data={"csrf": csrf(client, "/password/forgot"), "email": "fan@example.com"},
        )
        assert resp.status_code in (200, 429)
    # Three emails at most to one inbox, however many times the form is submitted.
    assert len([e for e in emails if e[0] == "reset"]) == 3


def test_forgot_password_is_rate_limited_per_address(client):
    codes = []
    for i in range(7):
        resp = client.post(
            "/password/forgot",
            data={"csrf": csrf(client, "/password/forgot"), "email": f"p{i}@example.com"},
        )
        codes.append(resp.status_code)
    assert codes[:5] == [200] * 5 and codes[5:] == [429, 429]


def test_pending_account_cannot_log_in_and_is_pointed_to_the_link(client, emails):
    join_list(client)
    resp = client.post(
        "/login",
        data={
            "csrf": csrf(client, "/login"),
            "email": "fan@example.com",
            "password": "whatever you guess",
        },
    )
    assert resp.status_code == 401 and "do not match" in resp.text
    assert "/password/forgot" in client.get("/login").text


def test_registering_with_a_waitlist_email_points_to_forgot_password(client):
    join_list(client)
    resp = client.post(
        "/register",
        data={
            "csrf": csrf(client, "/register"),
            "email": "fan@example.com",
            "display_name": "Sam",
            "password": PASSWORD,
        },
    )
    assert resp.status_code == 422 and "already exists" in resp.text
    assert "Forgot your password" in resp.text


def test_nav_shows_log_in_then_my_account(client, emails):
    assert 'href="/login">Log in<' in client.get("/").text
    join_list(client)
    set_password(client, emails[0][2])
    home = client.get("/").text
    assert 'href="/account">My account<' in home and "Log in<" not in home


def test_every_page_shows_the_same_key_dates(client):
    home = client.get("/").text
    guide = client.get("/fantasy-101").text
    for text in ("Nov 15", "Nov 20", "Nov 30 to Dec 4", "Dec 5"):
        assert text in home and text in guide


def test_signup_does_not_make_accounts_while_accounts_are_off_in_production(
    monkeypatch, fantasy_db, emails
):
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("SECRET_KEY", "a-long-random-production-secret")
    monkeypatch.delenv("ACCOUNTS_ENABLED", raising=False)
    get_settings.cache_clear()
    try:
        off = TestClient(web_app.create_app(), follow_redirects=False)
        resp = off.post("/signup", data={"email": "fan@example.com", "consent": "on"})
        assert resp.status_code == 303
        assert emails == [] and count(fantasy_db, User) == 0
        assert count(fantasy_db, WaitlistSignup) == 1  # still on the list, for a later backfill
        assert off.get("/account/set-password?token=x").status_code == 404
        assert off.get("/password/forgot").status_code == 404
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
        web_app.create_app()
