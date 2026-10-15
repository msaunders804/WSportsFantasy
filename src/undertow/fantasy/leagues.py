"""Leagues, membership and teams. Business rules only, no HTTP.

Joining a league is one transaction: the league row is locked, the team and member rows are
inserted together, and database constraints (one team per person per league, unique team name,
one commissioner) back up the checks, so two people joining at once cannot overfill a league.
"""

import secrets
from dataclasses import asdict
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from undertow.data.models import (
    AuditLog,
    FantasyTeam,
    League,
    LeagueMember,
    LeagueSettings,
    Player,
    User,
)
from undertow.fantasy import league_rules
from undertow.fantasy.league_rules import LeagueSettingsData

MAX_LEAGUE_NAME = 80
MAX_TEAM_NAME = 40


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
) -> tuple[League, league_rules.Validation]:
    """Create a league in `setup` status. Staff only: this season's leagues are run by Undertow."""
    if not actor.is_admin:
        raise PermissionDenied("Only Undertow staff can create leagues.")
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
        invite_code=secrets.token_urlsafe(12),
        created_by_id=actor.id,
    )
    session.add(league)
    try:
        session.flush()
        session.add(LeagueSettings(league_id=league.id, **settings.as_columns()))
        _audit(
            session,
            league_id=league.id,
            actor_id=actor.id,
            action="league.create",
            after={"name": name, "size": size, **settings.as_columns()},
        )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise LeagueError("A league with that name already exists this season.") from exc
    return league, result


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

    session.add(LeagueMember(league_id=league.id, user_id=user.id, role="member"))
    team = FantasyTeam(league_id=league.id, owner_id=user.id, name=team_name)
    session.add(team)
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
