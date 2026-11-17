"""What a team scored in a fantasy week, and who is winning a matchup (Plan 4.3, 4.4). No HTTP.

Points are always worked out from the stored box scores with the scoring engine, never added up
as games come in, so a stat correction simply changes the next answer. A game's points count for
a team only if the player's frozen slot at puck drop (`lineup_lock`) was a starting one. Games
that have not started yet are listed (so a manager can see what is coming) but score nothing.

Everything is exact: points are fractions and are only rounded when shown.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from fractions import Fraction

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from undertow.data.models import (
    FantasyTeam,
    FantasyWeek,
    Game,
    GoalieGameStats,
    League,
    LineupLock,
    Matchup,
    Player,
    ProTeam,
    SkaterGameStats,
)
from undertow.data.models.lineup import STARTING_SLOTS
from undertow.fantasy import lineups, stat_lines
from undertow.fantasy.scoring import score_goalie, score_skater
from undertow.fantasy.season_calendar import EASTERN
from undertow.fantasy.season_stats import SCORING, goalie_summary, skater_summary


@dataclass
class GameScore:
    game_id: int
    start_utc: datetime
    day: date
    opponent: str
    home: bool
    status: str  # scheduled, live or final
    slot: (
        str | None
    )  # the slot the player was frozen in; the current slot if not started; None if neither
    counted: bool  # True when this game's points count for the team
    stats: dict[str, str] = field(default_factory=dict)
    points: Fraction = Fraction(0)  # what the player scored, whether or not it counts
    breakdown: dict[str, Fraction] = field(default_factory=dict)

    @property
    def scored_for_team(self) -> Fraction:
        return self.points if self.counted else Fraction(0)


@dataclass
class PlayerWeek:
    player_id: int
    name: str
    position: str | None
    pro_team: str
    slot: str | None  # the slot now; None for a player no longer on the lineup
    games: list[GameScore]

    @property
    def points(self) -> Fraction:
        """Points that count for the team this week."""
        return sum((g.scored_for_team for g in self.games), Fraction(0))

    @property
    def games_total(self) -> int:
        return len(self.games)

    def games_left(self, now: datetime) -> int:
        return sum(1 for g in self.games if g.status == "scheduled" and g.start_utc > now)

    def next_game(self, now: datetime) -> GameScore | None:
        return next((g for g in self.games if g.status == "scheduled" and g.start_utc > now), None)


@dataclass
class TeamWeek:
    team_id: int
    start: date
    end: date
    players: list[PlayerWeek]

    @property
    def total(self) -> Fraction:
        return sum((p.points for p in self.players), Fraction(0))


@dataclass
class MatchupScore:
    matchup: Matchup
    home: TeamWeek
    away: TeamWeek
    status: str  # scheduled, live or final (finalized is a stored state, set later)

    @property
    def leader(self) -> int | None:
        """The team id ahead right now, or None if level. Compared on exact totals."""
        if self.home.total == self.away.total:
            return None
        return self.home.team_id if self.home.total > self.away.total else self.away.team_id


def dump_fraction(value: Fraction) -> str:
    """An exact points total as text for storage: Fraction(33, 10) -> "33/10", 5 -> "5"."""
    return str(value)


def load_fraction(text: str) -> Fraction:
    return Fraction(text)


def week_window(start: date, end: date) -> tuple[datetime, datetime]:
    """The moments a week begins and ends, as Eastern midnights (end is exclusive)."""
    lo = datetime.combine(start, time(0, 0), tzinfo=EASTERN)
    hi = datetime.combine(end + timedelta(days=1), time(0, 0), tzinfo=EASTERN)
    return lo, hi


def week_range(
    session: Session, season_id: int, first_week: int, last_week: int
) -> tuple[date, date] | None:
    """First day of `first_week` to last day of `last_week`, or None if the calendar lacks them."""
    rows = {
        w.week_no: w
        for w in session.scalars(
            select(FantasyWeek).where(
                FantasyWeek.season_id == season_id,
                FantasyWeek.week_no.in_((first_week, last_week)),
            )
        )
    }
    if first_week not in rows or last_week not in rows:
        return None
    return rows[first_week].start_date, rows[last_week].end_date


def team_week(
    session: Session, team_id: int, start: date, end: date, *, now: datetime | None = None
) -> TeamWeek:
    """Every player a team had in the window with each game and what it scored.

    Includes players who are no longer on the lineup but have locked games in the window, so a
    dropped player's points are not lost from a week that already happened.
    """
    team = session.get(FantasyTeam, team_id)
    season_id = session.get(League, team.league_id).season_id
    lo, hi = week_window(start, end)

    lineup = {p.id: (row.slot, p) for row, p in lineups.team_lineup(session, team_id)}
    lock_rows = session.execute(
        select(LineupLock, Game)
        .join(Game, Game.id == LineupLock.game_id)
        .where(LineupLock.team_id == team_id, Game.start_utc >= lo, Game.start_utc < hi)
    ).all()
    locks = {(lock.player_id, lock.game_id): lock.slot for lock, _ in lock_rows}
    games: dict[int, Game] = {game.id: game for _, game in lock_rows}

    players: dict[int, tuple[str | None, Player]] = dict(lineup)
    former = {lock.player_id for lock, _ in lock_rows} - set(players)
    if former:
        for player in session.scalars(select(Player).where(Player.id.in_(former))):
            players[player.id] = (None, player)

    pro_ids = {p.pro_team_id for _, p in players.values() if p.pro_team_id is not None}
    if pro_ids:
        for game in session.scalars(
            select(Game).where(
                Game.season_id == season_id,
                Game.start_utc >= lo,
                Game.start_utc < hi,
                or_(Game.home_team_id.in_(pro_ids), Game.away_team_id.in_(pro_ids)),
            )
        ):
            games[game.id] = game

    team_ids = (
        {g.home_team_id for g in games.values()}
        | {g.away_team_id for g in games.values()}
        | pro_ids
    )
    codes = (
        {t.id: t.code for t in session.scalars(select(ProTeam).where(ProTeam.id.in_(team_ids)))}
        if team_ids
        else {}
    )
    skater_rows = (
        {
            (r.player_id, r.game_id): r
            for r in session.scalars(
                select(SkaterGameStats).where(
                    SkaterGameStats.game_id.in_(games), SkaterGameStats.player_id.in_(players)
                )
            )
        }
        if games and players
        else {}
    )
    goalie_rows = (
        {
            (r.player_id, r.game_id): r
            for r in session.scalars(
                select(GoalieGameStats).where(
                    GoalieGameStats.game_id.in_(games), GoalieGameStats.player_id.in_(players)
                )
            )
        }
        if games and players
        else {}
    )

    by_player: dict[int, list[GameScore]] = defaultdict(list)
    for player_id, (slot_now, player) in players.items():
        wanted = {gid for (pid, gid) in locks if pid == player_id}
        wanted |= {
            g.id
            for g in games.values()
            if player.pro_team_id is not None
            and player.pro_team_id in (g.home_team_id, g.away_team_id)
        }
        for gid in sorted(wanted, key=lambda i: (games[i].start_utc, i)):
            game = games[gid]
            frozen = locks.get((player_id, gid))
            is_home = player.pro_team_id == game.home_team_id
            started = game.status in ("live", "final") or game.start_utc <= (
                now or datetime.now(UTC)
            )
            if frozen is not None:
                slot, counted = frozen, frozen in STARTING_SLOTS
            elif not started:
                slot, counted = slot_now, False  # still open: the current slot is what will lock
            else:
                slot, counted = None, False  # started before the team could lock (late draft)
            entry = GameScore(
                game_id=gid,
                start_utc=game.start_utc,
                day=game.start_utc.astimezone(EASTERN).date(),
                opponent=codes.get(game.away_team_id if is_home else game.home_team_id, ""),
                home=is_home,
                status=game.status,
                slot=slot,
                counted=counted,
            )
            if player.position == "G":
                row = goalie_rows.get((player_id, gid))
                if row is not None:
                    line = stat_lines.goalie_stat_line(row, game.result_type)
                    if line.played:
                        result = score_goalie(line, SCORING)
                        entry.points, entry.breakdown = result.total, result.breakdown
                        entry.stats = goalie_summary(row)
            else:
                row = skater_rows.get((player_id, gid))
                if row is not None and row.toi_seconds > 0:
                    result = score_skater(stat_lines.skater_stat_line(row), SCORING)
                    entry.points, entry.breakdown = result.total, result.breakdown
                    entry.stats = skater_summary(row)
            by_player[player_id].append(entry)

    out = [
        PlayerWeek(
            player_id=pid,
            name=f"{player.first_name} {player.last_name}".strip(),
            position=player.position,
            pro_team=codes.get(player.pro_team_id, "") if player.pro_team_id else "",
            slot=slot_now,
            games=by_player.get(pid, []),
        )
        for pid, (slot_now, player) in players.items()
    ]
    return TeamWeek(team_id=team_id, start=start, end=end, players=out)


def week_status(
    session: Session, season_id: int, start: date, end: date, *, now: datetime | None = None
) -> str:
    """scheduled until the week's first game starts, final once every game in it is final,
    live in between. A week with no games is final."""
    now = now or datetime.now(UTC)
    lo, hi = week_window(start, end)
    games = list(
        session.execute(
            select(Game.status, Game.start_utc).where(
                Game.season_id == season_id, Game.start_utc >= lo, Game.start_utc < hi
            )
        ).all()
    )
    if not games or all(status == "final" for status, _ in games):
        return "final"
    if any(status in ("live", "final") or start_utc <= now for status, start_utc in games):
        return "live"
    return "scheduled"


def score_matchup(
    session: Session, matchup: Matchup, *, now: datetime | None = None
) -> MatchupScore | None:
    """Both teams' totals over the matchup's weeks, or None if the calendar lacks those weeks."""
    now = now or datetime.now(UTC)
    league = session.get(League, matchup.league_id)
    span = week_range(session, league.season_id, matchup.week_no, matchup.last_week_no)
    if span is None:
        return None
    start, end = span
    return MatchupScore(
        matchup=matchup,
        home=team_week(session, matchup.home_team_id, start, end, now=now),
        away=team_week(session, matchup.away_team_id, start, end, now=now),
        status=week_status(session, league.season_id, start, end, now=now),
    )
