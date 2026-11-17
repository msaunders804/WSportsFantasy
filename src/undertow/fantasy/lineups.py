"""Lineup rules (Plan 4.3): where each rostered player sits, who may move where, and the
per-game lock. Business rules only, no HTTP.

A team's lineup is one row per rostered player in `lineup_slot`. Slots: F, D, UTIL (a forward or a
defender), G, BN (bench) and IR (injured reserve). Only the starting slots (F, D, UTIL, G) score.

Locking: a player may not be moved once their game has started on the current fantasy day. When
a game starts, `lock_started_games` writes a `lineup_lock` row per rostered player in it, freezing
the slot they were in; scoring counts only players whose frozen slot is a starting one. Because
the frozen row is what scores, moving a player later never rewrites a game that already happened.

The fantasy day rolls over at 04:00 Eastern, so a late West-coast game still counts as the day it
was played on.
"""

import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from undertow.data.models import (
    AuditLog,
    Draft,
    FantasyTeam,
    Game,
    League,
    LeagueSettings,
    LineupLock,
    LineupSlot,
    Player,
    RosterEntry,
    User,
)
from undertow.data.models.lineup import LINEUP_SLOTS, STARTING_SLOTS
from undertow.fantasy import leagues
from undertow.fantasy.season_calendar import EASTERN

log = logging.getLogger(__name__)

DAY_ROLLOVER = timedelta(hours=4)
# How far back the lock job looks for games that need locking. It exists to catch up after
# downtime without scanning the whole season every time.
LOCK_LOOKBACK = timedelta(days=3)
SLOT_LABELS = {
    "F": "forward",
    "D": "defender",
    "UTIL": "flex",
    "G": "goalie",
    "BN": "bench",
    "IR": "injured reserve",
}
INJURED = ("injured", "ltir")


class LineupError(leagues.LeagueError):
    """A lineup rule was broken. The message is safe to show to the person."""


def fantasy_day(moment: datetime):
    return (moment.astimezone(EASTERN) - DAY_ROLLOVER).date()


def day_start(moment: datetime) -> datetime:
    """The instant the fantasy day containing `moment` began (04:00 Eastern)."""
    return datetime.combine(fantasy_day(moment), time(4, 0), tzinfo=EASTERN)


def capacities(settings: LeagueSettings) -> dict[str, int]:
    return {
        "F": settings.roster_f,
        "D": settings.roster_d,
        "UTIL": settings.roster_util,
        "G": settings.roster_g,
        "BN": settings.roster_bench,
        "IR": settings.roster_ir,
    }


def eligible(position: str | None, slot: str, status: str = "active") -> bool:
    """May a player at `position` (with availability `status`) sit in `slot`?"""
    if slot == "BN":
        return True
    if slot == "IR":
        return status in INJURED
    if slot == "UTIL":
        return position in ("F", "D")
    return position == slot


def _rows(session: Session, team_id: int) -> list[tuple[LineupSlot, Player]]:
    return list(
        session.execute(
            select(LineupSlot, Player)
            .join(Player, Player.id == LineupSlot.player_id)
            .where(LineupSlot.team_id == team_id)
            .order_by(LineupSlot.id)
        ).all()
    )


def _season_id(session: Session, team_id: int) -> int:
    return session.scalar(
        select(League.season_id)
        .join(FantasyTeam, FantasyTeam.league_id == League.id)
        .where(FantasyTeam.id == team_id)
    )


def _settings(session: Session, team_id: int) -> LeagueSettings:
    return session.scalar(
        select(LeagueSettings)
        .join(FantasyTeam, FantasyTeam.league_id == LeagueSettings.league_id)
        .where(FantasyTeam.id == team_id)
    )


# ---- placing players ----


def place_new_player(session: Session, team_id: int, player: Player) -> str:
    """Give a newly rostered player a slot: their own position's slot if one is open, then the
    flex spot for a forward or defender, then the bench. Raises if the team has no room at all."""
    caps = capacities(_settings(session, team_id))
    used = Counter(slot.slot for slot, _ in _rows(session, team_id))
    order = (
        [player.position, "UTIL", "BN"]
        if player.position in ("F", "D")
        else [player.position, "BN"]
    )
    for slot in order:
        if slot in caps and used[slot] < caps[slot]:
            session.add(LineupSlot(team_id=team_id, player_id=player.id, slot=slot))
            session.flush()
            return slot
    raise LineupError("There is no open spot on this roster.")


def remove_player(session: Session, team_id: int, player_id: int) -> None:
    session.execute(
        delete(LineupSlot).where(LineupSlot.team_id == team_id, LineupSlot.player_id == player_id)
    )


