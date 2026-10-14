"""Landing-page signups: validate the form input and store it. No HTTP code here."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from undertow.models import WaitlistSignup

EXPERIENCE_CHOICES = {
    "new": "I'm brand new to fantasy",
    "some": "I've played a little",
    "lots": "I've played plenty",
}

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MAX = {
    "first_name": 100,
    "commissioner_note": 300,
    "utm_source": 100,
    "utm_medium": 100,
    "utm_campaign": 100,
}


@dataclass(frozen=True)
class SignupData:
    email: str
    first_name: str | None
    experience: str | None
    commissioner_note: str | None
    utm_source: str | None
    utm_medium: str | None
    utm_campaign: str | None


def clean_signup(form: Mapping[str, str]) -> tuple[SignupData | None, dict[str, str]]:
    """Validate raw form fields. Returns the cleaned data, or None plus field errors."""
    errors: dict[str, str] = {}
    email = form.get("email", "").strip().lower()
    if not email or len(email) > 254 or not _EMAIL.match(email):
        errors["email"] = "Please enter a valid email address."
    if form.get("consent") != "on":
        errors["consent"] = "Please tick the box so we know it's OK to email you."
    experience = form.get("experience", "").strip() or None
    if experience is not None and experience not in EXPERIENCE_CHOICES:
        experience = None

    def opt(name: str) -> str | None:
        value = form.get(name, "").strip()
        return value[: _MAX[name]] or None

    if errors:
        return None, errors
    return (
        SignupData(
            email=email,
            first_name=opt("first_name"),
            experience=experience,
            commissioner_note=opt("commissioner_note"),
            utm_source=opt("utm_source"),
            utm_medium=opt("utm_medium"),
            utm_campaign=opt("utm_campaign"),
        ),
        {},
    )


def add_signup(session: Session, data: SignupData) -> bool:
    """Store a signup. Returns False (and changes nothing) if the email is already on the list."""
    stmt = (
        insert(WaitlistSignup)
        .values(**data.__dict__, consented_at=datetime.now(UTC))
        .on_conflict_do_nothing(index_elements=["email"])
        .returning(WaitlistSignup.id)
    )
    created = session.execute(stmt).scalar_one_or_none() is not None
    session.commit()
    return created
