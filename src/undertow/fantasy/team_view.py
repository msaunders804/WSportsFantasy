"""What the Team tab shows: a team's lineup for a fantasy week with points, games and moves.

Read-only and free of HTTP. Moves themselves go through `lineups.move_player`.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from fractions import Fraction

from sqlalchemy import select
from sqlalchemy.orm import Session

from undertow.data.models import FantasyTeam, League, LeagueSettings, User
from undertow.data.models.lineup import STARTING_SLOTS
from undertow.fantasy import leagues, lineups, matchup_scoring, season_calendar
from undertow.fantasy.season_calendar import EASTERN, WeekSpan


@dataclass
class Row:
    slot: str
    player_id: int | None = None
    name: str = ""
    position: str = ""
    pro_team: str = ""
    injured: bool = False
    locked: bool = False
    points: Fraction = Fraction(0)
    games_total: int = 0
    games_left: int = 0
    next_game: str = ""
    moves: list[dict] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return self.player_id is None


@dataclass
class TeamPage:
    league: League
    team: FantasyTeam
    manager: str
    weeks: list[WeekSpan]
    week: WeekSpan | None
    is_current: bool
    total: Fraction
    games_left: int
    starters: list[Row]
    bench: list[Row]
    ir: list[Row]
    editable: bool
    others: list[tuple[int, str]]  # (team id, name) of every team in the league
    slot_labels: dict[str, str]

    @property
    def prev_week(self) -> int | None:
        return self.week.week_no - 1 if self.week and self.week.week_no > 1 else None

    @property
    def next_week(self) -> int | None:
        return self.week.week_no + 1 if self.week and self.week.week_no < len(self.weeks) else None


def current_week(weeks: list[WeekSpan], now: datetime) -> WeekSpan | None:
    """The week containing today, else the next one to come, else the last one played."""
    if not weeks:
        return None
    today = season_calendar.eastern_date(now)
    found = season_calendar.week_for_date(weeks, today)
    if found:
        return found
    upcoming = [w for w in weeks if w.start > today]
    return upcoming[0] if upcoming else weeks[-1]


def _next_text(game: matchup_scoring.GameScore | None) -> str:
    if game is None:
        return ""
    when = game.start_utc.astimezone(EASTERN)
    clock = when.strftime("%I:%M %p").lstrip("0")
    return f"{when.strftime('%a')} {clock} {'vs' if game.home else '@'} {game.opponent}"


def team_page(
    session: Session,
    league: League,
    team: FantasyTeam,
    viewer: User,
    *,
    week_no: int | None = None,
    now: datetime | None = None,
) -> TeamPage:
    now = now or datetime.now(UTC)
    weeks = season_calendar.stored_weeks(session, league.season_id)
    now_week = current_week(weeks, now)
    week = next((w for w in weeks if w.week_no == week_no), None) if week_no else now_week
    week = week or now_week
    is_current = bool(week and now_week and week.week_no == now_week.week_no)

    scored = (
        matchup_scoring.team_week(session, team.id, week.start, week.end, now=now) if week else None
    )
    by_player = {p.player_id: p for p in scored.players} if scored else {}
    lineup = lineups.team_lineup(session, team.id)
    settings = session.get(LeagueSettings, league.id)
    caps = lineups.capacities(settings)

    editable = (
        is_current
        and league.status != "setup"
        and (team.owner_id == viewer.id or leagues.can_manage(session, viewer, league.id))
    )
    locked = lineups.locked_player_ids(session, [p.id for _, p in lineup], league.season_id, now)
    options = lineups.move_options(session, team.id, now) if editable else {}

    def build(slot_row, player) -> Row:
        pw = by_player.get(player.id)
        row = Row(
            slot=slot_row.slot,
            player_id=player.id,
            name=f"{player.first_name} {player.last_name}".strip(),
            position=player.position or "",
            pro_team=pw.pro_team if pw else "",
            injured=player.status in lineups.INJURED,
            locked=player.id in locked,
            moves=[
                {"slot": o.slot, "swap_with": o.swap_with, "swap_name": o.swap_name}
                for o in options.get(player.id, [])
            ],
        )
        if pw:
            row.points, row.games_total = pw.points, pw.games_total
            row.games_left = pw.games_left(now) if is_current else 0
            row.next_game = _next_text(pw.next_game(now)) if is_current else ""
        return row

    by_slot: dict[str, list[Row]] = {s: [] for s in lineups.LINEUP_SLOTS}
    for slot_row, player in lineup:
        by_slot[slot_row.slot].append(build(slot_row, player))
    starters: list[Row] = []
    for slot in STARTING_SLOTS:  # F, D, UTIL, G, with an empty row for each open spot
        rows = by_slot[slot]
        starters += rows + [Row(slot=slot) for _ in range(max(0, caps[slot] - len(rows)))]
    bench = by_slot["BN"] + [Row(slot="BN") for _ in range(max(0, caps["BN"] - len(by_slot["BN"])))]
    ir = by_slot["IR"] + [Row(slot="IR") for _ in range(max(0, caps["IR"] - len(by_slot["IR"])))]

    manager = session.scalar(select(User.display_name).where(User.id == team.owner_id)) or ""
    others = list(
        session.execute(
            select(FantasyTeam.id, FantasyTeam.name)
            .where(FantasyTeam.league_id == league.id)
            .order_by(FantasyTeam.name)
        ).all()
    )
    return TeamPage(
        league=league,
        team=team,
        manager=manager,
        weeks=weeks,
        week=week,
        is_current=is_current,
        total=scored.total if scored else Fraction(0),
        games_left=sum(r.games_left for r in starters if not r.empty),
        starters=starters,
        bench=bench,
        ir=ir,
        editable=editable,
        others=[(i, n) for i, n in others],
        slot_labels=lineups.SLOT_LABELS,
    )
