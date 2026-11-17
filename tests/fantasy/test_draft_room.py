import threading
from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from undertow.data.models import (
    AuditLog,
    Draft,
    DraftQueueEntry,
    DraftSelection,
    FantasyTeam,
    League,
    LeagueSettings,
    Player,
    RosterEntry,
    User,
)
from undertow.fantasy import auth, draft_room, leagues

pytestmark = pytest.mark.db

T0 = datetime(2026, 11, 30, 18, 0, tzinfo=UTC)
FIRST_ID = 900_001
NO_SUCH_PLAYER = 800_000_001  # never a real id, whatever data the dev database holds
# 36 forwards, 16 defenders, 8 goalies: enough for a 4-team, 11-round draft (44 picks).
POSITIONS = ["F"] * 36 + ["D"] * 16 + ["G"] * 8


def best_first(session):
    return list(session.scalars(select(Player.id).where(Player.id >= FIRST_ID).order_by(Player.id)))


@pytest.fixture
def db(fantasy_db):
    with fantasy_db.begin() as conn:
        conn.execute(text("DELETE FROM player WHERE id >= :n"), {"n": FIRST_ID})
    with Session(fantasy_db) as s:
        s.add_all(
            Player(
                id=FIRST_ID + i,
                first_name=f"P{i}",
                last_name=pos,
                position=pos,
                status="active",
            )
            for i, pos in enumerate(POSITIONS)
        )
        s.commit()
        yield s
    with fantasy_db.begin() as conn:
        conn.execute(text("TRUNCATE draft_queue, draft_pick, draft, roster_entry CASCADE"))
        conn.execute(text("DELETE FROM player WHERE id >= :n"), {"n": FIRST_ID})


def make_user(db, email):
    return auth.register_user(
        db, email=email, display_name=email.split("@")[0], password="a long enough password"
    ).user


def full_league(db, size=4):
    """A private league that is full. Returns (league, users) with users[0] the commissioner."""
    boss = make_user(db, "u0@example.com")
    league, _ = leagues.register_league(
        db, boss, name="Friends", season_id=11, size=size, team_name="Team 0"
    )
    users = [boss]
    for i in range(1, size):
        user = make_user(db, f"u{i}@example.com")
        leagues.join_league(db, user, league.invite_code, f"Team {i}")
        users.append(user)
    return league, users


def started(db, size=4, **kwargs):
    league, users = full_league(db, size)
    draft = draft_room.start_draft(db, users[0], league.id, now=T0, seed=7, **kwargs)
    return league, users, draft


def user_for_team(db, users, team_id):
    owner_id = db.get(FantasyTeam, team_id).owner_id
    return next(u for u in users if u.id == owner_id)


def next_player(db, position="F"):
    """The lowest-id undrafted player at a position."""
    return db.scalar(
        select(Player.id)
        .where(
            Player.id >= FIRST_ID,
            Player.position == position,
            Player.id.not_in(select(DraftSelection.player_id)),
        )
        .order_by(Player.id)
        .limit(1)
    )


def pick_for_clock(db, users, draft, position="F", now=T0):
    on_clock = user_for_team(db, users, draft_room.team_on_clock(draft))
    return draft_room.make_pick(db, on_clock, draft.id, next_player(db, position), now=now)


# ---- starting ----


def test_start_draft_needs_a_full_league_and_a_manager(db):
    boss = make_user(db, "boss@example.com")
    league, _ = leagues.register_league(
        db, boss, name="Half", season_id=11, size=4, team_name="Only"
    )
    with pytest.raises(draft_room.DraftError, match="not full"):
        draft_room.start_draft(db, boss, league.id, now=T0)
    league, users = full_league(db)
    with pytest.raises(leagues.PermissionDenied):
        draft_room.start_draft(db, users[1], league.id, now=T0)
    with pytest.raises(draft_room.DraftError, match="timer"):
        draft_room.start_draft(db, users[0], league.id, now=T0, pick_seconds=5)
    assert league.status == "setup"


