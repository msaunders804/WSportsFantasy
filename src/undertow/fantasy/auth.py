"""Accounts and sessions: password hashing (argon2), registration, login, session tokens.

No HTTP code here. The web layer sets the cookie; this module only deals in raw tokens and rows.
Session tokens are random and only their SHA-256 is stored, so a leaked database cannot be used
to log in.
"""

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from undertow.data.models import AuthSession, AuthToken, User

SESSION_DAYS = 30
# Only write last_seen_at when it is older than this, so a page view is not a database write.
TOUCH_AFTER = timedelta(minutes=10)
MIN_PASSWORD = 10
MAX_PASSWORD = 128
MAX_DISPLAY_NAME = 60
# Link in the welcome email (waitlist signups can take a while to get round to it), and the
# shorter-lived link for a forgotten password.
WELCOME_LINK_TTL = timedelta(days=14)
RESET_LINK_TTL = timedelta(hours=2)
SET_PASSWORD = "set_password"
LINK_EXPIRED = "This link has expired or was already used."

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_hasher = PasswordHasher()
# Verified against when the email is unknown, so a missing account takes as long as a wrong
# password and login timing does not reveal which emails are registered.
_DUMMY_HASH = _hasher.hash("not-a-real-password")


def normalize_email(value: str) -> str:
    return value.strip().lower()


def validate_email(email: str) -> str | None:
    if not email or len(email) > 254 or not _EMAIL.match(email):
        return "Enter a valid email address."
    return None


def validate_password(password: str, email: str = "") -> str | None:
    if len(password) < MIN_PASSWORD:
        return f"Use at least {MIN_PASSWORD} characters."
    if len(password) > MAX_PASSWORD:
        return f"Use at most {MAX_PASSWORD} characters."
    if email and password.strip().lower() == email.lower():
        return "Your password cannot be your email address."
    return None


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


@dataclass(frozen=True)
class RegistrationResult:
    user: User | None
    errors: dict[str, str]


def register_user(
    session: Session, *, email: str, display_name: str, password: str, timezone: str = "UTC"
) -> RegistrationResult:
    """Create an account. Returns field errors instead of raising for anything a person can fix."""
    email = normalize_email(email)
    display_name = " ".join(display_name.split())
    errors: dict[str, str] = {}
    if msg := validate_email(email):
        errors["email"] = msg
    if not display_name or len(display_name) > MAX_DISPLAY_NAME:
        errors["display_name"] = f"Enter a name up to {MAX_DISPLAY_NAME} characters."
    if msg := validate_password(password, email):
        errors["password"] = msg
    if errors:
        return RegistrationResult(None, errors)

    user = User(
        email=email,
        display_name=display_name,
        password_hash=hash_password(password),
        timezone=timezone or "UTC",
    )
    session.add(user)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return RegistrationResult(
            None,
            {
                "email": "An account with that email already exists. Log in, or choose "
                "'Forgot your password' if you haven't set one yet."
            },
        )
    return RegistrationResult(user, {})


def authenticate(session: Session, email: str, password: str) -> User | None:
    """Return the user when the email and password match and the account is not disabled."""
    user = session.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None or user.password_hash is None:
        # Unknown email, or an account that has not set a password yet: take as long as a wrong
        # password so timing does not reveal which emails have accounts.
        verify_password(_DUMMY_HASH, password)
        return None
    if not verify_password(user.password_hash, password) or user.disabled_at is not None:
        return None
    try:
        needs_rehash = _hasher.check_needs_rehash(user.password_hash)
    except InvalidHashError:
        needs_rehash = False
    if needs_rehash:
        user.password_hash = hash_password(password)
    user.last_login_at = datetime.now(UTC)
    session.commit()
    return user


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(session: Session, user: User, now: datetime | None = None) -> str:
    """Start a login session and return the raw token for the cookie."""
    now = now or datetime.now(UTC)
    token = secrets.token_urlsafe(32)
    session.add(
        AuthSession(
            user_id=user.id,
            token_hash=_digest(token),
            created_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(days=SESSION_DAYS),
        )
    )
    session.commit()
    return token


