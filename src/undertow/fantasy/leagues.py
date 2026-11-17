"""Leagues, membership and teams. Business rules only, no HTTP.

Joining a league is one transaction: the league row is locked, the team and member rows are
inserted together, and database constraints (one team per person per league, unique team name,
one commissioner) back up the checks, so two people joining at once cannot overfill a league.
"""

import secrets
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from undertow.data.models import (
    AuditLog,
    FantasyTeam,
    League,
    LeagueInvite,
    LeagueMember,
    LeagueSettings,
    Player,
    User,
)
from undertow.data.models.fantasy import LEAGUE_KINDS
from undertow.fantasy import auth, league_rules
from undertow.fantasy.league_rules import LeagueSettingsData

MAX_LEAGUE_NAME = 80
MAX_TEAM_NAME = 40
# Anyone can register a private league, so the amount of it one person can do is capped.
MAX_PRIVATE_LEAGUES_PER_USER = 2
# Emailed invites per league: a few times the league size, so a typo'd list cannot be a mail cannon.
INVITES_PER_SEAT = 3


class LeagueError(Exception):
    """A rule was broken. The message is safe to show to the person."""


class PermissionDenied(LeagueError):
    pass


def active_player_pool(session: Session) -> int:
    """Rosterable players in the real league, used for the pool-size warning."""
    return (
        session.scalar(select(func.count()).select_from(Player).where(Player.status == "active"))
        or 0
    )


def _audit(session, *, league_id, actor_id, action, before=None, after=None) -> None:
    session.add(
        AuditLog(league_id=league_id, actor_id=actor_id, action=action, before=before, after=after)
    )


def membership(session: Session, user_id: int, league_id: int) -> LeagueMember | None:
    return session.scalar(
        select(LeagueMember).where(
            LeagueMember.user_id == user_id, LeagueMember.league_id == league_id
        )
    )


def can_manage(session: Session, user: User, league_id: int) -> bool:
    """Undertow staff, the commissioner and the co-commissioner may manage a league."""
    if user.is_admin:
        return True
    member = membership(session, user.id, league_id)
    return member is not None and member.role in ("commissioner", "co_commissioner")


def require_member(session: Session, user: User, league_id: int) -> LeagueMember | None:
    """Raise unless the user belongs to the league. Staff may look at any league (returns None)."""
    member = membership(session, user.id, league_id)
    if member is None and not user.is_admin:
        raise PermissionDenied("You are not in this league.")
    return member


def require_manager(session: Session, user: User, league_id: int) -> None:
    if not can_manage(session, user, league_id):
        raise PermissionDenied("Only the commissioner can do that.")


def create_league(
    session: Session,
    actor: User,
    *,
    name: str,
    season_id: int,
    size: int,
    settings: LeagueSettingsData | None = None,
    kind: str = "house",
) -> tuple[League, league_rules.Validation]:
    """Create a league in `setup` status. Staff only: house leagues are run by Undertow. Members
    register their own through `register_league`."""
    if not actor.is_admin:
        raise PermissionDenied("Only Undertow staff can create leagues.")
    return _create_league(
        session, actor, name=name, season_id=season_id, size=size, settings=settings, kind=kind
    )


def _create_league(
    session: Session,
    actor: User,
    *,
    name: str,
    season_id: int,
    size: int,
    settings: LeagueSettingsData | None,
    kind: str,
    team_name: str | None = None,
) -> tuple[League, league_rules.Validation]:
    """Insert a league, its settings and audit row (and, for `team_name`, the actor as its
    commissioner with a team) in one transaction."""
    if kind not in LEAGUE_KINDS:
        raise LeagueError("Unknown league type.")
    name = league_rules.clean_name(name, max_length=MAX_LEAGUE_NAME)
    if not name:
        raise LeagueError("Give the league a name.")
    # The default of 6 playoff teams would be more than a small league has.
    settings = settings or LeagueSettingsData(playoff_teams=min(6, size))
    result = league_rules.validate_settings(
        size, settings, active_pool=active_player_pool(session) or None
    )
    if not result.ok:
        raise LeagueError(" ".join(result.errors))

    league = League(
        name=name,
        season_id=season_id,
        size=size,
        kind=kind,
        invite_code=secrets.token_urlsafe(12),
        created_by_id=actor.id,
    )
    session.add(league)
    try:
        session.flush()
        session.add(LeagueSettings(league_id=league.id, **settings.as_columns()))
        if team_name is not None:
            session.add(LeagueMember(league_id=league.id, user_id=actor.id, role="commissioner"))
            session.add(FantasyTeam(league_id=league.id, owner_id=actor.id, name=team_name))
        _audit(
            session,
            league_id=league.id,
            actor_id=actor.id,
            action="league.create",
            after={"name": name, "size": size, "kind": kind, **settings.as_columns()},
        )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise LeagueError("A league with that name already exists this season.") from exc
    return league, result


