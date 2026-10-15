from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from undertow.models import AuthSession
from undertow.services import auth


def test_password_hash_roundtrip_and_salting():
    h1 = auth.hash_password("correct horse battery")
    h2 = auth.hash_password("correct horse battery")
    assert h1 != h2  # salted
    assert h1.startswith("$argon2")
    assert auth.verify_password(h1, "correct horse battery")
    assert not auth.verify_password(h1, "wrong password!!")
    assert not auth.verify_password("not-a-hash", "anything")


@pytest.mark.parametrize(
    ("password", "ok"),
    [("short", False), ("x" * 9, False), ("x" * 10, True), ("x" * 128, True), ("x" * 129, False)],
)
def test_password_length_limits(password, ok):
    assert (auth.validate_password(password) is None) is ok


def test_password_cannot_be_the_email():
    assert auth.validate_password("Fan@Example.com", "fan@example.com")


@pytest.mark.parametrize("email", ["", "nope", "a@b", "a b@c.com", "x" * 250 + "@a.com"])
def test_bad_emails(email):
    assert auth.validate_email(auth.normalize_email(email))


def test_email_is_normalized():
    assert auth.normalize_email("  Fan@Example.COM ") == "fan@example.com"


@pytest.fixture
def db(fantasy_db):
    from sqlalchemy.orm import Session

    with Session(fantasy_db) as s:
        yield s


def register(db, email="fan@example.com", password="a long enough password"):
    return auth.register_user(db, email=email, display_name="Sam", password=password)


@pytest.mark.db
def test_register_stores_a_hash_not_the_password(db):
    result = register(db, email=" Fan@Example.com ")
    assert result.user.email == "fan@example.com"
    assert "a long enough password" not in result.user.password_hash
    assert result.user.is_admin is False


@pytest.mark.db
def test_register_reports_field_errors(db):
    result = auth.register_user(db, email="nope", display_name=" ", password="short")
    assert result.user is None
    assert set(result.errors) == {"email", "display_name", "password"}


@pytest.mark.db
def test_duplicate_email_is_refused_case_insensitively(db):
    assert register(db).user is not None
    again = register(db, email="FAN@example.com")
    assert again.user is None
    assert "already exists" in again.errors["email"]


@pytest.mark.db
def test_authenticate(db):
    register(db)
    assert auth.authenticate(db, "FAN@example.com", "a long enough password") is not None
    assert auth.authenticate(db, "fan@example.com", "wrong wrong wrong") is None
    assert auth.authenticate(db, "nobody@example.com", "a long enough password") is None


@pytest.mark.db
def test_disabled_account_cannot_log_in_or_use_a_session(db):
    user = register(db).user
    token = auth.create_session(db, user)
    user.disabled_at = datetime.now(UTC)
    db.commit()
    assert auth.authenticate(db, user.email, "a long enough password") is None
    assert auth.user_for_token(db, token) is None


@pytest.mark.db
def test_session_stores_only_a_hash_and_resolves_to_the_user(db):
    user = register(db).user
    token = auth.create_session(db, user)
    stored = db.scalar(select(AuthSession.token_hash))
    assert stored != token
    assert len(stored) == 64
    assert auth.user_for_token(db, token).id == user.id
    assert auth.user_for_token(db, "") is None
    assert auth.user_for_token(db, "garbage") is None


@pytest.mark.db
def test_session_expires(db):
    user = register(db).user
    now = datetime.now(UTC)
    token = auth.create_session(db, user, now=now)
    later = now + timedelta(days=auth.SESSION_DAYS, seconds=1)
    assert auth.user_for_token(db, token, now=now + timedelta(days=1)) is not None
    assert auth.user_for_token(db, token, now=later) is None


@pytest.mark.db
def test_logout_and_logout_everywhere(db):
    user = register(db).user
    t1, t2 = auth.create_session(db, user), auth.create_session(db, user)
    auth.end_session(db, t1)
    assert auth.user_for_token(db, t1) is None
    assert auth.user_for_token(db, t2) is not None
    auth.end_all_sessions(db, user.id)
    assert auth.user_for_token(db, t2) is None
