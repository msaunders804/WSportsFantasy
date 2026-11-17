"""Keeping matchups up to date and finalizing them (Plan 2.3, 4.4). Run by the worker.

A matchup's status follows its weeks: scheduled until the week's first game, live while games are
being played, final once every game in the week is final. Results are not settled then, because
box scores get corrected; a matchup is *finalized* a few days after its last week ends. At that
point its two totals are stored as exact fractions and never change again, so a late correction
cannot flip a result that managers have already seen.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from undertow.data.models import League, Matchup
from undertow.fantasy import matchup_scoring as ms
from undertow.fantasy.season_calendar import eastern_date

log = logging.getLogger(__name__)

# Days after the last week ends before a result is final for good (Plan 2.3: default 3).
FINALIZE_AFTER_DAYS = 3
ACTIVE_LEAGUE_STATUSES = ("in_season", "playoffs")


@dataclass(frozen=True)
class Refresh:
    status_changes: int
    finalized: int

    def summary(self) -> str:
        return f"status_changes={self.status_changes} finalized={self.finalized}"


def refresh_matchups(session: Session, now: datetime | None = None) -> Refresh:
    """Update every unfinalized matchup's status, and finalize those whose time has come."""
    now = now or datetime.now(UTC)
    today: date = eastern_date(now)
    rows = session.execute(
        select(Matchup, League.season_id)
        .join(League, League.id == Matchup.league_id)
        .where(Matchup.status != "finalized", League.status.in_(ACTIVE_LEAGUE_STATUSES))
        .order_by(Matchup.id)
    ).all()
    spans: dict[tuple[int, int, int], tuple[date, date] | None] = {}
    statuses: dict[tuple[int, int, int], str] = {}
    changed = finalized = 0
    for matchup, season_id in rows:
        key = (season_id, matchup.week_no, matchup.last_week_no)
        if key not in spans:
            spans[key] = ms.week_range(session, season_id, matchup.week_no, matchup.last_week_no)
        span = spans[key]
        if span is None or span[0] > today:  # no calendar, or a week that has not begun
            continue
        if key not in statuses:
            statuses[key] = ms.week_status(session, season_id, *span, now=now)
        status = statuses[key]
        if status == "final" and today > span[1] + timedelta(days=FINALIZE_AFTER_DAYS):
            scored = ms.score_matchup(session, matchup, now=now)
            if scored is not None:
                matchup.home_points = ms.dump_fraction(scored.home.total)
                matchup.away_points = ms.dump_fraction(scored.away.total)
                matchup.status = "finalized"
                matchup.finalized_at = now
                finalized += 1
                session.commit()
                continue
        if matchup.status != status:
            matchup.status = status
            changed += 1
            session.commit()
    return Refresh(changed, finalized)


def run_matchup_job(session_factory: Callable[[], Session], now: datetime | None = None) -> str:
    with session_factory() as session:
        return refresh_matchups(session, now).summary()
