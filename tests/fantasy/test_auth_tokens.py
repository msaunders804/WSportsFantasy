from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from undertow.data.models import AuthToken, User
from undertow.fantasy import auth

pytestmark = pytest.mark.db


@pytest.fixture
def db(fantasy_db):
    with Session(fantasy_db) as s:
        yield s


def pending(db, email="fan@example.com", first_name="Sam"):
    return auth.create_pending_account(db, email=email, first_name=first_name)


def test_pending_account_has_no_password_and_a_friendly_name(db):
    user = pending(db, email=" Fan@Example.com ", first_name="  Sam  Smith ")
    assert user.email == "fan@example.com"
    assert user.display_name == "Sam Smith"
    assert user.password_hash is None
    assert pending(db, email="x@example.com", first_name=None).display_name == "x"


def test_pending_account_is_none_for_existing_or_invalid_email(db):
    assert pending(db) is not None
    assert pending(db, email="FAN@example.com") is None
    assert pending(db, email="nope") is None


def test_nobody_can_log_in_to_an_account_without_a_password(db):
    pending(db)
    for guess in ["", "anything at all", "None", "null"]:
        assert auth.authenticate(db, "fan@example.com", guess) is None


def test_token_is_stored_hashed(db):
    user = pending(db)
    token = auth.issue_token(db, user, auth.WELCOME_LINK_TTL)
    stored = db.scalar(select(AuthToken.token_hash))
    assert stored != token and len(stored) == 64
    assert auth.token_is_valid(db, token)
    assert not auth.token_is_valid(db, "")
    assert not auth.token_is_valid(db, "garbage")


def test_set_password_logs_the_person_in_and_spends_the_token(db):
    user = pending(db)
    token = auth.issue_token(db, user, auth.WELCOME_LINK_TTL)
    got, problem = auth.set_password_with_token(db, token, "a long enough password")
    assert problem is None and got.id == user.id
    assert auth.authenticate(db, "fan@example.com", "a long enough password") is not None
    # Single use.
    again, problem = auth.set_password_with_token(db, token, "another long password")
    assert again is None and problem == auth.LINK_EXPIRED
    assert not auth.token_is_valid(db, token)


def test_weak_password_is_refused_without_spending_the_token(db):
    user = pending(db)
    token = auth.issue_token(db, user, auth.WELCOME_LINK_TTL)
    got, problem = auth.set_password_with_token(db, token, "short")
    assert got is None and "at least" in problem
    assert auth.token_is_valid(db, token)  # can try again
    assert db.get(User, user.id).password_hash is None


def test_password_equal_to_email_is_refused(db):
    user = pending(db)
    token = auth.issue_token(db, user, auth.WELCOME_LINK_TTL)
    got, problem = auth.set_password_with_token(db, token, "fan@example.com")
    assert got is None and "email" in problem


def test_token_expires(db):
    user = pending(db)
    now = datetime.now(UTC)
    token = auth.issue_token(db, user, timedelta(hours=2), now=now)
    assert auth.token_is_valid(db, token, now=now + timedelta(hours=1, minutes=59))
    assert not auth.token_is_valid(db, token, now=now + timedelta(hours=2, seconds=1))
    got, problem = auth.set_password_with_token(
        db, token, "a long enough password", now=now + timedelta(hours=3)
    )
    assert got is None and problem == auth.LINK_EXPIRED


def test_a_newer_link_replaces_the_older_one(db):
    user = pending(db)
    old = auth.issue_token(db, user, auth.WELCOME_LINK_TTL)
    new = auth.issue_token(db, user, auth.RESET_LINK_TTL)
    assert not auth.token_is_valid(db, old)
    assert auth.token_is_valid(db, new)


def test_links_are_per_person(db):
    a, b = pending(db), pending(db, email="b@example.com")
    token_a = auth.issue_token(db, a, auth.WELCOME_LINK_TTL)
    auth.issue_token(db, b, auth.WELCOME_LINK_TTL)
    assert auth.token_is_valid(db, token_a)  # issuing b's link did not touch a's


def test_resetting_a_password_signs_out_every_session(db):
    user = auth.register_user(
        db, email="fan@example.com", display_name="Sam", password="the old password!"
    ).user
    session_token = auth.create_session(db, user)
    link = auth.issue_token(db, user, auth.RESET_LINK_TTL)
    auth.set_password_with_token(db, link, "a brand new password")
    assert auth.user_for_token(db, session_token) is None
    assert auth.authenticate(db, "fan@example.com", "the old password!") is None
    assert auth.authenticate(db, "fan@example.com", "a brand new password") is not None


def test_disabled_account_cannot_use_a_link(db):
    user = pending(db)
    token = auth.issue_token(db, user, auth.WELCOME_LINK_TTL)
    user.disabled_at = datetime.now(UTC)
    db.commit()
    got, problem = auth.set_password_with_token(db, token, "a long enough password")
    assert got is None and problem == auth.LINK_EXPIRED


def test_two_requests_cannot_both_spend_one_link(fantasy_db):
    import threading

    with Session(fantasy_db) as s:
        user = pending(s)
        token = auth.issue_token(s, user, auth.WELCOME_LINK_TTL)
    outcomes: list[bool] = []

    def attempt(password: str) -> None:
        with Session(fantasy_db) as s:
            got, _ = auth.set_password_with_token(s, token, password)
            outcomes.append(got is not None)

    threads = [threading.Thread(target=attempt, args=(f"password number {i}",)) for i in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert outcomes.count(True) == 1
