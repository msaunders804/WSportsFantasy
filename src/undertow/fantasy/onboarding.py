"""Turning waitlist signups into accounts, and sending set-password links.

Used by the signup form (one person at a time) and by the command line (everyone already on the
list):

    uv run python -m undertow.fantasy.onboarding            # dry run: who would get an account
    uv run python -m undertow.fantasy.onboarding --send     # create accounts and email the links
"""

import argparse
import logging
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from undertow import notify
from undertow.config import Settings, get_settings
from undertow.data.models import User, WaitlistSignup
from undertow.data.session import session_factory
from undertow.fantasy import auth, key_dates

logger = logging.getLogger(__name__)

# Resend allows a couple of requests a second, so space out a bulk send.
SEND_DELAY_SECONDS = 0.6


def welcome_signup(
    session: Session, settings: Settings, *, email: str, first_name: str | None
) -> bool:
    """Give a waitlist signup an account and email them a link to set a password.

    Returns True when a new account was made. False means the email already had an account, so
    nothing is sent (no repeat emails, and nothing to learn about who has registered).
    """
    user = auth.create_pending_account(session, email=email, first_name=first_name)
    if user is None:
        return False
    token = auth.issue_token(session, user, auth.WELCOME_LINK_TTL)
    notify.send_welcome_email(
        settings,
        user.email,
        first_name,
        token,
        days=auth.WELCOME_LINK_TTL.days,
        invites_from=key_dates.DATES["invites"].when,
        draft_window=key_dates.DATES["draft"].when,
    )
    return True


def send_password_link(session: Session, settings: Settings, email: str) -> bool:
    """Email a fresh set-password link if this email has an enabled account.

    Callers must answer the same way whether or not it did, so a visitor cannot use this to
    find out who has an account.
    """
    user = session.scalar(select(User).where(User.email == auth.normalize_email(email)))
    if user is None or user.disabled_at is not None:
        return False
    token = auth.issue_token(session, user, auth.RESET_LINK_TTL)
    first = user.display_name.split()[0] if user.display_name else None
    notify.send_password_reset_email(
        settings, user.email, first, token, hours=int(auth.RESET_LINK_TTL.total_seconds() // 3600)
    )
    return True


def waitlist_without_accounts(session: Session) -> list[WaitlistSignup]:
    taken = select(User.email)
    return list(
        session.scalars(
            select(WaitlistSignup)
            .where(WaitlistSignup.email.not_in(taken))
            .order_by(WaitlistSignup.created_at)
        )
    )


def backfill(session: Session, settings: Settings, *, send: bool) -> dict[str, int]:
    """Create accounts for everyone on the waitlist who does not have one."""
    pending = waitlist_without_accounts(session)
    result = {"waiting": len(pending), "created": 0}
    if not send:
        return result
    for signup in pending:
        if welcome_signup(session, settings, email=signup.email, first_name=signup.first_name):
            result["created"] += 1
            time.sleep(SEND_DELAY_SECONDS)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--send", action="store_true", help="create the accounts and email the links"
    )
    args = parser.parse_args()
    settings = get_settings()
    if args.send and not settings.email_api_key:
        parser.error("EMAIL_API_KEY is not set, so no links would be delivered. Set it first.")
    with session_factory()() as session:
        if not args.send:
            for signup in waitlist_without_accounts(session):
                print(signup.email)
        result = backfill(session, settings, send=args.send)
    verb = "created and emailed" if args.send else "would get an account (dry run)"
    count = result["created"] if args.send else result["waiting"]
    print(f"{count} of {result['waiting']} waitlist signups {verb}.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