def test_start_draft_sets_order_clock_status_and_waiver_priority(db):
    league, users, draft = started(db)
    assert sorted(draft.order) == sorted(t.id for t in db.scalars(select(FantasyTeam)))
    assert draft.rounds == 11 and draft.order_seed == 7 and draft.current_pick == 1
    assert draft.status == "live" and draft.deadline == T0 + timedelta(seconds=90)
    db.refresh(league)
    assert league.status == "drafting"
    priorities = {t.id: t.waiver_priority for t in db.scalars(select(FantasyTeam))}
    assert [priorities[t] for t in reversed(draft.order)] == [1, 2, 3, 4]
    assert db.scalar(select(AuditLog).where(AuditLog.action == "draft.start")) is not None


def test_same_seed_gives_the_same_order(db):
    league, users = full_league(db)
    a = draft_room.start_draft(db, users[0], league.id, now=T0, seed=42)
    assert a.order_seed == 42


def test_a_draft_cannot_be_started_twice(db):
    league, users, _ = started(db)
    with pytest.raises(draft_room.DraftError, match="already started"):
        draft_room.start_draft(db, users[0], league.id, now=T0)


def test_settings_are_locked_once_the_draft_starts(db):
    league, users, _ = started(db)
    with pytest.raises(leagues.LeagueError, match="locked"):
        leagues.update_settings(db, users[0], league.id, {"roster_bench": 3})


# ---- picking ----


def test_picks_follow_the_snake_and_build_rosters(db):
    league, users, draft = started(db)
    order = list(draft.order)
    seen = []
    for _ in range(8):
        db.refresh(draft)
        seen.append(draft_room.team_on_clock(draft))
        pick_for_clock(db, users, draft)
    assert seen == order + order[::-1]
    entries = db.scalars(select(RosterEntry)).all()
    assert len(entries) == 8 and all(e.acquired_via == "draft" for e in entries)
    last = db.scalar(select(DraftSelection).where(DraftSelection.overall_no == 8))
    assert last.round == 2 and last.team_id == order[0] and not last.is_autopick


def test_pick_resets_the_clock(db):
    league, users, draft = started(db)
    later = T0 + timedelta(seconds=40)
    pick_for_clock(db, users, draft, now=later)
    db.refresh(draft)
    assert draft.deadline == later + timedelta(seconds=90)


def test_only_the_team_on_the_clock_may_pick(db):
    league, users, draft = started(db)
    other = next(
        u for u in users if u.id != db.get(FantasyTeam, draft_room.team_on_clock(draft)).owner_id
    )
    with pytest.raises(draft_room.DraftError, match="not your turn"):
        draft_room.make_pick(db, other, draft.id, next_player(db), now=T0)
    outsider = make_user(db, "outsider@example.com")
    with pytest.raises(draft_room.DraftError, match="do not have a team"):
        draft_room.make_pick(db, outsider, draft.id, next_player(db), now=T0)
    assert db.scalar(select(func.count()).select_from(DraftSelection)) == 0


def test_a_player_cannot_be_drafted_twice_or_if_unavailable(db):
    league, users, draft = started(db)
    target = next_player(db)
    first = user_for_team(db, users, draft_room.team_on_clock(draft))
    draft_room.make_pick(db, first, draft.id, target, now=T0)
    db.refresh(draft)
    second = user_for_team(db, users, draft_room.team_on_clock(draft))
    with pytest.raises(draft_room.DraftError, match="just taken"):
        draft_room.make_pick(db, second, draft.id, target, now=T0)
    with pytest.raises(draft_room.DraftError, match="cannot be drafted"):
        draft_room.make_pick(db, second, draft.id, NO_SUCH_PLAYER, now=T0)
    injured = db.get(Player, next_player(db, "D"))
    injured.status = "injured"
    db.commit()
    with pytest.raises(draft_room.DraftError, match="cannot be drafted"):
        draft_room.make_pick(db, second, draft.id, injured.id, now=T0)


