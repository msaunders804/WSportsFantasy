"""What the Matchup tab shows: the two teams head to head, slot by slot, and the week's other
matchups. Read-only, no HTTP. The numbers come from `matchup_scoring`."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from fractions import Fraction

from sqlalchemy import select
from sqlalchemy.orm import Session

from undertow.data.models import FantasyTeam, League, LeagueSettings, Matchup, User
from undertow.data.models.lineup import STARTING_SLOTS
from undertow.fantasy import matchup_scoring as ms
from undertow.fantasy import season_calendar, standings, team_view
from undertow.fantasy.matchup_scoring import PlayerWeek


@dataclass
class Cell:
    player_id: int
    name: str
    position: str
    pro_team: str
    points: Fraction
    status: str  # live, final, upcoming or none
    detail: str  # a short stat line, e.g. "1G 2A 4 SOG"
    games_left: int


@dataclass
class SlotRow:
    group: str  # F, UTIL, D or G
    left: Cell | None
    right: Cell | None


@dataclass
class Side:
    team_id: int
    name: str
    manager: str
    total: Fraction
    mine: bool = False


@dataclass
class Other:
    matchup: Matchup
    home: Side
    away: Side


@dataclass
class MatchupPage:
    league: League
    matchup: Matchup
    left: Side
    right: Side
    rows: list[SlotRow]
    status: str  # scheduled, live, final or finalized
    leader: int | None  # team id ahead, None if level
    first_week: int
    weeks: list[int]  # week numbers that have matchups
    week_dates: tuple | None
    others: list[Other] = field(default_factory=list)

    @property
    def prev_week(self) -> int | None:
        earlier = [w for w in self.weeks if w < self.first_week]
        return max(earlier) if earlier else None

    @property
    def next_week(self) -> int | None:
        later = [w for w in self.weeks if w > self.first_week]
        return min(later) if later else None


GROUP_ORDER = ("F", "UTIL", "D", "G")


def _detail(pw: PlayerWeek) -> str:
    """The counted games' stats added together, as short text."""
    games = [g for g in pw.games if g.counted and g.stats]
    if not games:
        return ""
    if pw.position == "G":
        sv = sum(int(g.stats["SV"]) for g in games)
        ga = sum(int(g.stats["GA"]) for g in games)
        wins = sum(1 for g in games if g.stats["Dec"] == "W")
        shutouts = sum(int(g.stats["SO"]) for g in games)
        bits = [f"{sv} SV", f"{ga} GA"]
        if wins:
            bits.append("W" if wins == 1 else f"{wins} W")
        if shutouts:
            bits.append("SO")
        return " · ".join(bits)
    total = {k: sum(int(g.stats[k]) for g in games) for k in ("G", "A", "SOG", "Hits", "Blk")}
    bits = [f"{total['G']}G", f"{total['A']}A", f"{total['SOG']} SOG"]
    if total["Hits"] or total["Blk"]:
        bits.append(f"{total['Hits']} H {total['Blk']} B")
    return " ".join(bits)


def _cell(pw: PlayerWeek, now: datetime) -> Cell:
    left = pw.games_left(now)
    if any(g.status == "live" for g in pw.games):
        status = "live"
    elif left:
        status = "upcoming"
    elif pw.games:
        status = "final"
    else:
        status = "none"
    return Cell(
        pw.player_id, pw.name, pw.position or "", pw.pro_team, pw.points, status, _detail(pw), left
    )


def _group_of(pw: PlayerWeek) -> str | None:
    """The slot a player is shown in: the one they were last frozen in with points counted, or
    their current starting slot. Bench and IR players who never counted are not shown."""
    counted = [g.slot for g in pw.games if g.counted and g.slot]
    if counted:
        return counted[-1]
    return pw.slot if pw.slot in STARTING_SLOTS else None


def _rows(left: ms.TeamWeek, right: ms.TeamWeek, now: datetime) -> list[SlotRow]:
    def cells(tw: ms.TeamWeek) -> dict[str, list[Cell]]:
        out: dict[str, list[Cell]] = {g: [] for g in GROUP_ORDER}
        for pw in tw.players:
            group = _group_of(pw)
            if group in out:
                out[group].append(_cell(pw, now))
        for lst in out.values():
            lst.sort(key=lambda c: (-c.points, c.name))
        return out

    a, b = cells(left), cells(right)
    rows: list[SlotRow] = []
    for group in GROUP_ORDER:
        for i in range(max(len(a[group]), len(b[group]))):
            rows.append(
                SlotRow(
                    group,
                    a[group][i] if i < len(a[group]) else None,
                    b[group][i] if i < len(b[group]) else None,
                )
            )
    return rows


def _side(session: Session, team_id: int, total: Fraction, viewer: User) -> Side:
    team = session.get(FantasyTeam, team_id)
    manager = session.scalar(select(User.display_name).where(User.id == team.owner_id)) or ""
    return Side(team.id, team.name, manager, total, mine=team.owner_id == viewer.id)


def weeks_with_matchups(session: Session, league_id: int) -> list[int]:
    return sorted(
        set(session.scalars(select(Matchup.week_no).where(Matchup.league_id == league_id)))
    )