def repair_lineups(session: Session) -> int:
    """Safety net: give a slot to any active roster player who has none, and drop slots for
    players no longer on the roster. Returns how many rows it changed. Normally a no-op."""
    changed = 0
    missing = session.execute(
        select(RosterEntry.team_id, Player)
        .join(Player, Player.id == RosterEntry.player_id)
        .outerjoin(
            LineupSlot,
            (LineupSlot.team_id == RosterEntry.team_id)
            & (LineupSlot.player_id == RosterEntry.player_id),
        )
        .where(RosterEntry.released_at.is_(None), LineupSlot.id.is_(None))
        .order_by(RosterEntry.id)
    ).all()
    for team_id, player in missing:
        try:
            place_new_player(session, team_id, player)
            changed += 1
        except LineupError:
            log.warning("team %s has no room to place player %s", team_id, player.id)
    stale = (
        session.execute(
            select(LineupSlot.id)
            .outerjoin(
                RosterEntry,
                (RosterEntry.team_id == LineupSlot.team_id)
                & (RosterEntry.player_id == LineupSlot.player_id)
                & RosterEntry.released_at.is_(None),
            )
            .where(RosterEntry.id.is_(None))
        )
        .scalars()
        .all()
    )
    if stale:
        session.execute(delete(LineupSlot).where(LineupSlot.id.in_(stale)))
        changed += len(stale)
    session.commit()
    return changed


# ---- the lock ----


def locked_player_ids(
    session: Session, player_ids: list[int], season_id: int, now: datetime
) -> set[int]:
    """Players whose real game has already started on the current fantasy day."""
    if not player_ids:
        return set()
    return set(
        session.scalars(
            select(Player.id)
            .join(
                Game,
                or_(
                    Game.home_team_id == Player.pro_team_id, Game.away_team_id == Player.pro_team_id
                ),
            )
            .where(
                Player.id.in_(player_ids),
                Game.season_id == season_id,
                Game.start_utc <= now,
                Game.start_utc >= day_start(now),
            )
        )
    )


def lock_started_games(session: Session, now: datetime | None = None) -> int:
    """Freeze each rostered player's slot for every game that has started. Safe to run often and
    to run twice: a (team, player, game) is locked once and never rewritten. Returns new locks.

    Only leagues whose draft is complete are locked, and only for games that started after their
    draft finished, so nobody scores for games played before their team existed.
    """
    now = now or datetime.now(UTC)
    select_locks = (
        select(LineupSlot.team_id, LineupSlot.player_id, Game.id, LineupSlot.slot)
        .join(FantasyTeam, FantasyTeam.id == LineupSlot.team_id)
        .join(League, League.id == FantasyTeam.league_id)
        .join(Draft, Draft.league_id == League.id)
        .join(Player, Player.id == LineupSlot.player_id)
        .join(
            Game,
            (Game.season_id == League.season_id)
            & or_(Game.home_team_id == Player.pro_team_id, Game.away_team_id == Player.pro_team_id),
        )
        .where(
            League.status.in_(("in_season", "playoffs")),
            Draft.status == "complete",
            Game.start_utc >= Draft.completed_at,
            Game.start_utc >= now - LOCK_LOOKBACK,
            or_(Game.start_utc <= now, Game.status.in_(("live", "final"))),
        )
    )
    # INSERT ... SELECT reports no row count through the driver, so count the rows it returns.
    created = session.execute(
        insert(LineupLock)
        .from_select(["team_id", "player_id", "game_id", "slot"], select_locks)
        .on_conflict_do_nothing(constraint="uq_lineup_lock_team_player_game")
        .returning(LineupLock.id)
    ).all()
    session.commit()
    return len(created)


def release_unstarted_locks(session: Session, now: datetime | None = None) -> int:
    """Remove locks for games that turned out not to have started: a game postponed after its
    start time passed shows as scheduled again with a later start. Returns how many were removed."""
    now = now or datetime.now(UTC)
    games = select(Game.id).where(Game.status == "scheduled", Game.start_utc > now)
    removed = session.execute(
        delete(LineupLock).where(LineupLock.game_id.in_(games)).returning(LineupLock.id)
    ).all()
    session.commit()
    return len(removed)


def run_lock_job(session_factory: Callable[[], Session], now: datetime | None = None) -> str:
    """One tick of the worker's lineup job. Returns a summary line for the job log."""
    with session_factory() as session:
        repaired = repair_lineups(session)
        released = release_unstarted_locks(session, now)
        locked = lock_started_games(session, now)
    return f"locked={locked} released={released} repaired={repaired}"


# ---- moving players ----


@dataclass(frozen=True)
class Placed:
    player_id: int
    slot: str


def _can_edit(session: Session, actor: User, team: FantasyTeam) -> bool:
    return team.owner_id == actor.id or leagues.can_manage(session, actor, team.league_id)


