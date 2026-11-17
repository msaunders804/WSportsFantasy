from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from undertow.data.models import (
    AuditLog,
    Draft,
    FantasyTeam,
    Game,
    LineupLock,
    LineupSlot,
    Player,
    ProTeam,
    RosterEntry,
    Season,
)
from undertow.fantasy import auth, leagues, lineups

pytestmark = pytest.mark.db

BASE = 950_000
TEAMS = [9_401, 9_402, 9_403]  # real teams: 0 and 1 play each other; 2 is idle
NOW = datetime(2026, 12, 5, 23, 0, tzinfo=UTC)  # 6 pm Eastern on Dec 5: fantasy day Dec 5


@pytest.fixture
def db(fantasy_db):
    with fantasy_db.begin() as conn:
        conn.execute(text("DELETE FROM player WHERE id >= :n"), {"n": BASE})
        conn.execute(text("DELETE FROM pro_team WHERE id = ANY(:t)"), {"t": TEAMS})
    with Session(fantasy_db) as s:
        s.add_all([ProTeam(id=t, name=f"T{t}", code=f"T{i}") for i, t in enumerate(TEAMS)])
        s.add(Season(id=9, name="2026-27 Pre-Season", is_playoff=False, is_current=False))
        s.commit()
        yield s
    with fantasy_db.begin() as conn:
        conn.execute(
            text("TRUNCATE draft, roster_entry, lineup_slot, lineup_lock, matchup, game CASCADE")
        )
        conn.execute(text("DELETE FROM player WHERE id >= :n"), {"n": BASE})
        conn.execute(text("DELETE FROM pro_team WHERE id = ANY(:t)"), {"t": TEAMS})


def user(db, email):
    return auth.register_user(
        db, email=email, display_name=email.split("@")[0], password="a long enough password"
    ).user


def league_of_four(db):
    """An in-season private league. Returns (league, users, team ids); users[0] is commissioner."""
    boss = user(db, "u0@example.com")
    league, _ = leagues.register_league(
        db, boss, name="Friends", season_id=11, size=4, team_name="Team 0"
    )
    users = [boss]
    for i in range(1, 4):
        u = user(db, f"u{i}@example.com")
        leagues.join_league(db, u, league.invite_code, f"Team {i}")
        users.append(u)
    league.status = "in_season"
    db.commit()
    teams = [db.scalar(select(FantasyTeam.id).where(FantasyTeam.owner_id == u.id)) for u in users]
    return league, users, teams


_next = [0]


def rostered(db, league, team_id, position, *, pro=0, status="active", place=True):
    """A player on the team's roster, placed in the lineup the way a draft pick is."""
    _next[0] += 1
    player = Player(
        id=BASE + _next[0],
        first_name="First",
        last_name=f"{position}{_next[0]}",
        position=position,
        pro_team_id=TEAMS[pro],
        status=status,
    )
    db.add(player)
    db.flush()
    db.add(RosterEntry(league_id=league.id, team_id=team_id, player_id=player.id))
    db.flush()
    if place:
        lineups.place_new_player(db, team_id, player)
    return player


def slots(db, team_id):
    return {p.id: s.slot for s, p in lineups.team_lineup(db, team_id)}


def game(db, gid, start, season=11, home=0, away=1, status="scheduled"):
    db.add(
        Game(
            id=gid,
            season_id=season,
            home_team_id=TEAMS[home],
            away_team_id=TEAMS[away],
            start_utc=start,
            status=status,
        )
    )
    db.flush()


# ---- pure rules ----


@pytest.mark.parametrize(
    ("position", "slot", "status", "ok"),
    [
        ("F", "F", "active", True),
        ("F", "D", "active", False),
        ("D", "D", "active", True),
        ("F", "UTIL", "active", True),
        ("D", "UTIL", "active", True),
        ("G", "UTIL", "active", False),
        ("G", "G", "active", True),
        ("G", "F", "active", False),
        ("G", "BN", "active", True),
        ("F", "IR", "active", False),
        ("F", "IR", "injured", True),
        ("G", "IR", "ltir", True),
        ("D", "IR", "inactive", False),
        (None, "BN", "active", True),
        (None, "F", "active", False),
    ],
)
def test_eligibility(position, slot, status, ok):
    assert lineups.eligible(position, slot, status) is ok


