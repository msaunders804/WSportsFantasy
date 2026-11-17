"""What the draft room shows: the page data and the live state the browser polls.

Read-only and free of HTTP. The rules that change a draft live in `draft_room`; the pure values
and recommendations come from `draft` and `rankings`.
"""

import logging
import math
import threading
import time
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from undertow.data.models import (
    Draft,
    DraftQueueEntry,
    DraftSelection,
    FantasyTeam,
    League,
    LeagueSettings,
    User,
)
from undertow.fantasy import draft, draft_room, leagues, rankings

log = logging.getLogger(__name__)

# The ranked pool comes from last season's box scores, which do not change during a draft, so it
# is read once per season and reused. Every manager's page polls, and recomputing it would be
# wasteful.
POOL_TTL_SECONDS = 300
_pool_lock = threading.Lock()
_pool_cache: dict[int, tuple[float, list[draft.DraftPlayer], str | None]] = {}


def clear_pool_cache() -> None:
    with _pool_lock:
        _pool_cache.clear()


def cached_basis(session: Session, season_id: int) -> tuple[list[draft.DraftPlayer], str | None]:
    """(the draft pool, the name of the season its ratings come from or None) for a league's
    season."""
    with _pool_lock:
        now = time.monotonic()
        hit = _pool_cache.get(season_id)
        if hit is None or now - hit[0] > POOL_TTL_SECONDS:
            history = rankings.history_season_ids(session, season_id)
            hit = (
                now,
                rankings.draft_pool(session, history),
                rankings.basis_name(session, history),
            )
            _pool_cache[season_id] = hit
        return hit[1], hit[2]


def cached_pool(session: Session, season_id: int) -> list[draft.DraftPlayer]:
    return cached_basis(session, season_id)[0]


def draft_config(settings: LeagueSettings, teams: int) -> draft.DraftConfig:
    """The demo model's config for this league (flex counts as a forward, as in the model)."""
    return draft.DraftConfig(
        teams=teams,
        need_f=settings.roster_f + settings.roster_util,
        need_d=settings.roster_d,
        need_g=settings.roster_g,
        bench=settings.roster_bench,
        max_goalies=settings.max_goalies,
    )


def my_team(session: Session, user: User, league_id: int) -> FantasyTeam | None:
    return session.scalar(
        select(FantasyTeam).where(
            FantasyTeam.league_id == league_id, FantasyTeam.owner_id == user.id
        )
    )


def team_rows(session: Session, order: list[int]) -> list[dict]:
    """Teams in draft order, with the manager's name."""
    rows = session.execute(
        select(FantasyTeam.id, FantasyTeam.name, User.display_name)
        .join(User, User.id == FantasyTeam.owner_id)
        .where(FantasyTeam.id.in_(order))
    ).all()
    by_id = {tid: {"id": tid, "name": name, "owner": owner} for tid, name, owner in rows}
    return [by_id[tid] for tid in order]


def page_data(session: Session, league: League, dr: Draft, user: User) -> dict:
    """Everything that does not change while the draft runs. Sent once with the page."""
    settings = session.get(LeagueSettings, league.id)
    cfg = draft_config(settings, len(dr.order))
    pool, basis = cached_basis(session, league.season_id)
    vals = draft.valuations(pool, cfg)
    ranked = sorted(pool, key=lambda p: (vals[p.id].rank, p.id))
    mine = my_team(session, user, league.id)
    return {
        "league_id": league.id,
        "league_name": league.name,
        "teams": team_rows(session, list(dr.order)),
        "me": dr.order.index(mine.id) if mine and mine.id in dr.order else -1,
        "rounds": dr.rounds,
        "pick_seconds": dr.pick_seconds,
        "ranking_basis": basis,
        "slots": {
            "F": settings.roster_f,
            "D": settings.roster_d,
            "UTIL": settings.roster_util,
            "G": settings.roster_g,
            "BN": settings.roster_bench,
        },
        "pool": [
            {
                "id": p.id,
                "name": p.name,
                "pos": p.pos,
                "team": p.team,
                "fpg": round(p.fpg, 1),
                "gp": p.gp,
                "rank": vals[p.id].rank,
                "pos_rank": vals[p.id].pos_rank,
            }
            for p in ranked
        ],
    }


def state(
    session: Session, league: League, dr: Draft, user: User, *, now: datetime | None = None
) -> dict:
    """The live state the browser polls. Small, and cheap to build."""
    now = now or datetime.now(UTC)
    settings = session.get(LeagueSettings, league.id)
    picks = session.execute(
        select(DraftSelection.overall_no, DraftSelection.team_id, DraftSelection.player_id)
        .where(DraftSelection.draft_id == dr.id)
        .order_by(DraftSelection.overall_no)
    ).all()
    auto = set(
        session.scalars(
            select(DraftSelection.overall_no).where(
                DraftSelection.draft_id == dr.id, DraftSelection.is_autopick.is_(True)
            )
        )
    )
    mine = my_team(session, user, league.id)
    on_clock = draft_room.team_on_clock(dr)
    if dr.status == "live" and dr.deadline is not None:
        seconds_left = max(0, math.ceil((dr.deadline - now).total_seconds()))
    elif dr.status == "paused":
        seconds_left = dr.paused_remaining
    else:
        seconds_left = None
    n = len(dr.order)
    out = {
        "status": dr.status,
        "pick_no": dr.current_pick,
        "round": min(draft.snake_slot(dr.current_pick, n)[0], dr.rounds)
        if dr.status != "complete"
        else dr.rounds,
        "on_clock": on_clock,
        "seconds_left": seconds_left,
        "picks": [[no, tid, pid, no in auto] for no, tid, pid in picks],
        "my_team": mine.id if mine else None,
        "can_manage": leagues.can_manage(session, user, league.id),
        "queue": [],
        "recommend": [],
        "needs": None,
        "roster": None,
        "version": f"{dr.status}:{dr.current_pick}",
    }
    if mine is None or mine.id not in dr.order:
        return out
    out["queue"] = list(
        session.scalars(
            select(DraftQueueEntry.player_id)
            .where(DraftQueueEntry.team_id == mine.id)
            .order_by(DraftQueueEntry.rank)
        )
    )
    cfg = draft_config(settings, n)
    pool = cached_pool(session, league.season_id)
    try:
        replayed = draft.replay([pid for _, _, pid in picks], pool, cfg)
    except ValueError:
        # A drafted player has since left the active pool. The room still works; only the
        # suggestions and roster summary are unavailable until it is fixed.
        log.warning("draft %s could not be replayed for suggestions", dr.id, exc_info=True)
        return out
    me = dr.order.index(mine.id)
    roster = draft.assign_slots(replayed.rosters[me], cfg)
    out["roster"] = {k: [p.id for p in v] for k, v in roster.items()}
    out["needs"] = {
        **draft.needs(replayed.rosters[me], cfg),
        "picks_left": dr.rounds - len(replayed.rosters[me]),
    }
    if on_clock == mine.id and dr.status == "live":
        vals = draft.valuations(pool, cfg)
        out["recommend"] = [
            {"id": r.player.id, "tag": r.tag, "reason": r.reason}
            for r in draft.recommend(replayed, me, vals, cfg, n=3)
        ]
    return out