def user_for_token(session: Session, token: str, now: datetime | None = None) -> User | None:
    """The user a session cookie belongs to, or None if it is unknown, expired or disabled."""
    if not token:
        return None
    now = now or datetime.now(UTC)
    row = session.execute(
        select(AuthSession, User)
        .join(User, User.id == AuthSession.user_id)
        .where(AuthSession.token_hash == _digest(token))
    ).first()
    if row is None:
        return None
    auth_session, user = row
    if auth_session.expires_at <= now or user.disabled_at is not None:
        return None
    if now - auth_session.last_seen_at > TOUCH_AFTER:
        session.execute(
            update(AuthSession).where(AuthSession.id == auth_session.id).values(last_seen_at=now)
        )
        session.commit()
    return user


def end_session(session: Session, token: str) -> None:
    session.execute(delete(AuthSession).where(AuthSession.token_hash == _digest(token)))
    session.commit()


def end_all_sessions(session: Session, user_id: int) -> None:
    session.execute(delete(AuthSession).where(AuthSession.user_id == user_id))
    session.commit()


def display_name_from(first_name: str | None, email: str) -> str:
    """A name for an account made without one: the first name, else the part before the @."""
    name = " ".join((first_name or "").split()) or email.split("@")[0]
    return name[:MAX_DISPLAY_NAME]


def create_pending_account(
    session: Session, *, email: str, first_name: str | None = None
) -> User | None:
    """Make an account with no password yet. None if the email already has an account.

    The person sets a password from an emailed link (`issue_token`). Until then nobody can log in
    to it, because `password_hash` is empty.
    """
    email = normalize_email(email)
    if validate_email(email):
        return None
    user = User(email=email, display_name=display_name_from(first_name, email), password_hash=None)
    session.add(user)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return None
    return user


def issue_token(
    session: Session,
    user: User,
    ttl: timedelta,
    now: datetime | None = None,
) -> str:
    """Create a set-password link token and return the raw value for the email.

    Any earlier unused link for this person stops working, so only the newest email is valid.
    """
    now = now or datetime.now(UTC)
    session.execute(
        update(AuthToken)
        .where(
            AuthToken.user_id == user.id,
            AuthToken.purpose == SET_PASSWORD,
            AuthToken.used_at.is_(None),
        )
        .values(used_at=now)
    )
    token = secrets.token_urlsafe(32)
    session.add(
        AuthToken(
            user_id=user.id,
            purpose=SET_PASSWORD,
            token_hash=_digest(token),
            created_at=now,
            expires_at=now + ttl,
        )
    )
    session.commit()
    return token


def token_is_valid(session: Session, token: str, now: datetime | None = None) -> bool:
    """Whether a link token can still be used. Does not use it up."""
    if not token:
        return False
    now = now or datetime.now(UTC)
    row = session.scalar(select(AuthToken).where(AuthToken.token_hash == _digest(token)))
    return row is not None and row.used_at is None and row.expires_at > now


def set_password_with_token(
    session: Session, token: str, password: str, now: datetime | None = None
) -> tuple[User | None, str | None]:
    """Use a link token to set a password. Returns (user, None) or (None, reason to show).

    A weak password is refused WITHOUT using the token up, so the person can try again. A valid
    token is spent in the same statement that claims it, so two requests cannot both succeed.
    Every existing login session for the account ends, so a reset also signs out anyone who had
    got in with the old password.
    """
    now = now or datetime.now(UTC)
    if not token_is_valid(session, token, now):
        return None, LINK_EXPIRED
    row = session.scalar(select(AuthToken).where(AuthToken.token_hash == _digest(token)))
    user = session.get(User, row.user_id)
    if user is None or user.disabled_at is not None:
        return None, LINK_EXPIRED
    if msg := validate_password(password, user.email):
        return None, msg
    claimed = session.execute(
        update(AuthToken)
        .where(AuthToken.id == row.id, AuthToken.used_at.is_(None))
        .values(used_at=now)
    )
    if claimed.rowcount != 1:
        session.rollback()
        return None, LINK_EXPIRED
    user.password_hash = hash_password(password)
    session.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    session.commit()
    return user, None
