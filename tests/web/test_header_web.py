import re

import pytest
from fastapi.testclient import TestClient

from undertow import notify
from undertow.config import get_settings
from undertow.fantasy import auth
from undertow.web import accounts
from undertow.web import app as web_app

PASSWORD = "a long enough password"
PAGES = ["/", "/about", "/fantasy-101", "/privacy", "/preview/matchup", "/preview/draft"]


def toggle_in_footer(html: str) -> bool:
    """Exactly one theme button, and it comes after the opening of the footer."""
    return html.count('id="theme-toggle"') == 1 and html.index('id="theme-toggle"') > html.index(
        "<footer"
    )


@pytest.mark.parametrize("path", PAGES)
def test_theme_button_is_in_the_footer_not_the_header(path):
    html = TestClient(web_app.create_app()).get(path).text
    assert toggle_in_footer(html)


def test_the_theme_script_still_wires_up_the_footer_button():
    html = TestClient(web_app.create_app()).get("/").text
    assert 'getElementById("theme-toggle")' in html and "if (!btn) return;" in html


def test_logged_out_visitors_see_a_log_in_button():
    html = TestClient(web_app.create_app()).get("/").text
    assert re.search(r'class="login-link" href="/login">Log in<', html)
    assert 'class="acct"' not in html


@pytest.fixture(autouse=True)
def fresh_limiters():
    for name in ("login_ip_limiter", "login_email_limiter", "register_limiter"):
        getattr(accounts, name)._events.clear()


@pytest.fixture
def signed_in(fantasy_db, monkeypatch):
    monkeypatch.setattr(notify, "send_signup_alert", lambda *a, **k: True)
    client = TestClient(web_app.create_app(), follow_redirects=False)
    token = re.search(r'name="csrf" value="([0-9a-f]+)"', client.get("/register").text).group(1)
    resp = client.post(
        "/register",
        data={
            "csrf": token,
            "email": "pat@example.com",
            "display_name": "<b>Patricia</b> Long-Surname",
            "password": PASSWORD,
        },
    )
    assert resp.status_code == 303
    return client


# The full-screen draft room has no account control: its top bar is the pick clock.
# "/" sends signed-in members to /home, so /home stands in for it.
HEADER_PAGES = ["/home" if p == "/" else p for p in PAGES if p != "/preview/draft"]


@pytest.mark.db
@pytest.mark.parametrize("path", HEADER_PAGES)
def test_signed_in_members_see_their_account_instead_of_log_in(signed_in, path):
    html = signed_in.get(path).text
    assert 'class="acct" href="/account"' in html
    assert "login-link" not in html
    assert toggle_in_footer(html)


@pytest.mark.db
def test_account_control_shows_initial_and_escaped_name(signed_in):
    html = signed_in.get("/home").text
    assert '<span class="acct-avatar" aria-hidden="true">&lt;</span>' in html
    assert "&lt;b&gt;Patricia&lt;/b&gt;" in html and "<b>Patricia</b>" not in html
    assert 'aria-label="My account (&lt;b&gt;Patricia&lt;/b&gt; Long-Surname)"' in html


@pytest.mark.db
def test_a_bad_or_ended_session_shows_log_in(signed_in, fantasy_db):
    from sqlalchemy.orm import Session

    signed_in.cookies.set("ut_session", "not-a-real-session")
    assert "login-link" in signed_in.get("/").text
    signed_in.cookies.clear()
    with Session(fantasy_db) as s:
        user = auth.authenticate(s, "pat@example.com", PASSWORD)
        token = auth.create_session(s, user)
        signed_in.cookies.set("ut_session", token)
        assert 'class="acct"' in signed_in.get("/about").text
        auth.end_all_sessions(s, user.id)
    assert "login-link" in signed_in.get("/").text


@pytest.mark.db
def test_logging_out_brings_back_the_log_in_button(signed_in):
    csrf = re.search(r'name="csrf" value="([0-9a-f]+)"', signed_in.get("/account").text).group(1)
    signed_in.post("/logout", data={"csrf": csrf})
    assert "login-link" in signed_in.get("/").text


def test_a_database_error_in_the_header_does_not_break_the_page(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(web_app.auth, "user_for_token", boom)
    client = TestClient(web_app.create_app())
    client.cookies.set("ut_session", "anything")
    resp = client.get("/")
    assert resp.status_code == 200 and "login-link" in resp.text


def test_no_account_control_while_accounts_are_off_in_production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("SECRET_KEY", "a-long-random-production-secret")
    monkeypatch.delenv("ACCOUNTS_ENABLED", raising=False)
    get_settings.cache_clear()
    try:
        html = TestClient(web_app.create_app()).get("/").text
        assert "login-link" not in html and 'class="acct"' not in html
        assert toggle_in_footer(html)  # the theme button still works
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
        web_app.create_app()