def pick_matchup(
    session: Session, league: League, viewer: User, week_no: int | None, now: datetime
) -> Matchup | None:
    """The matchup to show: the viewer's in the chosen (default: current) week, else the week's
    first one. None if the league has no schedule."""
    weeks = weeks_with_matchups(session, league.id)
    if not weeks:
        return None
    if week_no not in weeks:
        current = team_view.current_week(
            season_calendar.stored_weeks(session, league.season_id), now
        )
        wanted = current.week_no if current else weeks[0]
        week_no = min(weeks, key=lambda w: (abs(w - wanted), w))
    in_week = list(
        session.scalars(
            select(Matchup)
            .where(Matchup.league_id == league.id, Matchup.week_no == week_no)
            .order_by(Matchup.id)
        )
    )
    mine = session.scalars(
        select(FantasyTeam.id).where(
            FantasyTeam.league_id == league.id, FantasyTeam.owner_id == viewer.id
        )
    ).first()
    for m in in_week:
        if mine in (m.home_team_id, m.away_team_id):
            return m
    return in_week[0] if in_week else None


def matchup_page(
    session: Session, league: League, matchup: Matchup, viewer: User, *, now: datetime | None = None
) -> MatchupPage | None:
    now = now or datetime.now(UTC)
    scored = ms.score_matchup(session, matchup, now=now)
    if scored is None:
        return None
    home_total, away_total = scored.home.total, scored.away.total
    if matchup.status == "finalized" and matchup.home_points is not None:
        home_total, away_total = (
            ms.load_fraction(matchup.home_points),
            ms.load_fraction(matchup.away_points),
        )
    home = _side(session, matchup.home_team_id, home_total, viewer)
    away = _side(session, matchup.away_team_id, away_total, viewer)
    # Your own team is always on the left.
    flip = away.mine and not home.mine
    left, right = (away, home) if flip else (home, away)
    left_week, right_week = (scored.away, scored.home) if flip else (scored.home, scored.away)
    leader = (
        None
        if home_total == away_total
        else (home.team_id if home_total > away_total else away.team_id)
    )
    status = matchup.status if matchup.status in ("final", "finalized") else scored.status
    others = []
    for m in session.scalars(
        select(Matchup)
        .where(
            Matchup.league_id == league.id,
            Matchup.week_no == matchup.week_no,
            Matchup.id != matchup.id,
        )
        .order_by(Matchup.id)
    ):
        other = ms.score_matchup(session, m, now=now)
        if other is None:
            continue
        h_total, a_total = other.home.total, other.away.total
        if m.status == "finalized" and m.home_points is not None:
            h_total, a_total = ms.load_fraction(m.home_points), ms.load_fraction(m.away_points)
        others.append(
            Other(
                m,
                _side(session, m.home_team_id, h_total, viewer),
                _side(session, m.away_team_id, a_total, viewer),
            )
        )
    span = ms.week_range(session, league.season_id, matchup.week_no, matchup.last_week_no)
    return MatchupPage(
        league=league,
        matchup=matchup,
        left=left,
        right=right,
        rows=_rows(left_week, right_week, now),
        status=status,
        leader=leader,
        first_week=matchup.week_no,
        weeks=weeks_with_matchups(session, league.id),
        week_dates=span,
        others=others,
    )


def settings_for(session: Session, league_id: int) -> LeagueSettings:
    return session.get(LeagueSettings, league_id)


@dataclass
class StandingsPage:
    league: League
    rows: list[standings.StandingRow]
    provisional: bool
    week_no: int | None
    this_week: list[Other]  # matchups of the current week that are not final yet
    results: list[tuple[int, list[Other]]]  # finished weeks, newest first
    mine_team_id: int | None


def standings_page(
    session: Session, league: League, viewer: User, *, now: datetime | None = None
) -> StandingsPage:
    now = now or datetime.now(UTC)
    rows, provisional = standings.standings(session, league.id, now=now)
    done = standings.results(session, league.id, now=now)
    by_week: dict[int, list[Other]] = {}
    for r in done:
        by_week.setdefault(r.matchup.week_no, []).append(
            Other(
                r.matchup,
                _side(session, r.matchup.home_team_id, r.home_points, viewer),
                _side(session, r.matchup.away_team_id, r.away_points, viewer),
            )
        )
    weeks = weeks_with_matchups(session, league.id)
    current = team_view.current_week(season_calendar.stored_weeks(session, league.season_id), now)
    week_no = None
    if weeks:
        wanted = current.week_no if current else weeks[0]
        week_no = min(weeks, key=lambda w: (abs(w - wanted), w))
    finished = {m for r in done for m in (r.matchup.id,)}
    this_week: list[Other] = []
    if week_no is not None:
        for m in session.scalars(
            select(Matchup)
            .where(Matchup.league_id == league.id, Matchup.week_no == week_no)
            .order_by(Matchup.id)
        ):
            if m.id in finished:
                continue
            scored = ms.score_matchup(session, m, now=now)
            if scored is not None:
                this_week.append(
                    Other(
                        m,
                        _side(session, m.home_team_id, scored.home.total, viewer),
                        _side(session, m.away_team_id, scored.away.total, viewer),
                    )
                )
    mine = session.scalar(
        select(FantasyTeam.id).where(
            FantasyTeam.league_id == league.id, FantasyTeam.owner_id == viewer.id
        )
    )
    return StandingsPage(
        league=league,
        rows=rows,
        provisional=provisional,
        week_no=week_no,
        this_week=this_week,
        results=sorted(by_week.items(), reverse=True),
        mine_team_id=mine,
    )
