"""Head-to-head schedules (Plan 4.5): who plays whom each week, and creating them for a league.

`generate_matchups` is pure. It deals out a round robin with the circle method, repeats it as
many times as the season needs, and guarantees: every team plays exactly once each week, no team
meets the same opponent two weeks running, and the number of times any two teams meet differs by
at most one. The draw is seeded and the seed is stored in the audit log, so it can be reproduced.
"""

import random
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from undertow.data.models import (
    AuditLog,
    FantasyTeam,
    League,
    LeagueSettings,
    Matchup,
    User,
)
from undertow.fantasy import leagues, season_calendar

Pairing = tuple[int, int]  # (home team id, away team id)


def _circle_rounds(order: Sequence[int]) -> list[list[Pairing]]:
    """The n-1 rounds of a round robin, every pair meeting exactly once across them."""
    n = len(order)
    spots = list(order)
    rounds = []
    for _ in range(n - 1):
        rounds.append([(spots[i], spots[n - 1 - i]) for i in range(n // 2)])
        spots = [spots[0], spots[-1], *spots[1:-1]]  # keep the first fixed and rotate the rest
    return rounds


def generate_matchups(team_ids: Sequence[int], weeks: int, seed: int) -> list[list[Pairing]]:
    """`weeks` weeks of pairings for the teams. Same inputs and seed, same schedule."""
    n = len(team_ids)
    if n < 4 or n % 2:
        raise ValueError("A league needs an even number of teams, at least 4.")
    if len(set(team_ids)) != n:
        raise ValueError("Team ids must be distinct.")
    if weeks < 1:
        raise ValueError("A season needs at least one week.")
    rng = random.Random(seed)
    order = sorted(team_ids)
    rng.shuffle(order)
    rounds = _circle_rounds(order)
    # Every pair meets once per round robin, so two different rounds never share a pairing. The
    # only way to meet the same opponent two weeks running is the same round twice in a row,
    # which can only happen where one round robin ends and the next begins.
    sequence: list[int] = []
    while len(sequence) < weeks:
        cycle = list(range(len(rounds)))
        rng.shuffle(cycle)
        if sequence and cycle[0] == sequence[-1]:
            swap = rng.randrange(1, len(cycle))
            cycle[0], cycle[swap] = cycle[swap], cycle[0]
        sequence.extend(cycle)
    # Home and away: the first meeting of a pair is a coin flip, and later meetings alternate.
    meetings: Counter[frozenset[int]] = Counter()
    first_home: dict[frozenset[int], bool] = {}
    schedule = []
    for index in sequence[:weeks]:
        week = []
        for a, b in rounds[index]:
            pair = frozenset((a, b))
            if pair not in first_home:
                first_home[pair] = rng.random() < 0.5
            a_home = first_home[pair] == (meetings[pair] % 2 == 0)
            meetings[pair] += 1
            week.append((a, b) if a_home else (b, a))
        schedule.append(week)
    return schedule


# ---- creating a league's schedule ----


def create_league_schedule(
    session: Session,
    league_id: int,
    *,
    seed: int | None = None,
    actor: User | None = None,
) -> list[Matchup]:
    """Create every regular-season matchup for a full league. Refuses if it already has them.

    Needs the fantasy calendar for the league's season (it is built from the real schedule if
    missing) to have at least as many weeks as the league's regular season.
    """
    league = session.scalar(select(League).where(League.id == league_id).with_for_update())
    if league is None:
        raise leagues.LeagueError("League not found.")
    if session.scalar(
        select(func.count()).select_from(Matchup).where(Matchup.league_id == league_id)
    ):
        raise leagues.LeagueError("This league already has a schedule.")
    team_ids = list(
        session.scalars(select(FantasyTeam.id).where(FantasyTeam.league_id == league_id))
    )
    if len(team_ids) != league.size:
        raise leagues.LeagueError(
            f"The league has {len(team_ids)} of {league.size} teams, so it cannot be scheduled."
        )
    settings = session.get(LeagueSettings, league_id)
    weeks = season_calendar.stored_weeks(session, league.season_id)
    if len(weeks) < settings.regular_season_weeks:
        weeks = season_calendar.sync_calendar(session, league.season_id)
    if len(weeks) < settings.regular_season_weeks:
        raise leagues.LeagueError(
            f"The season calendar has {len(weeks)} weeks but this league plays "
            f"{settings.regular_season_weeks}. Load the season's schedule first."
        )
    seed = random.SystemRandom().randrange(1_000_000_000) if seed is None else seed
    plan = generate_matchups(team_ids, settings.regular_season_weeks, seed)
    created = [
        Matchup(
            league_id=league_id,
            week_no=week_no,
            last_week_no=week_no,
            home_team_id=home,
            away_team_id=away,
        )
        for week_no, week in enumerate(plan, start=1)
        for home, away in week
    ]
    session.add_all(created)
    session.add(
        AuditLog(
            league_id=league_id,
            actor_id=actor.id if actor else None,
            action="schedule.generate",
            after={"seed": seed, "weeks": settings.regular_season_weeks, "teams": len(team_ids)},
        )
    )
    session.commit()
    return created


def regenerate_league_schedule(
    session: Session, actor: User, league_id: int, *, now: datetime | None = None
) -> list[Matchup]:
    """Commissioner or staff draw a new schedule. Only before the first week begins (Plan 4.5)."""
    leagues.require_manager(session, actor, league_id)
    now = now or datetime.now(UTC)
    league = session.get(League, league_id)
    if league is None:
        raise leagues.LeagueError("League not found.")
    weeks = season_calendar.stored_weeks(session, league.season_id)
    if weeks and season_calendar.eastern_date(now) >= weeks[0].start:
        raise leagues.LeagueError("Week 1 has started, so the schedule can no longer be redrawn.")
    old = session.scalar(
        select(func.count()).select_from(Matchup).where(Matchup.league_id == league_id)
    )
    session.execute(delete(Matchup).where(Matchup.league_id == league_id))
    session.flush()
    created = create_league_schedule(session, league_id, actor=actor)
    session.add(
        AuditLog(
            league_id=league_id,
            actor_id=actor.id,
            action="schedule.regenerate",
            before={"matchups": old},
        )
    )
    session.commit()
    return created


def matchups_for_week(session: Session, league_id: int, week_no: int) -> list[Matchup]:
    return list(
        session.scalars(
            select(Matchup)
            .where(
                Matchup.league_id == league_id,
                Matchup.week_no <= week_no,
                Matchup.last_week_no >= week_no,
            )
            .order_by(Matchup.id)
        )
    )


def main() -> None:
    """Create a league's schedule by hand, for a draft that finished before the season calendar
    was loaded:  uv run python -m undertow.fantasy.schedule LEAGUE_ID"""
    import argparse

    from sqlalchemy.engine import make_url

    from undertow.config import get_settings
    from undertow.data.session import session_factory

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("league_id", type=int)
    args = parser.parse_args()
    print(f"Database host: {make_url(get_settings().database_url).host}")
    with session_factory()() as session:
        try:
            created = create_league_schedule(session, args.league_id)
        except leagues.LeagueError as exc:
            raise SystemExit(f"Not created: {exc}") from exc
    print(f"Created {len(created)} matchups for league {args.league_id}.")


if __name__ == "__main__":
    main()