def register_league(
    session: Session,
    user: User,
    *,
    name: str,
    season_id: int,
    size: int,
    team_name: str,
) -> tuple[League, league_rules.Validation]:
    """A member registers their own private league and becomes its commissioner, with a team.

    Capped at `MAX_PRIVATE_LEAGUES_PER_USER` per person. Settings start at the defaults and stay
    editable by the commissioner until the draft starts.
    """
    team_name = league_rules.clean_name(team_name, max_length=MAX_TEAM_NAME)
    if not team_name:
        raise LeagueError("Give your team a name.")
    # Serialise a person's registrations so two quick submits cannot slip past the cap.
    session.scalar(select(User.id).where(User.id == user.id).with_for_update())
    owned = session.scalar(
        select(func.count())
        .select_from(League)
        .where(League.created_by_id == user.id, League.kind == "private")
    )
    if owned >= MAX_PRIVATE_LEAGUES_PER_USER:
        raise LeagueError(
            f"You can register up to {MAX_PRIVATE_LEAGUES_PER_USER} leagues of your own."
        )
    return _create_league(
        session,
        user,
        name=name,
        season_id=season_id,
        size=size,
        settings=None,
        kind="private",
        team_name=team_name,
    )


def join_league(session: Session, user: User, invite_code: str, team_name: str) -> FantasyTeam:
    """Add the user to the league that owns `invite_code` and create their team."""
    team_name = league_rules.clean_name(team_name, max_length=MAX_TEAM_NAME)
    if not team_name:
        raise LeagueError("Give your team a name.")
    # Lock the league row so two people joining at once see a consistent member count.
    league = session.scalar(
        select(League).where(League.invite_code == invite_code).with_for_update()
    )
    if league is None:
        raise LeagueError("That invite link is not valid.")
    if league.status != "setup":
        raise LeagueError("This league has already started, so it is closed to new teams.")
    if membership(session, user.id, league.id) is not None:
        raise LeagueError("You are already in this league.")
    members = session.scalar(
        select(func.count()).select_from(LeagueMember).where(LeagueMember.league_id == league.id)
    )
    if members >= league.size:
        raise LeagueError("This league is full.")

    invite = session.scalar(
        select(LeagueInvite).where(
            LeagueInvite.league_id == league.id, LeagueInvite.email == user.email
        )
    )
    session.add(LeagueMember(league_id=league.id, user_id=user.id, role="member"))
    team = FantasyTeam(league_id=league.id, owner_id=user.id, name=team_name)
    session.add(team)
    if invite is not None and invite.accepted_at is None:
        invite.accepted_at = datetime.now(UTC)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise LeagueError("Someone in this league already has a team with that name.") from exc
    return team


def set_role(session: Session, actor: User, league_id: int, user_id: int, role: str) -> None:
    """Make a member the commissioner or co-commissioner (or back to a plain member).

    Staff only for now. Replacing an existing commissioner is a two-step move: demote them first,
    which keeps the one-commissioner constraint honest and leaves a clear audit trail.
    """
    if not actor.is_admin:
        raise PermissionDenied("Only Undertow staff can change commissioners.")
    if role not in ("member", "commissioner", "co_commissioner"):
        raise LeagueError("Unknown role.")
    member = membership(session, user_id, league_id)
    if member is None:
        raise LeagueError("That person is not in this league.")
    before = member.role
    member.role = role
    _audit(
        session,
        league_id=league_id,
        actor_id=actor.id,
        action="league.set_role",
        before={"user_id": user_id, "role": before},
        after={"user_id": user_id, "role": role},
    )
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise LeagueError(
            "This league already has one. Change the current one to member first."
        ) from exc


def settings_data(row: LeagueSettings) -> LeagueSettingsData:
    keys = LeagueSettingsData.__dataclass_fields__
    return LeagueSettingsData(**{k: getattr(row, k) for k in keys})


def update_settings(
    session: Session, actor: User, league_id: int, changes: dict[str, int]
) -> league_rules.Validation:
    """Change integer settings. Commissioner or staff, and only before the draft starts."""
    require_manager(session, actor, league_id)
    league = session.get(League, league_id)
    row = session.get(LeagueSettings, league_id)
    if league is None or row is None:
        raise LeagueError("League not found.")
    if league.status != "setup":
        raise LeagueError("Settings are locked once the draft has started.")
    unknown = set(changes) - set(league_rules.INT_LIMITS)
    if unknown:
        raise LeagueError(f"Unknown settings: {', '.join(sorted(unknown))}.")
    before = settings_data(row)
    after = LeagueSettingsData(**{**asdict(before), **changes})
    result = league_rules.validate_settings(
        league.size, after, active_pool=active_player_pool(session) or None
    )
    if not result.ok:
        raise LeagueError(" ".join(result.errors))
    for key, value in changes.items():
        setattr(row, key, value)
    _audit(
        session,
        league_id=league_id,
        actor_id=actor.id,
        action="league.update_settings",
        before={k: getattr(before, k) for k in changes},
        after=changes,
    )
    session.commit()
    return result