def test_the_fantasy_day_rolls_over_at_4am_eastern():
    # 1:30 am Eastern on Dec 6 is still the Dec 5 fantasy day; 4:00 am starts Dec 6.
    assert lineups.fantasy_day(datetime(2026, 12, 6, 6, 30, tzinfo=UTC)).isoformat() == "2026-12-05"
    assert lineups.fantasy_day(datetime(2026, 12, 6, 9, 0, tzinfo=UTC)).isoformat() == "2026-12-06"
    assert lineups.day_start(NOW) == datetime(2026, 12, 5, 9, 0, tzinfo=UTC)


# ---- placing players ----


def test_new_players_fill_their_own_slots_then_flex_then_bench(db):
    league, users, teams = league_of_four(db)
    t = teams[0]
    placed = [rostered(db, league, t, pos) for pos in "FFFFFDDDGG"]
    got = [slots(db, t)[p.id] for p in placed]
    # F F F -> F; 4th F -> UTIL; 5th F -> BN; D D -> D; 3rd D -> BN (UTIL taken); G -> G; G2 -> BN
    assert got == ["F", "F", "F", "UTIL", "BN", "D", "D", "BN", "G", "BN"]


def test_a_full_roster_has_no_room_for_another_player(db):
    league, users, teams = league_of_four(db)
    for pos in "FFFFDDGFFFF":  # 11 players fill 7 starters + 4 bench
        rostered(db, league, teams[0], pos)
    assert lineups.count_by_slot(db, teams[0]) == {"F": 3, "UTIL": 1, "D": 2, "G": 1, "BN": 4}
    with pytest.raises(lineups.LineupError, match="no open spot"):
        rostered(db, league, teams[0], "F")


# ---- moving players ----


def test_move_to_an_open_slot_and_swap_when_full(db):
    league, users, teams = league_of_four(db)
    owner, t = users[1], teams[1]
    f = [rostered(db, league, t, "F") for _ in range(5)]  # F F F UTIL BN
    assert slots(db, t)[f[4].id] == "BN"
    with pytest.raises(lineups.LineupError, match="full. Choose a player to swap"):
        lineups.move_player(db, owner, t, f[4].id, "F", now=NOW)
    with pytest.raises(lineups.LineupError, match="only swap with a player in that slot"):
        lineups.move_player(db, owner, t, f[4].id, "F", swap_with=f[3].id, now=NOW)  # f[3] is UTIL
    lineups.move_player(db, owner, t, f[4].id, "F", swap_with=f[0].id, now=NOW)
    got = slots(db, t)
    assert got[f[4].id] == "F" and got[f[0].id] == "BN"
    # Moving a starter to the open bench spot needs no swap.
    lineups.move_player(db, owner, t, f[1].id, "BN", now=NOW)
    assert slots(db, t)[f[1].id] == "BN"
    # Moving to the slot you are already in changes nothing and is not an error.
    lineups.move_player(db, owner, t, f[1].id, "BN", now=NOW)


def test_position_and_injury_rules_are_enforced(db):
    league, users, teams = league_of_four(db)
    owner, t = users[1], teams[1]
    g, d, f = (
        rostered(db, league, t, "G"),
        rostered(db, league, t, "D"),
        rostered(db, league, t, "F"),
    )
    with pytest.raises(lineups.LineupError, match="cannot play forward"):
        lineups.move_player(db, owner, t, g.id, "F", now=NOW)
    with pytest.raises(lineups.LineupError, match="cannot play flex"):
        lineups.move_player(db, owner, t, g.id, "UTIL", now=NOW)
    lineups.move_player(db, owner, t, d.id, "UTIL", now=NOW)  # a defender may play the flex spot
    with pytest.raises(lineups.LineupError, match="not injured"):
        lineups.move_player(db, owner, t, f.id, "IR", now=NOW)
    hurt = db.get(Player, f.id)
    hurt.status = "injured"
    db.commit()
    lineups.move_player(db, owner, t, f.id, "IR", now=NOW)
    assert slots(db, t)[f.id] == "IR"
    # A second injured player cannot exceed the IR limit once it is full.
    league_settings = db.get(leagues.LeagueSettings, league.id)
    league_settings.roster_ir = 1
    other = rostered(db, league, t, "F", status="injured")
    db.commit()
    with pytest.raises(lineups.LineupError, match="injured reserve spots are full"):
        lineups.move_player(db, owner, t, other.id, "IR", now=NOW)


