"""What the staff overview shows: every league and its status, plus a few site-wide counts.

Read-only and free of HTTP. Nothing here changes data; actions stay on the pages that already
write to the audit log.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from undertow.data.models import (
    Draft,
    FantasyTeam,
    League,
    LeagueInvite,
    LeagueMember,
    User,
    WaitlistSignup,
)

# A live draft whose clock ran out this long ago should already have been autopicked, so the
# worker is probably not running.
CLOCK_OVERDUE = timedelta(seconds=20)


@dataclass
class LeagueRow:
    id: int
    name: str
    kind: str
    status: str
    size: int
    teams: int
    commissioner: str
    commissioner_email: str
    created_at: datetime
    draft_at: datetime | None
    draft_status: str | None
    picks_made: int
    picks_total: int
    pending_invites: int
    flags: list[str] = field(default_factory=list)


@dataclass
class Summary:
    users: int
    users_without_password: int
    waitlist: int
    waitlist_without_account: int
    leagues_by_status: dict[str, int]
    leagues_by_kind: dict[str, int]
    open_seats: int  # empty seats in leagues still in setup


def league_rows(session: Session, *, now: datetime | None = None) -> list[LeagueRow]:
    """Every league, newest first, with what staff need to see at a glance."""
    now = now or datetime.now(UTC)
    teams = dict(
        session.execute(
            select(FantasyTeam.league_id, func.count()).group_by(FantasyTeam.league_id)
        ).all()
    )
    pending = dict(
        session.execute(
            select(LeagueInvite.league_id, func.count())
            .where(LeagueInvite.accepted_at.is_(None))
            .group_by(LeagueInvite.league_id)
        ).all()
    )
    drafts = {d.league_id: d for d in session.scalars(select(Draft))}
    bosses = {
        league_id: (name, email)
        for league_id, name, email in session.execute(
            select(LeagueMember.league_id, User.display_name, User.email)
            .join(User, User.id == LeagueMember.user_id)
            .where(LeagueMember.role == "commissioner")
        )
    }
    rows: list[LeagueRow] = []
    for league in session.scalars(
        select(League).order_by(League.created_at.desc(), League.id.desc())
    ):
        draft = drafts.get(league.id)
        boss_name, boss_email = bosses.get(league.id, ("", ""))
        row = LeagueRow(
            id=league.id,
            name=league.name,
            kind=league.kind,
            status=league.status,
            size=league.size,
            teams=teams.get(league.id, 0),
            commissioner=boss_name or ("Undertow staff" if league.kind == "house" else ""),
            commissioner_email=boss_email,
            created_at=league.created_at,
            draft_at=league.draft_at,
            draft_status=draft.status if draft else None,
            picks_made=min(draft.current_pick - 1, draft.rounds * len(draft.order)) if draft else 0,
            picks_total=draft.rounds * len(draft.order) if draft else 0,
            pending_invites=pending.get(league.id, 0),
        )
        row.flags = _flags(league, row, draft, now)
        rows.append(row)
    return rows


def _flags(league: League, row: LeagueRow, draft: Draft | None, now: datetime) -> list[str]:
    flags: list[str] = []
    if draft is not None and draft.status == "live" and draft.deadline is not None:
        if now - draft.deadline > CLOCK_OVERDUE:
            flags.append("Draft clock overdue: is the worker running?")
    if league.status == "setup":
        if row.teams >= league.size and draft is None:
            flags.append("Full, draft not started")
        if league.draft_at is None:
            flags.append("No draft time set")
        elif league.draft_at < now and draft is None:
            flags.append("Draft time has passed")
    if draft is not None and draft.status == "paused":
        flags.append("Draft is paused")
    return flags


def summary(session: Session) -> Summary:
    from undertow.fantasy import onboarding  # local: onboarding pulls in email settings

    def count(stmt) -> int:
        return session.scalar(stmt) or 0

    leagues = session.scalars(select(League)).all()
    members = dict(
        session.execute(
            select(FantasyTeam.league_id, func.count()).group_by(FantasyTeam.league_id)
        ).all()
    )
    return Summary(
        users=count(select(func.count()).select_from(User)),
        users_without_password=count(
            select(func.count()).select_from(User).where(User.password_hash.is_(None))
        ),
        waitlist=count(select(func.count()).select_from(WaitlistSignup)),
        waitlist_without_account=len(onboarding.waitlist_without_accounts(session)),
        leagues_by_status=_tally(league.status for league in leagues),
        leagues_by_kind=_tally(league.kind for league in leagues),
        open_seats=sum(
            max(0, league.size - members.get(league.id, 0))
            for league in leagues
            if league.status == "setup"
        ),
    )


def _tally(values) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return out