def leagues_for_user(session: Session, user: User) -> list[tuple[League, FantasyTeam | None, str]]:
    """The user's leagues as (league, their team, their role), newest first."""
    rows = session.execute(
        select(League, FantasyTeam, LeagueMember.role)
        .join(LeagueMember, LeagueMember.league_id == League.id)
        .outerjoin(
            FantasyTeam,
            (FantasyTeam.league_id == League.id) & (FantasyTeam.owner_id == user.id),
        )
        .where(LeagueMember.user_id == user.id)
        .order_by(League.created_at.desc())
    ).all()
    return [(league, team, role) for league, team, role in rows]


def member_count(session: Session, league_id: int) -> int:
    return session.scalar(
        select(func.count()).select_from(LeagueMember).where(LeagueMember.league_id == league_id)
    )


def set_draft_time(
    session: Session, actor: User, league_id: int, draft_at: datetime | None
) -> League:
    """Set (or clear, with None) when a league's draft starts. Commissioner or staff, and only
    before the draft has started. The time must carry a time zone, so it is never guessed."""
    require_manager(session, actor, league_id)
    league = session.get(League, league_id)
    if league is None:
        raise LeagueError("League not found.")
    if league.status != "setup":
        raise LeagueError("The draft time is locked once the draft has started.")
    if draft_at is not None and draft_at.tzinfo is None:
        raise LeagueError("The draft time needs a time zone.")
    before = league.draft_at
    league.draft_at = draft_at
    _audit(
        session,
        league_id=league_id,
        actor_id=actor.id,
        action="league.set_draft_time",
        before={"draft_at": before.isoformat() if before else None},
        after={"draft_at": draft_at.isoformat() if draft_at else None},
    )
    session.commit()
    return league


def seats_open(session: Session, league: League) -> int:
    return max(0, league.size - member_count(session, league.id))


def is_full(session: Session, league: League) -> bool:
    return seats_open(session, league) == 0


@dataclass(frozen=True)
class InviteResult:
    """Who was newly invited (the caller emails them) and who was skipped, with the reason."""

    new: list[str]
    skipped: dict[str, str]


def parse_emails(raw: str) -> list[str]:
    """Split a pasted list (commas, semicolons, spaces, new lines) into distinct emails."""
    seen: dict[str, None] = {}
    for part in raw.replace(";", ",").replace("\n", ",").replace(" ", ",").split(","):
        email = auth.normalize_email(part)
        if email:
            seen.setdefault(email)
    return list(seen)


def invite_by_email(
    session: Session, actor: User, league_id: int, emails: list[str]
) -> InviteResult:
    """Record invites for a league. Commissioner or staff, setup status, and only while seats
    remain. Someone already invited or already in the league is skipped, so nobody is emailed
    twice. The caller sends the emails for `result.new`."""
    require_manager(session, actor, league_id)
    league = session.scalar(select(League).where(League.id == league_id).with_for_update())
    if league is None:
        raise LeagueError("League not found.")
    if league.status != "setup":
        raise LeagueError("This league has already started, so it is closed to new teams.")
    if not emails:
        raise LeagueError("Enter at least one email address.")
    if is_full(session, league):
        raise LeagueError("This league is full.")
    existing_invites = set(
        session.scalars(select(LeagueInvite.email).where(LeagueInvite.league_id == league_id))
    )
    if len(existing_invites) + len(emails) > INVITES_PER_SEAT * league.size:
        raise LeagueError("That is more invites than this league needs.")
    members = set(
        session.scalars(
            select(User.email)
            .join(LeagueMember, LeagueMember.user_id == User.id)
            .where(LeagueMember.league_id == league_id)
        )
    )
    new: list[str] = []
    skipped: dict[str, str] = {}
    for email in emails:
        problem = auth.validate_email(email)
        if problem:
            skipped[email] = "not a valid email address"
        elif email in members:
            skipped[email] = "already in this league"
        elif email in existing_invites:
            skipped[email] = "already invited"
        else:
            session.add(LeagueInvite(league_id=league_id, email=email, invited_by_id=actor.id))
            new.append(email)
    if new:
        _audit(
            session,
            league_id=league_id,
            actor_id=actor.id,
            action="league.invite",
            after={"count": len(new)},
        )
    session.commit()
    return InviteResult(new=new, skipped=skipped)


def invites_for_league(session: Session, league_id: int) -> list[LeagueInvite]:
    return list(
        session.scalars(
            select(LeagueInvite)
            .where(LeagueInvite.league_id == league_id)
            .order_by(LeagueInvite.created_at)
        )
    )
