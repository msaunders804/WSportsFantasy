"""The real, database-backed draft (Plan 4.6). Business rules only, no HTTP.

Every operation that changes a draft (pick, autopick, pause, resume, undo) first locks the draft
row with SELECT ... FOR UPDATE, so they run one at a time per draft. Unique constraints on
(draft, overall pick), (draft, player) and (league, active roster player) are the backstop: even
if this module had a bug, the database refuses a double pick.

Time is passed in as `now` so the clock rules can be tested without sleeping. Position rules reuse
the league's settings: a team may not take more than `max_goalies` goalies, and may not make a
pick that leaves it unable to fill its starting lineup in the picks it has left.
"""

import logging
import random
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from undertow.data.models import (
    AuditLog,
    Draft,
    DraftQueueEntry,
    DraftSelection,
    FantasyTeam,
    League,
    LeagueMember,
    LeagueSettings,
    Player,
    RosterEntry,
    User,
)
from undertow.fantasy import leagues
from undertow.fantasy.draft import snake_slot

log = logging.getLogger(__name__)

DEFAULT_PICK_SECONDS = 90
MIN_PICK_SECONDS = 10
MAX_PICK_SECONDS = 300
MAX_QUEUE = 60

# Best player first. Given the session, returns player ids; the caller decides how to rank.
Ranking = Callable[[Session], list[int]]


class DraftError(leagues.LeagueError):
    """A draft rule was broken. The message is safe to show to the person."""


@dataclass(frozen=True)
class PickResult:
    pick: DraftSelection
    complete: bool


def _audit(session: Session, league_id: int, actor_id: int | None, action: str, after=None) -> None:
    session.add(AuditLog(league_id=league_id, actor_id=actor_id, action=action, after=after))


def _locked_draft(session: Session, draft_id: int) -> Draft:
    draft = session.scalar(select(Draft).where(Draft.id == draft_id).with_for_update())
    if draft is None:
        raise DraftError("Draft not found.")
    return draft


def draft_for_league(session: Session, league_id: int) -> Draft | None:
    return session.scalar(select(Draft).where(Draft.league_id == league_id))


def team_on_clock(draft: Draft) -> int | None:
    """The fantasy_team id whose turn it is, or None once the draft is complete."""
    if draft.status == "complete":
        return None
    return draft.order[snake_slot(draft.current_pick, len(draft.order))[1]]


def total_picks(draft: Draft) -> int:
    return draft.rounds * len(draft.order)


# ---- starting a draft ----


def start_draft(
    session: Session,
    actor: User,
    league_id: int,
    *,
    now: datetime | None = None,
    pick_seconds: int = DEFAULT_PICK_SECONDS,
    seed: int | None = None,
) -> Draft:
    """Commissioner or staff starts the league's draft. The league must be in setup and full.

    The draft order is a seeded shuffle of the league's teams (the seed is stored). The first
    team goes on the clock immediately. The league moves to `drafting`, which locks its settings.
    """
    leagues.require_manager(session, actor, league_id)
    now = now or datetime.now(UTC)
    league = session.scalar(select(League).where(League.id == league_id).with_for_update())
    if league is None:
        raise DraftError("League not found.")
    if league.status != "setup" or draft_for_league(session, league_id) is not None:
        raise DraftError("This league's draft has already started.")
    if not MIN_PICK_SECONDS <= pick_seconds <= MAX_PICK_SECONDS:
        raise DraftError(
            f"The pick timer must be between {MIN_PICK_SECONDS} and {MAX_PICK_SECONDS} seconds."
        )
    team_ids = list(
        session.scalars(select(FantasyTeam.id).where(FantasyTeam.league_id == league_id))
    )
    members = session.scalar(
        select(func.count()).select_from(LeagueMember).where(LeagueMember.league_id == league_id)
    )
    if len(team_ids) < league.size or members < league.size:
        raise DraftError(
            f"The league is not full yet ({len(team_ids)} of {league.size} teams). "
            "Every seat needs a team before the draft can start."
        )
    settings = session.get(LeagueSettings, league_id)
    rounds = (
        settings.roster_f
        + settings.roster_d
        + settings.roster_util
        + settings.roster_g
        + settings.roster_bench
    )
    seed = random.SystemRandom().randrange(1_000_000_000) if seed is None else seed
    order = sorted(team_ids)
    random.Random(seed).shuffle(order)

    draft = Draft(
        league_id=league_id,
        status="live",
        pick_seconds=pick_seconds,
        rounds=rounds,
        order=order,
        order_seed=seed,
        current_pick=1,
        deadline=now + timedelta(seconds=pick_seconds),
        started_at=now,
    )
    session.add(draft)
    league.status = "drafting"
    # Waiver priority is the reverse of the draft order (Plan 4.7).
    for place, team_id in enumerate(reversed(order), start=1):
        session.get(FantasyTeam, team_id).waiver_priority = place
    _audit(
        session,
        league_id,
        actor.id,
        "draft.start",
        {"order": order, "seed": seed, "rounds": rounds, "pick_seconds": pick_seconds},
    )
    session.commit()
    return draft


