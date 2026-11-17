"""Grant or remove Undertow staff access (`user_account.is_admin`) from the command line.

Staff can create house leagues, assign commissioners and open the staff overview, so this is
deliberately not something a web page can do yet. A dry run is the default; nothing changes
until `--yes` is given, and every change writes an audit-log entry.

    uv run python -m undertow.fantasy.staff you@example.com            # dry run
    uv run python -m undertow.fantasy.staff you@example.com --yes      # make them staff
    uv run python -m undertow.fantasy.staff you@example.com --remove --yes

On Railway: `railway run python -m undertow.fantasy.staff you@example.com --yes`.
"""

import argparse
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from undertow.config import get_settings
from undertow.data.models import AuditLog, User
from undertow.data.session import session_factory
from undertow.fantasy import auth


class StaffError(Exception):
    """The change was refused. The message says why."""


@dataclass(frozen=True)
class StaffChange:
    email: str
    display_name: str
    was_staff: bool
    now_staff: bool  # the state asked for (what it is, or would be, after the change)
    applied: bool  # True only if the database was changed
    note: str = ""

    @property
    def changed(self) -> bool:
        return self.was_staff != self.now_staff


def set_staff(
    session: Session, email: str, *, make_staff: bool = True, apply: bool = False
) -> StaffChange:
    """Make an existing account staff (or remove it). Does nothing unless `apply` is true."""
    email = auth.normalize_email(email)
    user = session.scalar(select(User).where(User.email == email))
    if user is None:
        raise StaffError(f"No account has the email {email}. They need to register first.")
    if make_staff and user.disabled_at is not None:
        raise StaffError(f"{email} is a disabled account, so it cannot be made staff.")
    was = bool(user.is_admin)
    if not make_staff and was:
        others = session.scalar(
            select(func.count()).select_from(User).where(User.is_admin, User.id != user.id)
        )
        if not others:
            raise StaffError(
                "That is the only staff account. Make someone else staff first, so there is "
                "always someone who can run the site."
            )
    note = "" if user.password_hash else "This account has not set a password yet. "
    if not apply or was == make_staff:
        return StaffChange(email, user.display_name, was, make_staff, False, note)
    user.is_admin = make_staff
    session.add(
        AuditLog(
            league_id=None,
            actor_id=None,
            action="staff.grant" if make_staff else "staff.revoke",
            before={"email": email, "is_admin": was},
            after={"email": email, "is_admin": make_staff, "via": "command line"},
        )
    )
    session.commit()
    return StaffChange(email, user.display_name, was, make_staff, True, note)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("email", help="the account's email address")
    parser.add_argument("--remove", action="store_true", help="remove staff access instead")
    parser.add_argument("--yes", action="store_true", help="make the change (default is a dry run)")
    args = parser.parse_args()
    # Which database this is about to change, without the password.
    host = make_url(get_settings().database_url).host
    print(f"Database host: {host}")
    with session_factory()() as session:
        try:
            result = set_staff(session, args.email, make_staff=not args.remove, apply=args.yes)
        except StaffError as exc:
            raise SystemExit(f"Not changed: {exc}") from exc
    target = "staff" if result.now_staff else "not staff"
    who = f"{result.email} ({result.display_name})"
    if result.note:
        print(result.note.strip())
    if not result.changed:
        print(f"{who} is already {target}. Nothing to do.")
    elif result.applied:
        print(f"Done: {who} is now {target}.")
    else:
        print(f"Dry run: {who} would become {target}. Add --yes to make the change.")


if __name__ == "__main__":
    main()
