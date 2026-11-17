from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from undertow.data.models import AuditLog
from undertow.fantasy import auth, staff

pytestmark = pytest.mark.db


@pytest.fixture
def db(fantasy_db):
    with Session(fantasy_db) as s:
        yield s


def make_user(db, email, admin=False):
    user = auth.register_user(
        db, email=email, display_name=email.split("@")[0], password="a long enough password"
    ).user
    user.is_admin = admin
    db.commit()
    return user


def test_dry_run_changes_nothing(db):
    user = make_user(db, "me@example.com")
    result = staff.set_staff(db, "me@example.com")
    assert result.applied is False and result.changed is True
    assert result.now_staff is True and result.was_staff is False  # what it would become
    db.refresh(user)
    assert user.is_admin is False
    assert db.scalar(select(AuditLog)) is None


def test_granting_staff_sets_the_flag_and_writes_an_audit_entry(db):
    user = make_user(db, "me@example.com")
    result = staff.set_staff(db, "me@example.com", apply=True)
    assert result.applied and result.changed and result.now_staff
    db.refresh(user)
    assert user.is_admin is True
    entry = db.scalar(select(AuditLog))
    assert entry.action == "staff.grant" and entry.league_id is None and entry.actor_id is None
    assert entry.before == {"email": "me@example.com", "is_admin": False}
    assert entry.after["is_admin"] is True and entry.after["via"] == "command line"


def test_email_is_matched_ignoring_case_and_spaces(db):
    user = make_user(db, "me@example.com")
    staff.set_staff(db, "  Me@Example.COM ", apply=True)
    db.refresh(user)
    assert user.is_admin is True


def test_granting_twice_is_a_quiet_no_op(db):
    make_user(db, "me@example.com", admin=True)
    result = staff.set_staff(db, "me@example.com", apply=True)
    assert result.changed is False and result.applied is False
    assert db.scalar(select(AuditLog)) is None  # nothing changed, nothing logged


def test_unknown_email_and_disabled_accounts_are_refused(db):
    with pytest.raises(staff.StaffError, match="register first"):
        staff.set_staff(db, "nobody@example.com", apply=True)
    user = make_user(db, "off@example.com")
    user.disabled_at = datetime.now(UTC)
    db.commit()
    with pytest.raises(staff.StaffError, match="disabled"):
        staff.set_staff(db, "off@example.com", apply=True)
    assert user.is_admin is False


def test_removing_staff_works_but_never_removes_the_last_one(db):
    first = make_user(db, "a@example.com", admin=True)
    second = make_user(db, "b@example.com", admin=True)
    staff.set_staff(db, "a@example.com", make_staff=False, apply=True)
    db.refresh(first)
    assert first.is_admin is False
    assert db.scalar(select(AuditLog)).action == "staff.revoke"
    with pytest.raises(staff.StaffError, match="only staff account"):
        staff.set_staff(db, "b@example.com", make_staff=False, apply=True)
    db.refresh(second)
    assert second.is_admin is True


def test_a_note_warns_when_the_account_has_no_password_yet(db):
    user = make_user(db, "new@example.com")
    user.password_hash = None
    db.commit()
    assert "not set a password" in staff.set_staff(db, "new@example.com").note