# ---- picking ----


def _roster_counts(session: Session, league_id: int, team_id: int) -> Counter[str]:
    rows = session.execute(
        select(Player.position, func.count())
        .join(RosterEntry, RosterEntry.player_id == Player.id)
        .where(
            RosterEntry.league_id == league_id,
            RosterEntry.team_id == team_id,
            RosterEntry.released_at.is_(None),
        )
        .group_by(Player.position)
    )
    return Counter({pos: n for pos, n in rows})


def check_pick_allowed(
    counts: Counter[str], position: str, settings: LeagueSettings, picks_left: int
) -> str | None:
    """Why a team may not take a player at `position`, or None if it may.

    `counts` is the team's roster by position before the pick and `picks_left` its picks still to
    make, including this one. A pick may not break the goalie cap, and must leave enough picks to
    still fill the starting lineup (forwards include the flex spot, as in the draft model).
    """
    if position == "G" and counts["G"] >= settings.max_goalies:
        return f"You can have at most {settings.max_goalies} goalies."
    after = counts.copy()
    after[position] += 1
    wanted = {
        "F": settings.roster_f + settings.roster_util,
        "D": settings.roster_d,
        "G": settings.roster_g,
    }
    unmet = sum(max(0, wanted[pos] - after[pos]) for pos in wanted)
    if unmet > picks_left - 1:
        missing = [pos for pos in wanted if after[pos] < wanted[pos]]
        return (
            "That pick would leave you unable to fill your starting lineup. "
            f"You still need a {' and a '.join(missing)}."
        )
    return None


def _make_pick(
    session: Session,
    draft: Draft,
    team_id: int,
    player_id: int,
    now: datetime,
    *,
    autopick: bool,
) -> PickResult:
    """Record one pick for the team on the clock. The caller holds the draft lock."""
    if draft.status == "paused":
        raise DraftError("The draft is paused.")
    if draft.status != "live":
        raise DraftError("The draft is not running.")
    if team_on_clock(draft) != team_id:
        raise DraftError("It is not your turn.")
    player = session.get(Player, player_id)
    if player is None or player.position is None or player.status != "active":
        raise DraftError("That player cannot be drafted.")
    taken = session.scalar(
        select(DraftSelection.id).where(
            DraftSelection.draft_id == draft.id, DraftSelection.player_id == player_id
        )
    )
    if taken is not None:
        raise DraftError("That player was just taken.")

    settings = session.get(LeagueSettings, draft.league_id)
    counts = _roster_counts(session, draft.league_id, team_id)
    picks_left = draft.rounds - sum(counts.values())
    problem = check_pick_allowed(counts, player.position, settings, picks_left)
    if problem:
        raise DraftError(problem)

    overall = draft.current_pick
    rnd = snake_slot(overall, len(draft.order))[0]
    pick = DraftSelection(
        draft_id=draft.id,
        overall_no=overall,
        round=rnd,
        team_id=team_id,
        player_id=player_id,
        picked_at=now,
        is_autopick=autopick,
    )
    session.add(pick)
    session.add(
        RosterEntry(
            league_id=draft.league_id,
            team_id=team_id,
            player_id=player_id,
            acquired_via="draft",
            acquired_at=now,
        )
    )
    # A drafted player leaves every team's queue so nobody autopicks a taken player.
    session.execute(delete(DraftQueueEntry).where(DraftQueueEntry.player_id == player_id))
    complete = overall >= total_picks(draft)
    draft.current_pick = overall + 1
    if complete:
        draft.status = "complete"
        draft.deadline = None
        draft.completed_at = now
        league = session.get(League, draft.league_id)
        league.status = "in_season"
    else:
        draft.deadline = now + timedelta(seconds=draft.pick_seconds)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise DraftError("That pick could not be saved. Please try again.") from exc
    return PickResult(pick=pick, complete=complete)