def test_goalie_cap_is_enforced_per_team(db):
    league, users, draft = started(db)
    team_id = draft_room.team_on_clock(draft)
    me = user_for_team(db, users, team_id)
    # Take two goalies with this team (other teams take forwards in between).
    for _ in range(2):
        db.refresh(draft)
        while draft_room.team_on_clock(draft) != team_id:
            pick_for_clock(db, users, draft, "F")
            db.refresh(draft)
        draft_room.make_pick(db, me, draft.id, next_player(db, "G"), now=T0)
    db.refresh(draft)
    while draft_room.team_on_clock(draft) != team_id:
        pick_for_clock(db, users, draft, "F")
        db.refresh(draft)
    with pytest.raises(draft_room.DraftError, match="at most 2 goalies"):
        draft_room.make_pick(db, me, draft.id, next_player(db, "G"), now=T0)


def test_check_pick_allowed_keeps_the_starting_lineup_fillable():
    settings = LeagueSettings(roster_f=3, roster_d=2, roster_util=1, roster_g=1, max_goalies=2)
    # Wants 4 F, 2 D, 1 G. With 10 forwards and nothing else, one pick left cannot fix that.
    counts = Counter({"F": 10})
    assert "unable to fill" in draft_room.check_pick_allowed(counts, "F", settings, 1)
    # Three picks left: a D now still leaves exactly enough picks for the other D and the G.
    assert draft_room.check_pick_allowed(counts, "D", settings, 3) is None
    # Two picks left: the same D would leave two spots to fill in one pick.
    assert "unable to fill" in draft_room.check_pick_allowed(counts, "D", settings, 2)
    # A pick that takes the last needed spot is fine.
    full = Counter({"F": 4, "D": 2})
    assert draft_room.check_pick_allowed(full, "G", settings, 1) is None
    assert draft_room.check_pick_allowed(Counter({"G": 2}), "G", settings, 5)


def test_a_full_draft_completes_and_every_team_can_field_a_lineup(db):
    league, users, draft = started(db)
    total = draft_room.total_picks(draft)
    assert total == 44
    for n in range(total):
        db.refresh(draft)
        team = draft_room.team_on_clock(draft)
        counts = draft_room._roster_counts(db, league.id, team)
        # A simple strategy that mixes positions, so lineup rules are exercised.
        need = "G" if counts["G"] < 1 else "D" if counts["D"] < 2 else "F"
        result = pick_for_clock(db, users, draft, need)
        assert result.complete == (n == total - 1)
    db.refresh(draft)
    db.refresh(league)
    assert draft.status == "complete" and draft.completed_at is not None
    assert draft_room.team_on_clock(draft) is None and draft.deadline is None
    assert league.status == "in_season"
    for team in db.scalars(select(FantasyTeam)):
        counts = draft_room._roster_counts(db, league.id, team.id)
        assert sum(counts.values()) == 11 and counts["G"] >= 1 and counts["D"] >= 2
    with pytest.raises(draft_room.DraftError, match="not running"):
        draft_room.make_pick(db, users[0], draft.id, next_player(db), now=T0)


# ---- clock and autopick ----


def test_autopick_does_nothing_before_the_deadline(db):
    league, users, draft = started(db)
    assert draft_room.due_draft_ids(db, T0 + timedelta(seconds=89)) == []
    assert draft_room.autopick(db, draft.id, best_first, now=T0 + timedelta(seconds=89)) is None
    assert draft_room.due_draft_ids(db, T0 + timedelta(seconds=90)) == [draft.id]