def move_player(
    session: Session,
    actor: User,
    team_id: int,
    player_id: int,
    to_slot: str,
    *,
    swap_with: int | None = None,
    now: datetime | None = None,
) -> list[Placed]:
    """Move a player to a slot. If the slot is full, `swap_with` names who goes the other way.

    The team's owner, its commissioner and staff may do it; a commissioner's edit is audited.
    Neither player may have a game that has started today, both must be allowed in their new
    slots, and no slot may go over its limit.
    """
    now = now or datetime.now(UTC)
    team = session.scalar(select(FantasyTeam).where(FantasyTeam.id == team_id).with_for_update())
    if team is None or not _can_edit(session, actor, team):
        raise leagues.PermissionDenied("You cannot change this lineup.")
    if to_slot not in LINEUP_SLOTS:
        raise LineupError("Unknown lineup slot.")
    league = session.get(League, team.league_id)
    if league.status == "setup":
        raise LineupError("Lineups open once the draft starts.")
    rows = _rows(session, team_id)
    by_player = {p.id: (slot, p) for slot, p in rows}
    if player_id not in by_player:
        raise LineupError("That player is not on this roster.")
    slot_row, player = by_player[player_id]
    if slot_row.slot == to_slot:
        return [Placed(s.player_id, s.slot) for s, _ in rows]
    caps = capacities(_settings(session, team_id))
    used = Counter(s.slot for s, _ in rows)

    involved = [player_id] + ([swap_with] if swap_with is not None else [])
    locked = locked_player_ids(session, involved, league.season_id, now)
    if player_id in locked:
        raise LineupError(f"{_name(player)}'s game has started, so they are locked for today.")
    if not eligible(player.position, to_slot, player.status):
        raise LineupError(_ineligible(player, to_slot))

    if used[to_slot] < caps[to_slot] and swap_with is None:
        slot_row.slot = to_slot
        changes = {player_id: to_slot}
    else:
        if swap_with is None:
            raise LineupError(
                f"The {SLOT_LABELS[to_slot]} spots are full. Choose a player to swap with."
            )
        if swap_with not in by_player or by_player[swap_with][0].slot != to_slot:
            raise LineupError("You can only swap with a player in that slot.")
        partner_row, partner = by_player[swap_with]
        if swap_with in locked:
            raise LineupError(f"{_name(partner)}'s game has started, so they are locked for today.")
        if not eligible(partner.position, slot_row.slot, partner.status):
            raise LineupError(_ineligible(partner, slot_row.slot))
        slot_row.slot, partner_row.slot = to_slot, slot_row.slot
        changes = {player_id: to_slot, swap_with: partner_row.slot}
    if actor.id != team.owner_id:
        session.add(
            AuditLog(
                league_id=team.league_id,
                actor_id=actor.id,
                action="lineup.edit",
                after={"team_id": team_id, "moves": changes},
            )
        )
    session.commit()
    return [Placed(s.player_id, s.slot) for s, _ in _rows(session, team_id)]


def _name(player: Player) -> str:
    return f"{player.first_name} {player.last_name}".strip()


def _ineligible(player: Player, slot: str) -> str:
    if slot == "IR":
        return f"{_name(player)} is not injured, so cannot go on injured reserve."
    return f"{_name(player)} cannot play {SLOT_LABELS[slot]}."


def starters(rows: list[tuple[LineupSlot, Player]]) -> list[tuple[LineupSlot, Player]]:
    return [(s, p) for s, p in rows if s.slot in STARTING_SLOTS]


def team_lineup(session: Session, team_id: int) -> list[tuple[LineupSlot, Player]]:
    return _rows(session, team_id)


def count_by_slot(session: Session, team_id: int) -> dict[str, int]:
    return dict(
        session.execute(
            select(LineupSlot.slot, func.count())
            .where(LineupSlot.team_id == team_id)
            .group_by(LineupSlot.slot)
        ).all()
    )


@dataclass(frozen=True)
class MoveOption:
    """A move a player may make now: to `slot`, swapping with `swap_with` if that slot is full."""

    slot: str
    swap_with: int | None = None
    swap_name: str = ""


def move_options(
    session: Session, team_id: int, now: datetime | None = None
) -> dict[int, list[MoveOption]]:
    """Every move each unlocked player on the team could make right now, using the same rules as
    `move_player`, so the page never offers a move the server would refuse."""
    now = now or datetime.now(UTC)
    rows = _rows(session, team_id)
    caps = capacities(_settings(session, team_id))
    used = Counter(s.slot for s, _ in rows)
    locked = locked_player_ids(session, [p.id for _, p in rows], _season_id(session, team_id), now)
    in_slot: dict[str, list[tuple[LineupSlot, Player]]] = {}
    for row, player in rows:
        in_slot.setdefault(row.slot, []).append((row, player))
    out: dict[int, list[MoveOption]] = {}
    for row, player in rows:
        options: list[MoveOption] = []
        if player.id not in locked:
            for slot in LINEUP_SLOTS:
                if slot == row.slot or not eligible(player.position, slot, player.status):
                    continue
                if used[slot] < caps[slot]:
                    options.append(MoveOption(slot))
                    continue
                for _, other in in_slot.get(slot, []):
                    if other.id not in locked and eligible(other.position, row.slot, other.status):
                        options.append(MoveOption(slot, other.id, _name(other)))
        out[player.id] = options
    return out