def make_pick(
    session: Session,
    user: User,
    draft_id: int,
    player_id: int,
    *,
    now: datetime | None = None,
) -> PickResult:
    """A manager drafts a player for their own team, when it is their turn."""
    now = now or datetime.now(UTC)
    draft = _locked_draft(session, draft_id)
    team = session.scalar(
        select(FantasyTeam).where(
            FantasyTeam.league_id == draft.league_id, FantasyTeam.owner_id == user.id
        )
    )
    if team is None:
        session.rollback()
        raise DraftError("You do not have a team in this league.")
    try:
        result = _make_pick(session, draft, team.id, player_id, now, autopick=False)
    except DraftError:
        session.rollback()
        raise
    session.commit()
    return result


# ---- the clock and autopick ----


def due_draft_ids(session: Session, now: datetime) -> list[int]:
    """Live drafts whose clock has run out. The worker calls `autopick` for each."""
    return list(
        session.scalars(
            select(Draft.id).where(Draft.status == "live", Draft.deadline <= now).order_by(Draft.id)
        )
    )


def autopick(
    session: Session, draft_id: int, ranking: Ranking, *, now: datetime | None = None
) -> PickResult | None:
    """Pick for the team on the clock if its time has run out. Returns None if nothing was due.

    Takes the first player in the team's queue that is still available and allowed, otherwise the
    best-ranked allowed player. Locks the draft first, so a manual pick that lands at the same
    moment either wins (and this returns None) or is applied before this runs.
    """
    now = now or datetime.now(UTC)
    draft = _locked_draft(session, draft_id)
    if draft.status != "live" or draft.deadline is None or draft.deadline > now:
        session.rollback()
        return None
    team_id = team_on_clock(draft)
    settings = session.get(LeagueSettings, draft.league_id)
    counts = _roster_counts(session, draft.league_id, team_id)
    picks_left = draft.rounds - sum(counts.values())
    drafted = set(
        session.scalars(select(DraftSelection.player_id).where(DraftSelection.draft_id == draft.id))
    )
    queued = list(
        session.scalars(
            select(DraftQueueEntry.player_id)
            .where(DraftQueueEntry.team_id == team_id)
            .order_by(DraftQueueEntry.rank)
        )
    )
    candidates = [pid for pid in queued if pid not in drafted] + [
        pid for pid in ranking(session) if pid not in drafted
    ]
    positions = dict(
        session.execute(
            select(Player.id, Player.position).where(
                Player.id.in_(candidates), Player.status == "active", Player.position.is_not(None)
            )
        ).all()
    )
    for pid in candidates:
        position = positions.get(pid)
        if position is None or check_pick_allowed(counts, position, settings, picks_left):
            continue
        result = _make_pick(session, draft, team_id, pid, now, autopick=True)
        session.commit()
        return result
    session.rollback()
    raise DraftError("No available player can be drafted for the team on the clock.")


def process_due_drafts(
    session_factory: Callable[[], Session], ranking: Ranking, *, now: datetime | None = None
) -> int:
    """One tick of the draft clock: autopick in every draft whose timer has run out.

    Called by the worker every second. Each draft is handled on its own session and its own
    errors, so one stuck draft cannot stop the others. The ranking is computed at most once per
    tick. Returns how many picks were made.
    """
    now = now or datetime.now(UTC)
    with session_factory() as session:
        due = due_draft_ids(session, now)
    if not due:
        return 0
    cached: list[list[int]] = []

    def ranking_once(session: Session) -> list[int]:
        if not cached:
            cached.append(ranking(session))
        return cached[0]

    made = 0
    for draft_id in due:
        try:
            with session_factory() as session:
                if autopick(session, draft_id, ranking_once, now=now) is not None:
                    made += 1
        except Exception:
            log.exception("autopick failed for draft %s", draft_id)
    return made


# ---- commissioner controls ----