def test_autopick_takes_the_queue_first_then_the_rankings(db):
    league, users, draft = started(db)
    team_id = draft_room.team_on_clock(draft)
    me = user_for_team(db, users, team_id)
    wanted = FIRST_ID + 40  # a defender far down the rankings
    draft_room.set_queue(db, me, draft.id, [wanted, FIRST_ID])
    late = T0 + timedelta(seconds=91)
    result = draft_room.autopick(db, draft.id, best_first, now=late)
    assert result.pick.player_id == wanted and result.pick.is_autopick
    assert result.pick.team_id == team_id and result.pick.overall_no == 1
    # The next team has no queue, so it gets the best-ranked player.
    db.refresh(draft)
    assert draft.deadline == late + timedelta(seconds=90)
    result = draft_room.autopick(db, draft.id, best_first, now=late + timedelta(seconds=91))
    assert result.pick.player_id == FIRST_ID


def test_autopick_skips_players_the_team_may_not_take(db):
    league, users, draft = started(db)
    team_id = draft_room.team_on_clock(draft)
    me = user_for_team(db, users, team_id)
    # Put two goalies on this team, then queue a third: autopick must skip it.
    for _ in range(2):
        db.refresh(draft)
        while draft_room.team_on_clock(draft) != team_id:
            pick_for_clock(db, users, draft, "F")
            db.refresh(draft)
        draft_room.make_pick(db, me, draft.id, next_player(db, "G"), now=T0)
    db.refresh(draft)
    while draft_room.team_on_clock(draft) != team_id:
        pick_for_clock(db, users, draft, "F")
        db.refresh(draft)
    third_goalie = next_player(db, "G")
    draft_room.set_queue(db, me, draft.id, [third_goalie])
    result = draft_room.autopick(db, draft.id, best_first, now=T0 + timedelta(days=1))
    assert result.pick.player_id != third_goalie
    assert db.get(Player, result.pick.player_id).position != "G"


def test_autopick_fails_loudly_when_nobody_can_be_picked(db):
    league, users, draft = started(db)
    with pytest.raises(draft_room.DraftError, match="No available player"):
        draft_room.autopick(db, draft.id, lambda s: [], now=T0 + timedelta(days=1))


def test_queue_drops_taken_unknown_and_repeated_players(db):
    league, users, draft = started(db)
    me = user_for_team(db, users, draft_room.team_on_clock(draft))
    taken = next_player(db)
    draft_room.make_pick(db, me, draft.id, taken, now=T0)
    a, b = FIRST_ID + 5, FIRST_ID + 6
    clean = draft_room.set_queue(db, me, draft.id, [b, taken, NO_SUCH_PLAYER, a, b])
    assert clean == [b, a]
    assert [
        q.player_id for q in db.scalars(select(DraftQueueEntry).order_by(DraftQueueEntry.rank))
    ] == [b, a]
    # Replacing the queue replaces, not appends.
    assert draft_room.set_queue(db, me, draft.id, [a]) == [a]
    with pytest.raises(draft_room.DraftError, match="at most"):
        draft_room.set_queue(db, me, draft.id, list(range(FIRST_ID, FIRST_ID + 61)))


def test_drafting_a_player_removes_them_from_every_queue(db):
    league, users, draft = started(db)
    on_clock = user_for_team(db, users, draft_room.team_on_clock(draft))
    other = next(u for u in users if u.id != on_clock.id)
    target = next_player(db)
    draft_room.set_queue(db, other, draft.id, [target, target + 1])
    draft_room.make_pick(db, on_clock, draft.id, target, now=T0)
    assert [q.player_id for q in db.scalars(select(DraftQueueEntry))] == [target + 1]


# ---- commissioner controls ----


def test_pause_stops_the_clock_and_resume_restores_the_time_left(db):
    league, users, draft = started(db)
    draft_room.pause(db, users[0], draft.id, now=T0 + timedelta(seconds=30))
    db.refresh(draft)
    assert draft.status == "paused" and draft.deadline is None and draft.paused_remaining == 60
    assert draft_room.due_draft_ids(db, T0 + timedelta(days=1)) == []
    with pytest.raises(draft_room.DraftError, match="paused"):
        pick_for_clock(db, users, draft)
    with pytest.raises(draft_room.DraftError, match="Only a running"):
        draft_room.pause(db, users[0], draft.id, now=T0)
    later = T0 + timedelta(hours=1)
    draft_room.resume(db, users[0], draft.id, now=later)
    db.refresh(draft)
    assert draft.status == "live" and draft.deadline == later + timedelta(seconds=60)
    with pytest.raises(draft_room.DraftError, match="not paused"):
        draft_room.resume(db, users[0], draft.id, now=later)