def test_only_the_owner_the_commissioner_and_staff_may_edit_and_edits_by_others_are_audited(db):
    league, users, teams = league_of_four(db)
    t = teams[1]  # owned by users[1]
    p = rostered(db, league, t, "F")
    with pytest.raises(leagues.PermissionDenied):
        lineups.move_player(db, users[2], t, p.id, "BN", now=NOW)  # another manager
    lineups.move_player(db, users[1], t, p.id, "BN", now=NOW)  # the owner
    assert (
        db.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.action == "lineup.edit")
        )
        == 0
    )
    lineups.move_player(db, users[0], t, p.id, "F", now=NOW)  # the commissioner
    entry = db.scalar(select(AuditLog).where(AuditLog.action == "lineup.edit"))
    assert entry.actor_id == users[0].id and entry.after["moves"] == {str(p.id): "F"}
    staff = user(db, "staff@example.com")
    staff.is_admin = True
    db.commit()
    lineups.move_player(db, staff, t, p.id, "BN", now=NOW)


def test_lineups_are_closed_before_the_draft_and_for_players_not_on_the_roster(db):
    league, users, teams = league_of_four(db)
    p = rostered(db, league, teams[1], "F")
    stranger = rostered(db, league, teams[2], "F")
    with pytest.raises(lineups.LineupError, match="not on this roster"):
        lineups.move_player(db, users[1], teams[1], stranger.id, "BN", now=NOW)
    with pytest.raises(lineups.LineupError, match="Unknown lineup slot"):
        lineups.move_player(db, users[1], teams[1], p.id, "XX", now=NOW)
    league.status = "setup"
    db.commit()
    with pytest.raises(lineups.LineupError, match="open once the draft starts"):
        lineups.move_player(db, users[1], teams[1], p.id, "BN", now=NOW)


# ---- the game-start lock on edits ----


def test_a_player_whose_game_has_started_today_cannot_be_moved(db):
    league, users, teams = league_of_four(db)
    owner, t = users[1], teams[1]
    playing = rostered(db, league, t, "F", pro=0)
    idle = rostered(db, league, t, "F", pro=2)  # their team has no game
    game(db, 1, NOW - timedelta(minutes=5))  # puck dropped five minutes ago
    db.commit()
    with pytest.raises(lineups.LineupError, match="game has started"):
        lineups.move_player(db, owner, t, playing.id, "BN", now=NOW)
    lineups.move_player(db, owner, t, idle.id, "BN", now=NOW)  # others are free
    # A swap with a locked player is refused too, even if the moved player is free.
    with pytest.raises(lineups.LineupError, match="game has started"):
        lineups.move_player(db, owner, t, idle.id, "F", swap_with=playing.id, now=NOW)


def test_games_later_today_games_on_other_days_and_other_seasons_do_not_lock(db):
    league, users, teams = league_of_four(db)
    owner, t = users[1], teams[1]
    p = rostered(db, league, t, "F", pro=0)
    game(db, 1, NOW + timedelta(hours=1))  # tonight, not started yet
    game(db, 2, NOW - timedelta(days=1))  # yesterday
    game(db, 3, NOW - timedelta(minutes=5), season=9)  # a preseason game today
    db.commit()
    lineups.move_player(db, owner, t, p.id, "BN", now=NOW)
    # Once tonight's game starts, the same move is refused.
    with pytest.raises(lineups.LineupError, match="game has started"):
        lineups.move_player(db, owner, t, p.id, "F", now=NOW + timedelta(hours=2))


