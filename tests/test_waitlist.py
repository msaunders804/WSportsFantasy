import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from undertow.db import get_engine
from undertow.services import notify, waitlist
from undertow.web import app as web_app
from undertow.web.ratelimit import RateLimiter


def test_clean_signup_normalizes_and_validates():
    data, errors = waitlist.clean_signup(
        {
            "email": "  Fan@Example.COM ",
            "consent": "on",
            "experience": "new",
            "first_name": " Sam ",
            "commissioner_note": " friend@example.com ",
        }
    )
    assert errors == {}
    assert data.email == "fan@example.com"
    assert data.first_name == "Sam"
    assert data.experience == "new"
    assert data.commissioner_note == "friend@example.com"


@pytest.mark.parametrize("email", ["", "nope", "a@b", "a b@c.com", "x" * 250 + "@a.com"])
def test_clean_signup_rejects_bad_email(email):
    data, errors = waitlist.clean_signup({"email": email, "consent": "on"})
    assert data is None
    assert "email" in errors


def test_clean_signup_requires_consent():
    data, errors = waitlist.clean_signup({"email": "a@b.co"})
    assert data is None
    assert "consent" in errors


def test_clean_signup_drops_unknown_experience_and_truncates():
    data, _ = waitlist.clean_signup(
        {
            "email": "a@b.co",
            "consent": "on",
            "experience": "bogus",
            "utm_source": "s" * 500,
            "commissioner_note": "n" * 500,
        }
    )
    assert data.experience is None
    assert len(data.utm_source) == 100
    assert len(data.commissioner_note) == 300


def test_rate_limiter_window():
    limiter = RateLimiter(max_events=2, window_seconds=10)
    assert limiter.allow("ip", now=0)
    assert limiter.allow("ip", now=1)
    assert not limiter.allow("ip", now=2)
    assert limiter.allow("other", now=2)
    assert limiter.allow("ip", now=11)


@pytest.fixture
def client():
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM waitlist_signup"))
    return TestClient(web_app.create_app(), follow_redirects=False)


def count() -> int:
    with Session(get_engine()) as s:
        return s.execute(text("SELECT count(*) FROM waitlist_signup")).scalar_one()


@pytest.mark.db
def test_signup_stores_row_and_redirects(client):
    resp = client.post(
        "/signup",
        data={
            "email": "Fan@Example.com",
            "consent": "on",
            "utm_source": "tiktok",
            "commissioner_note": "buddy@example.com",
        },
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/thanks"
    with Session(get_engine()) as s:
        row = s.execute(
            text("SELECT email, utm_source, commissioner_note, consented_at FROM waitlist_signup")
        ).one()
    assert row.email == "fan@example.com"
    assert row.utm_source == "tiktok"
    assert row.commissioner_note == "buddy@example.com"
    assert row.consented_at is not None


@pytest.mark.db
def test_duplicate_signup_is_a_quiet_noop(client):
    form = {"email": "fan@example.com", "consent": "on"}
    assert client.post("/signup", data=form).status_code == 303
    assert client.post("/signup", data={**form, "email": "FAN@example.com"}).status_code == 303
    assert count() == 1


@pytest.mark.db
def test_signup_triggers_alert_only_for_new_signups(client, monkeypatch):
    calls = []
    monkeypatch.setattr(
        notify,
        "send_signup_alert",
        lambda settings, email, first_name, **_: calls.append(email) or True,
    )
    form = {"email": "fan@example.com", "consent": "on"}
    client.post("/signup", data=form)
    assert calls == ["fan@example.com"]
    client.post("/signup", data={**form, "email": "FAN@example.com"})
    assert calls == ["fan@example.com"]  # duplicate did not trigger a second alert


@pytest.mark.db
def test_invalid_signup_rerenders_with_errors_and_stores_nothing(client):
    resp = client.post("/signup", data={"email": "nope"})
    assert resp.status_code == 422
    assert "Please enter a valid email address." in resp.text
    assert "Please tick the box" in resp.text
    assert count() == 0


@pytest.mark.db
def test_honeypot_signup_is_dropped_silently(client):
    resp = client.post(
        "/signup", data={"email": "bot@example.com", "consent": "on", "website": "http://spam"}
    )
    assert resp.status_code == 303
    assert count() == 0


@pytest.mark.db
def test_signup_is_rate_limited(client):
    for i in range(5):
        client.post("/signup", data={"email": f"p{i}@example.com", "consent": "on"})
    resp = client.post("/signup", data={"email": "p6@example.com", "consent": "on"})
    assert resp.status_code == 429
    assert count() == 5


@pytest.mark.db
def test_oversized_body_rejected(client):
    resp = client.post("/signup", content=b"email=" + b"a" * 9000)
    assert resp.status_code == 413