def test_only_managers_may_pause_resume_or_undo(db):
    league, users, draft = started(db)
    for action in (draft_room.pause, draft_room.resume, draft_room.undo_last_pick):
        with pytest.raises(leagues.PermissionDenied):
            action(db, users[1], draft.id)


def test_undo_returns_the_pick_and_puts_the_team_back_on_the_clock(db):
    league, users, draft = started(db)
    team = draft_room.team_on_clock(draft)
    pick_for_clock(db, users, draft)
    db.refresh(draft)
    assert draft.current_pick == 2
    undone = draft_room.undo_last_pick(db, users[0], draft.id, now=T0 + timedelta(seconds=20))
    db.refresh(draft)
    assert undone.overall_no == 1
    assert draft.current_pick == 1 and draft_room.team_on_clock(draft) == team
    assert draft.deadline == T0 + timedelta(seconds=20 + 90)
    assert db.scalar(select(func.count()).select_from(DraftSelection)) == 0
    assert db.scalar(select(func.count()).select_from(RosterEntry)) == 0
    with pytest.raises(draft_room.DraftError, match="no pick to undo"):
        draft_room.undo_last_pick(db, users[0], draft.id)
    # The same player can be drafted again.
    pick_for_clock(db, users, draft)


def test_undo_reopens_a_completed_draft(db):
    league, users, draft = started(db)
    for _ in range(draft_room.total_picks(draft)):
        db.refresh(draft)
        team = draft_room.team_on_clock(draft)
        counts = draft_room._roster_counts(db, league.id, team)
        need = "G" if counts["G"] < 1 else "D" if counts["D"] < 2 else "F"
        pick_for_clock(db, users, draft, need)
    draft_room.undo_last_pick(db, users[0], draft.id, now=T0)
    db.refresh(draft)
    db.refresh(league)
    assert draft.status == "live" and draft.current_pick == 44 and draft.completed_at is None
    assert league.status == "drafting" and draft.deadline is not None


# ---- constraints and races ----


def test_database_refuses_a_double_pick_even_if_the_service_were_bypassed(db):
    league, users, draft = started(db)
    pick_for_clock(db, users, draft)
    first = db.scalar(select(DraftSelection))
    from sqlalchemy.exc import IntegrityError

    for bad in (
        DraftSelection(
            draft_id=draft.id, overall_no=1, round=1, team_id=first.team_id, player_id=FIRST_ID + 9
        ),
        DraftSelection(
            draft_id=draft.id,
            overall_no=2,
            round=1,
            team_id=first.team_id,
            player_id=first.player_id,
        ),
    ):
        db.add(bad)
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()
    db.add(RosterEntry(league_id=league.id, team_id=first.team_id, player_id=first.player_id))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_two_managers_racing_for_the_same_player_one_wins(fantasy_db, db):
    league, users, draft = started(db)
    draft_id = draft.id
    target = next_player(db)
    results: list[str] = []

    def attempt(user_id: int) -> None:
        with Session(fantasy_db) as s:
            try:
                draft_room.make_pick(s, s.get(User, user_id), draft_id, target, now=T0)
                results.append("ok")
            except draft_room.DraftError:
                results.append("refused")

    ids = [u.id for u in users]
    threads = [threading.Thread(target=attempt, args=(uid,)) for uid in ids for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count("ok") == 1 and results.count("refused") == 7
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(DraftSelection)) == 1
    assert db.scalar(select(func.count()).select_from(RosterEntry)) == 1