def test_a_late_game_stays_locked_until_4am_eastern_then_frees_up(db):
    league, users, teams = league_of_four(db)
    p = rostered(db, league, teams[1], "F", pro=0)
    game(db, 1, datetime(2026, 12, 6, 2, 0, tzinfo=UTC))  # 9 pm Eastern, Dec 5
    db.commit()
    midnight = datetime(2026, 12, 6, 5, 0, tzinfo=UTC)  # 12 am Eastern: still the Dec 5 day
    with pytest.raises(lineups.LineupError, match="game has started"):
        lineups.move_player(db, users[1], teams[1], p.id, "BN", now=midnight)
    five_am = datetime(2026, 12, 6, 10, 0, tzinfo=UTC)  # 5 am Eastern: a new fantasy day
    lineups.move_player(db, users[1], teams[1], p.id, "BN", now=five_am)


# ---- locking the lineup when games start ----


def complete_draft(db, league, when):
    db.add(
        Draft(
            league_id=league.id,
            status="complete",
            pick_seconds=60,
            rounds=11,
            order=[],
            order_seed=1,
            current_pick=45,
            completed_at=when,
        )
    )
    db.commit()


def lock_rows(db):
    return {(r.team_id, r.player_id, r.game_id): r.slot for r in db.scalars(select(LineupLock))}


def test_starting_a_game_freezes_each_rostered_players_slot(db):
    league, users, teams = league_of_four(db)
    a = rostered(db, league, teams[0], "F", pro=0)  # starts
    b = rostered(db, league, teams[0], "G", pro=1)
    c = rostered(db, league, teams[1], "D", pro=0)
    bench = rostered(db, league, teams[1], "F", pro=0)
    lineups.move_player(db, users[1], teams[1], bench.id, "BN", now=NOW - timedelta(hours=5))
    idle = rostered(db, league, teams[2], "F", pro=2)  # their team does not play
    complete_draft(db, league, NOW - timedelta(days=1))
    game(db, 1, NOW - timedelta(minutes=5))
    db.commit()
    assert lineups.lock_started_games(db, NOW) == 4
    assert lock_rows(db) == {
        (teams[0], a.id, 1): "F",
        (teams[0], b.id, 1): "G",
        (teams[1], c.id, 1): "D",
        (teams[1], bench.id, 1): "BN",
    }
    assert idle.id not in {k[1] for k in lock_rows(db)}
    # Running it again changes nothing, and moving a player afterwards never rewrites the snapshot.
    assert lineups.lock_started_games(db, NOW + timedelta(seconds=30)) == 0
    row = db.scalar(select(LineupSlot).where(LineupSlot.player_id == a.id))
    row.slot = "BN"
    db.commit()
    lineups.lock_started_games(db, NOW + timedelta(minutes=1))
    assert lock_rows(db)[(teams[0], a.id, 1)] == "F"


def test_only_started_games_in_leagues_with_a_finished_draft_are_locked(db):
    league, users, teams = league_of_four(db)
    rostered(db, league, teams[0], "F", pro=0)
    complete_draft(db, league, NOW - timedelta(hours=2))
    game(db, 1, NOW + timedelta(hours=1))  # not started
    game(db, 2, NOW - timedelta(hours=3))  # started before the draft finished
    game(db, 3, NOW - timedelta(minutes=5), season=9)  # a preseason game
    game(db, 4, NOW - timedelta(days=4))  # older than the catch-up window
    db.commit()
    assert lineups.lock_started_games(db, NOW) == 0
    # A game the feed already calls live counts even if its start time is still ahead.
    game(db, 5, NOW + timedelta(minutes=1), status="live")
    db.commit()
    assert lineups.lock_started_games(db, NOW) == 1
    # A league whose draft is not complete is never locked.
    league.status = "drafting"
    db.execute(text("DELETE FROM lineup_lock"))
    db.commit()
    assert lineups.lock_started_games(db, NOW) == 0


def test_a_game_that_did_not_actually_start_has_its_locks_released(db):
    league, users, teams = league_of_four(db)
    rostered(db, league, teams[0], "F", pro=0)
    complete_draft(db, league, NOW - timedelta(days=1))
    game(db, 1, NOW - timedelta(minutes=5))
    db.commit()
    assert lineups.lock_started_games(db, NOW) == 1
    # The schedule sync then reports the game as postponed to tomorrow.
    g = db.get(Game, 1)
    g.start_utc = NOW + timedelta(days=1)
    db.commit()
    assert lineups.release_unstarted_locks(db, NOW) == 1
    assert lock_rows(db) == {}
    # A game that really is live is never released.
    game(db, 2, NOW - timedelta(minutes=1), status="live")
    db.commit()
    lineups.lock_started_games(db, NOW)
    assert lineups.release_unstarted_locks(db, NOW) == 0 and len(lock_rows(db)) == 1


