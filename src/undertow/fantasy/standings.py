"""League standings from head-to-head results. Read-only, no HTTP.

Only matchups whose week is over count: a finalized matchup uses its stored totals; one that is
final but not yet finalized is worked out from the box scores and marks the table provisional.
Order is record (a win is 2, a tie 1), then points for, then name. The full playoff tiebreak
(head to head, then a seeded coin flip, Plan 4.5) is applied when playoff seeds are set.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from fractions import Fraction

from sqlalchemy import select
from sqlalchemy.orm import Session

from undertow.data.models import FantasyTeam, Matchup, User
from undertow.fantasy import matchup_scoring as ms


@dataclass
class StandingRow:
    team_id: int
    name: str
    manager: str
    wins: int = 0
    losses: int = 0
    ties: int = 0
    points_for: Fraction = Fraction(0)
    points_against: Fraction = Fraction(0)
    rank: int = 0

    @property
    def played(self) -> int:
        return self.wins + self.losses + self.ties

    @property
    def record_points(self) -> int:
        return 2 * self.wins + self.ties


@dataclass(frozen=True)
class Result:
    matchup: Matchup
    home_points: Fraction
    away_points: Fraction
    provisional: bool

    @property
    def winner(self) -> int | None:
        """The winning team id, or None for a tie. Decided on exact totals."""
        if self.home_points == self.away_points:
            return None
        return (
            self.matchup.home_team_id
            if self.home_points > self.away_points
            else self.matchup.away_team_id
        )


def results(session: Session, league_id: int, *, now: datetime | None = None) -> list[Result]:
    """Every matchup in the league whose week is over, with its totals, oldest week first."""
    now = now or datetime.now(UTC)
    out = []
    for matchup in session.scalars(
        select(Matchup)
        .where(Matchup.league_id == league_id, Matchup.status.in_(("final", "finalized")))
        .order_by(Matchup.week_no, Matchup.id)
    ):
        if matchup.status == "finalized" and matchup.home_points is not None:
            out.append(
                Result(
                    matchup,
                    ms.load_fraction(matchup.home_points),
                    ms.load_fraction(matchup.away_points),
                    False,
                )
            )
            continue
        scored = ms.score_matchup(session, matchup, now=now)
        if scored is not None:
            out.append(Result(matchup, scored.home.total, scored.away.total, True))
    return out


def standings(
    session: Session, league_id: int, *, now: datetime | None = None
) -> tuple[list[StandingRow], bool]:
    """(rows best first, whether any result is provisional)."""
    teams = session.execute(
        select(FantasyTeam.id, FantasyTeam.name, User.display_name)
        .join(User, User.id == FantasyTeam.owner_id)
        .where(FantasyTeam.league_id == league_id)
    ).all()
    rows = {tid: StandingRow(tid, name, manager) for tid, name, manager in teams}
    provisional = False
    for r in results(session, league_id, now=now):
        provisional = provisional or r.provisional
        home, away = rows[r.matchup.home_team_id], rows[r.matchup.away_team_id]
        home.points_for += r.home_points
        home.points_against += r.away_points
        away.points_for += r.away_points
        away.points_against += r.home_points
        if r.winner is None:
            home.ties += 1
            away.ties += 1
        elif r.winner == home.team_id:
            home.wins += 1
            away.losses += 1
        else:
            away.wins += 1
            home.losses += 1
    ordered = sorted(rows.values(), key=lambda s: (-s.record_points, -s.points_for, s.name.lower()))
    for place, row in enumerate(ordered, start=1):
        row.rank = place
    return ordered, provisional