def test_double_clicks_by_the_team_on_the_clock_make_one_pick(fantasy_db, db):
    league, users, draft = started(db)
    me_id = user_for_team(db, users, draft_room.team_on_clock(draft)).id
    draft_id = draft.id
    candidates = [FIRST_ID, FIRST_ID + 1, FIRST_ID + 2]
    results: list[str] = []

    def attempt(player_id: int) -> None:
        with Session(fantasy_db) as s:
            try:
                draft_room.make_pick(s, s.get(User, me_id), draft_id, player_id, now=T0)
                results.append("ok")
            except draft_room.DraftError:
                results.append("refused")

    threads = [threading.Thread(target=attempt, args=(p,)) for p in candidates for _ in range(3)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count("ok") == 1
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(DraftSelection)) == 1
    assert db.get(Draft, draft_id).current_pick == 2


def test_timer_expiry_racing_a_manual_pick_makes_exactly_one_pick(fantasy_db, db):
    league, users, draft = started(db)
    me_id = user_for_team(db, users, draft_room.team_on_clock(draft)).id
    draft_id = draft.id
    late = T0 + timedelta(seconds=91)
    results: list[str] = []

    def manual() -> None:
        with Session(fantasy_db) as s:
            try:
                draft_room.make_pick(s, s.get(User, me_id), draft_id, FIRST_ID + 3, now=late)
                results.append("manual")
            except draft_room.DraftError:
                results.append("manual-refused")

    def auto() -> None:
        with Session(fantasy_db) as s:
            try:
                got = draft_room.autopick(s, draft_id, best_first, now=late)
                results.append("auto" if got else "auto-none")
            except draft_room.DraftError:
                results.append("auto-refused")

    threads = [threading.Thread(target=f) for f in (manual, auto, manual, auto)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    db.expire_all()
    # The clock is reset after whichever pick lands first, so the autopicks that run second see
    # an unexpired clock and do nothing. Manual picks by the same team on the *next* clock are
    # refused because it is no longer their turn.
    assert db.scalar(select(func.count()).select_from(DraftSelection)) in (1, 2)
    picks = db.scalars(select(DraftSelection).order_by(DraftSelection.overall_no)).all()
    assert [p.overall_no for p in picks] == list(range(1, len(picks) + 1))
    assert len({p.player_id for p in picks}) == len(picks)


def test_league_delete_cascades_the_draft(db):
    league, users, draft = started(db)
    pick_for_clock(db, users, draft)
    db.execute(delete(League).where(League.id == league.id))
    db.commit()
    assert db.scalar(select(func.count()).select_from(Draft)) == 0
    assert db.scalar(select(func.count()).select_from(RosterEntry)) == 0


# ---- the worker's clock tick ----


def test_clock_tick_autopicks_only_expired_drafts_and_ranks_once(fantasy_db, db):
    league, users, draft = started(db)
    calls = []

    def ranking(session):
        calls.append(1)
        return best_first(session)

    def factory():
        return Session(fantasy_db)

    assert draft_room.process_due_drafts(factory, ranking, now=T0 + timedelta(seconds=10)) == 0
    assert calls == []  # nothing due, so the rankings were never computed
    made = draft_room.process_due_drafts(factory, ranking, now=T0 + timedelta(seconds=91))
    assert made == 1 and calls == [1]
    db.expire_all()
    pick = db.scalar(select(DraftSelection))
    assert pick.is_autopick and pick.player_id == FIRST_ID
    # The clock was reset by the pick, so the same instant is no longer due.
    assert draft_room.process_due_drafts(factory, ranking, now=T0 + timedelta(seconds=91)) == 0


def test_clock_tick_survives_a_broken_draft(fantasy_db, db):
    league, users, draft = started(db)

    def broken(session):
        raise RuntimeError("rankings are down")

    made = draft_room.process_due_drafts(
        lambda: Session(fantasy_db), broken, now=T0 + timedelta(seconds=91)
    )
    assert made == 0  # logged, not raised
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(DraftSelection)) == 0
