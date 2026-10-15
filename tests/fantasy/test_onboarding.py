import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from undertow import notify
from undertow.config import Settings
from undertow.data.models import AuthToken, User, WaitlistSignup
from undertow.fantasy import auth, onboarding

pytestmark = pytest.mark.db


@pytest.fixture
def db(fantasy_db):
    with Session(fantasy_db) as s:
        yield s


@pytest.fixture
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr(
        notify,
        "send_welcome_email",
        lambda settings, to, first, token, days, **_: sent.append(("welcome", to, first, token)),
    )
    monkeypatch.setattr(
        notify,
        "send_password_reset_email",
        lambda settings, to, first, token, hours, **_: sent.append(("reset", to, first, token)),
    )
    monkeypatch.setattr(onboarding, "SEND_DELAY_SECONDS", 0)
    return sent


SETTINGS = Settings(email_api_key="k")


def add_signup(db, email, first_name=None):
    from datetime import UTC, datetime

    db.add(WaitlistSignup(email=email, first_name=first_name, consented_at=datetime.now(UTC)))
    db.commit()


def test_welcome_creates_an_account_and_sends_one_email(db, outbox):
    assert onboarding.welcome_signup(db, SETTINGS, email="fan@example.com", first_name="Sam")
    kind, to, first, token = outbox[0]
    assert (kind, to, first) == ("welcome", "fan@example.com", "Sam")
    assert auth.token_is_valid(db, token)
    assert db.scalar(select(User.display_name)) == "Sam"


def test_welcome_does_nothing_for_an_existing_account(db, outbox):
    onboarding.welcome_signup(db, SETTINGS, email="fan@example.com", first_name="Sam")
    outbox.clear()
    assert not onboarding.welcome_signup(db, SETTINGS, email="FAN@example.com", first_name="Sam")
    assert outbox == []
    assert db.scalar(select(func.count()).select_from(User)) == 1


def test_welcome_does_not_email_someone_who_registered_themselves(db, outbox):
    auth.register_user(db, email="fan@example.com", display_name="Sam", password="a long password!")
    assert not onboarding.welcome_signup(db, SETTINGS, email="fan@example.com", first_name="Sam")
    assert outbox == []


def test_password_link_goes_only_to_real_enabled_accounts(db, outbox):
    auth.register_user(
        db, email="fan@example.com", display_name="Sam Smith", password="a long password!"
    )
    assert onboarding.send_password_link(db, SETTINGS, " FAN@example.com ")
    assert outbox[0][:3] == ("reset", "fan@example.com", "Sam")
    outbox.clear()
    assert not onboarding.send_password_link(db, SETTINGS, "nobody@example.com")
    user = db.scalar(select(User))
    from datetime import UTC, datetime

    user.disabled_at = datetime.now(UTC)
    db.commit()
    assert not onboarding.send_password_link(db, SETTINGS, "fan@example.com")
    assert outbox == []


def test_backfill_dry_run_changes_nothing(db, outbox):
    add_signup(db, "a@example.com", "Ann")
    add_signup(db, "b@example.com")
    result = onboarding.backfill(db, SETTINGS, send=False)
    assert result == {"waiting": 2, "created": 0}
    assert outbox == []
    assert db.scalar(select(func.count()).select_from(User)) == 0


def test_backfill_creates_accounts_once_and_skips_existing(db, outbox):
    add_signup(db, "a@example.com", "Ann")
    add_signup(db, "b@example.com")
    add_signup(db, "c@example.com")
    auth.register_user(db, email="c@example.com", display_name="Cee", password="a long password!")
    result = onboarding.backfill(db, SETTINGS, send=True)
    assert result == {"waiting": 2, "created": 2}
    assert sorted(m[1] for m in outbox) == ["a@example.com", "b@example.com"]
    # Running it again finds nobody left, so nobody is emailed twice.
    outbox.clear()
    assert onboarding.backfill(db, SETTINGS, send=True) == {"waiting": 0, "created": 0}
    assert outbox == []
    assert db.scalar(select(func.count()).select_from(AuthToken)) == 2