def pause(session: Session, actor: User, draft_id: int, *, now: datetime | None = None) -> Draft:
    now = now or datetime.now(UTC)
    draft = _locked_draft(session, draft_id)
    leagues.require_manager(session, actor, draft.league_id)
    if draft.status != "live":
        session.rollback()
        raise DraftError("Only a running draft can be paused.")
    draft.paused_remaining = max(0, int((draft.deadline - now).total_seconds()))
    draft.deadline = None
    draft.status = "paused"
    _audit(session, draft.league_id, actor.id, "draft.pause", {"pick": draft.current_pick})
    session.commit()
    return draft


def resume(session: Session, actor: User, draft_id: int, *, now: datetime | None = None) -> Draft:
    now = now or datetime.now(UTC)
    draft = _locked_draft(session, draft_id)
    leagues.require_manager(session, actor, draft.league_id)
    if draft.status != "paused":
        session.rollback()
        raise DraftError("The draft is not paused.")
    remaining = draft.paused_remaining if draft.paused_remaining is not None else draft.pick_seconds
    draft.deadline = now + timedelta(seconds=max(remaining, 5))
    draft.paused_remaining = None
    draft.status = "live"
    _audit(session, draft.league_id, actor.id, "draft.resume", {"pick": draft.current_pick})
    session.commit()
    return draft


def undo_last_pick(
    session: Session, actor: User, draft_id: int, *, now: datetime | None = None
) -> DraftSelection:
    """Commissioner takes back the most recent pick; that team is on the clock again with a
    fresh timer. Works on a live or paused draft, and on a just-completed one (it reopens)."""
    now = now or datetime.now(UTC)
    draft = _locked_draft(session, draft_id)
    leagues.require_manager(session, actor, draft.league_id)
    last = session.scalar(
        select(DraftSelection)
        .where(DraftSelection.draft_id == draft.id)
        .order_by(DraftSelection.overall_no.desc())
        .limit(1)
    )
    if last is None:
        session.rollback()
        raise DraftError("There is no pick to undo.")
    session.execute(
        delete(RosterEntry).where(
            RosterEntry.league_id == draft.league_id,
            RosterEntry.team_id == last.team_id,
            RosterEntry.player_id == last.player_id,
            RosterEntry.acquired_via == "draft",
            RosterEntry.released_at.is_(None),
        )
    )
    session.delete(last)
    session.flush()
    if draft.status == "complete":
        league = session.get(League, draft.league_id)
        league.status = "drafting"
        draft.completed_at = None
    draft.current_pick = last.overall_no
    if draft.status == "paused":
        draft.paused_remaining = draft.pick_seconds
    else:
        draft.status = "live"
        draft.deadline = now + timedelta(seconds=draft.pick_seconds)
    _audit(
        session,
        draft.league_id,
        actor.id,
        "draft.undo",
        {"overall_no": last.overall_no, "team_id": last.team_id, "player_id": last.player_id},
    )
    session.commit()
    return last


# ---- queue ----


def set_queue(session: Session, user: User, draft_id: int, player_ids: list[int]) -> list[int]:
    """Replace the caller's draft queue with `player_ids` in order. Drafted players and unknown
    ones are dropped, and repeats keep their first position."""
    draft = session.get(Draft, draft_id)
    if draft is None:
        raise DraftError("Draft not found.")
    team = session.scalar(
        select(FantasyTeam).where(
            FantasyTeam.league_id == draft.league_id, FantasyTeam.owner_id == user.id
        )
    )
    if team is None:
        raise DraftError("You do not have a team in this league.")
    if len(player_ids) > MAX_QUEUE:
        raise DraftError(f"A queue can hold at most {MAX_QUEUE} players.")
    drafted = set(
        session.scalars(select(DraftSelection.player_id).where(DraftSelection.draft_id == draft_id))
    )
    known = set(session.scalars(select(Player.id).where(Player.id.in_(player_ids))))
    clean: list[int] = []
    for pid in player_ids:
        if pid in known and pid not in drafted and pid not in clean:
            clean.append(pid)
    session.execute(delete(DraftQueueEntry).where(DraftQueueEntry.team_id == team.id))
    session.add_all(
        DraftQueueEntry(team_id=team.id, player_id=pid, rank=i) for i, pid in enumerate(clean, 1)
    )
    session.commit()
    return clean
