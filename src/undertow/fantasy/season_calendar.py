"""The fantasy calendar (Plan 4.4): which real-world dates make up each fantasy week.

Weeks run Monday to Sunday in Eastern time. The opening weekend is merged into the first full
week (the 2026-27 season opens on a Saturday, so Week 1 is Dec 5 to Dec 13). A week with no games
at all, such as the All-Star break, is skipped and does not use up a week number. Which weeks are
regular season and which are playoff rounds is a league setting, not part of the calendar.

`build_weeks` and the checks are pure. `sync_calendar` stores the result for a season. Weeks that
have already started are never changed by a later sync, so a postponement cannot renumber history.
"""

import logging
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from undertow.data.models import FantasyWeek, Game

log = logging.getLogger(__name__)

EASTERN = ZoneInfo("America/New_York")
# Playoff rounds are two fantasy weeks long (decided Oct 12, 2026; see docs/decisions.md).
PLAYOFF_ROUND_WEEKS = 2
# The plan warns the commissioner when a real team plays fewer games than this in a round.
MIN_GAMES_PER_ROUND = 2


@dataclass(frozen=True)
class WeekSpan:
    week_no: int
    start: date
    end: date
    games: int

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end


def eastern_date(moment: datetime) -> date:
    """The Eastern calendar date of a moment (a timezone-aware datetime)."""
    return moment.astimezone(EASTERN).date()


def build_weeks(game_days: Iterable[date]) -> list[WeekSpan]:
    """Fantasy weeks for a season from the dates its games are played on."""
    counts = Counter(game_days)
    if not counts:
        return []
    first, last = min(counts), max(counts)
    monday = first - timedelta(days=first.weekday())
    # A season that opens mid-week folds that partial week into the next full one.
    start = first
    end = monday + timedelta(days=13 if first.weekday() != 0 else 6)
    spans: list[WeekSpan] = []
    while start <= last:
        games = sum(counts[start + timedelta(days=i)] for i in range((end - start).days + 1))
        if games:  # an empty week (the All-Star break) is skipped, not numbered
            spans.append(WeekSpan(len(spans) + 1, start, end, games))
        start = end + timedelta(days=1)
        end = start + timedelta(days=6)
    return spans


def week_for_date(weeks: Sequence[WeekSpan], day: date) -> WeekSpan | None:
    """The fantasy week a date falls in, or None for a skipped (empty) week or out of season."""
    return next((w for w in weeks if w.contains(day)), None)


def playoff_rounds(
    weeks: Sequence[WeekSpan], regular_weeks: int, round_weeks: int = PLAYOFF_ROUND_WEEKS
) -> list[tuple[int, int]]:
    """(first week, last week) of each whole playoff round that fits after the regular season."""
    rounds = []
    first = regular_weeks + 1
    while first + round_weeks - 1 <= len(weeks):
        rounds.append((first, first + round_weeks - 1))
        first += round_weeks
    return rounds


def thin_rounds(
    weeks: Sequence[WeekSpan],
    games: Iterable[tuple[date, int, int]],
    regular_weeks: int,
    minimum: int = MIN_GAMES_PER_ROUND,
) -> list[tuple[int, int, int]]:
    """(round number, real team id, games) for every team that plays fewer than `minimum` games
    in a playoff round. `games` is (date, home team id, away team id). A schedule change can
    create these, so the commissioner is warned (Plan 4.4)."""
    teams = {t for _, h, a in games for t in (h, a)}
    thin = []
    for number, (first, last) in enumerate(playoff_rounds(weeks, regular_weeks), start=1):
        lo, hi = weeks[first - 1].start, weeks[last - 1].end
        played = Counter(t for d, h, a in games if lo <= d <= hi for t in (h, a))
        thin.extend((number, t, played[t]) for t in sorted(teams) if played[t] < minimum)
    return thin


def sync_calendar(session: Session, season_id: int, *, today: date | None = None) -> list[WeekSpan]:
    """Store the season's weeks, built from its games. Returns the weeks now stored.

    Weeks that have already started keep their stored dates and numbers even if the schedule
    changed since, because matchups and results are tied to week numbers. A difference is logged.
    """
    today = today or eastern_date(datetime.now(EASTERN))
    rows = session.execute(select(Game.start_utc).where(Game.season_id == season_id)).all()
    wanted = build_weeks(eastern_date(start) for (start,) in rows)
    stored = {
        w.week_no: w
        for w in session.scalars(select(FantasyWeek).where(FantasyWeek.season_id == season_id))
    }
    for span in wanted:
        row = stored.pop(span.week_no, None)
        if row is None:
            session.add(
                FantasyWeek(
                    season_id=season_id,
                    week_no=span.week_no,
                    start_date=span.start,
                    end_date=span.end,
                    games=span.games,
                )
            )
        elif row.start_date <= today:
            if (row.start_date, row.end_date) != (span.start, span.end):
                log.warning(
                    "season %s week %s has started, so its dates stay %s to %s (schedule now "
                    "says %s to %s)",
                    season_id,
                    span.week_no,
                    row.start_date,
                    row.end_date,
                    span.start,
                    span.end,
                )
            row.games = (
                span.games
                if (row.start_date, row.end_date) == (span.start, span.end)
                else row.games
            )
        else:
            row.start_date, row.end_date, row.games = span.start, span.end, span.games
    for week_no, row in stored.items():  # weeks the schedule no longer has
        if row.start_date > today:
            session.delete(row)
        else:
            log.warning(
                "season %s week %s has started but is no longer in the schedule", season_id, week_no
            )
    session.commit()
    return stored_weeks(session, season_id)


def stored_weeks(session: Session, season_id: int) -> list[WeekSpan]:
    rows = session.scalars(
        select(FantasyWeek).where(FantasyWeek.season_id == season_id).order_by(FantasyWeek.week_no)
    )
    return [WeekSpan(w.week_no, w.start_date, w.end_date, w.games) for w in rows]