def test_repair_places_unplaced_players_and_drops_slots_of_released_ones(db):
    league, users, teams = league_of_four(db)
    new = rostered(db, league, teams[0], "G", place=False)
    gone = rostered(db, league, teams[0], "F")
    entry = db.scalar(select(RosterEntry).where(RosterEntry.player_id == gone.id))
    entry.released_at = NOW
    db.commit()
    assert lineups.repair_lineups(db) == 2
    got = slots(db, teams[0])
    assert got == {new.id: "G"}
    assert lineups.repair_lineups(db) == 0  # nothing left to fix


def test_the_worker_job_reports_what_it_did(db, fantasy_db):
    league, users, teams = league_of_four(db)
    rostered(db, league, teams[0], "F", pro=0)
    complete_draft(db, league, NOW - timedelta(days=1))
    game(db, 1, NOW - timedelta(minutes=5))
    db.commit()
    assert (
        lineups.run_lock_job(lambda: Session(fantasy_db), NOW) == "locked=1 released=0 repaired=0"
    )
    assert (
        lineups.run_lock_job(lambda: Session(fantasy_db), NOW) == "locked=0 released=0 repaired=0"
    )


# ---- the moves the page offers ----


def test_move_options_match_what_move_player_accepts(db):
    league, users, teams = league_of_four(db)
    t = teams[1]
    roster = [rostered(db, league, t, pos) for pos in "FFFFFDDDGG"]
    hurt = rostered(db, league, t, "F", status="injured")
    options = lineups.move_options(db, t, NOW)
    assert set(options) == {p.id for p in roster} | {hurt.id}
    first_f, bench_f, g1, g2 = roster[0], roster[4], roster[8], roster[9]
    # A bench forward can swap with any starting forward (all forwards fit every forward slot).
    assert {(o.slot, o.swap_with) for o in options[bench_f.id] if o.slot == "F"} == {
        ("F", roster[0].id),
        ("F", roster[1].id),
        ("F", roster[2].id),
    }
    # A goalie can only go to the bench or swap with the other goalie (slot G is full).
    assert {o.slot for o in options[g1.id]} == {"BN"} and {o.slot for o in options[g2.id]} == {"G"}
    assert options[g2.id][0].swap_with == g1.id and options[g2.id][0].swap_name
    # Healthy players are never offered IR; the injured one is, if it has room.
    assert all(o.slot != "IR" for p in roster for o in options[p.id])
    assert any(o.slot == "IR" and o.swap_with is None for o in options[hurt.id])
    assert first_f.id in options
    # Every offered option is accepted by move_player, and nothing else would be.
    snapshot = slots(db, t)
    for player_id, opts in options.items():
        for o in opts:
            lineups.move_player(db, users[1], t, player_id, o.slot, swap_with=o.swap_with, now=NOW)
            for row, p in lineups.team_lineup(db, t):
                row.slot = snapshot[p.id]
            db.commit()


def test_locked_players_are_not_offered_moves_and_not_offered_as_swap_partners(db):
    league, users, teams = league_of_four(db)
    t = teams[1]
    playing = rostered(db, league, t, "F", pro=0)
    free = rostered(db, league, t, "F", pro=2)
    rostered(db, league, t, "F", pro=2)  # three forwards: the F slots are now full
    benched = rostered(db, league, t, "F", pro=2)
    lineups.move_player(db, users[1], t, benched.id, "BN", now=NOW)
    game(db, 1, NOW - timedelta(minutes=5))  # team 0 is playing now
    db.commit()
    options = lineups.move_options(db, t, NOW)
    assert options[playing.id] == []  # locked: no moves at all
    for o in options[benched.id]:
        assert o.swap_with != playing.id  # and cannot be swapped with
    assert any(o.slot == "F" and o.swap_with == free.id for o in options[benched.id])
