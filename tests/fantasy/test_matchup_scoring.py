from datetime import UTC, date, datetime, timedelta
from fractions import Fraction

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from undertow.data.models import (
    Draft,
    FantasyTeam,
    FantasyWeek,
    Game,
    GoalieGameStats,
    LineupSlot,
    Matchup,
    Player,
    ProTeam,
    RosterEntry,
    SkaterGameStats,
)
from undertow.fantasy import auth, leagues, lineups
from undertow.fantasy import matchup_scoring as ms

pytestmark = pytest.mark.db

BASE = 970_000
HOME, AWAY, IDLE = 9_501, 9_502, 9_503
# Week 1: Sat Dec 5 to Sun Dec 13 (Eastern). A Tuesday in it:
TUE = datetime(2026, 12, 8, 0, 0, tzinfo=UTC)
NOW = datetime(2026, 12, 20, 12, 0, tzinfo=UTC)  # after the week
WEEK = (date(2026, 12, 5), date(2026, 12, 13))


@pytest.fixture
def db(fantasy_db):
    with fantasy_db.begin() as conn:
        conn.execute(text("DELETE FROM player WHERE id >= :n"), {"n": BASE})
        conn.execute(text("DELETE FROM pro_team WHERE id = ANY(:t)"), {"t": [HOME, AWAY, IDLE]})
    with Session(fantasy_db) as s:
        s.add_all(
            [
                ProTeam(id=t, name=f"T{t}", code=c)
                for t, c in ((HOME, "HOM"), (AWAY, "AWY"), (IDLE, "IDL"))
            ]
        )
        s.add_all(
            [
                FantasyWeek(
                    season_id=11,
                    week_no=1,
                    start_date=date(2026, 12, 5),
                    end_date=date(2026, 12, 13),
                    games=10,
                ),
                FantasyWeek(
                    season_id=11,
                    week_no=2,
                    start_date=date(2026, 12, 14),
                    end_date=date(2026, 12, 20),
                    games=10,
                ),
            ]
        )
        s.commit()
        yield s
    with fantasy_db.begin() as conn:
        conn.execute(
            text("TRUNCATE draft, roster_entry, lineup_slot, lineup_lock, matchup, game CASCADE")
        )
        conn.execute(text("DELETE FROM player WHERE id >= :n"), {"n": BASE})
        conn.execute(text("DELETE FROM pro_team WHERE id = ANY(:t)"), {"t": [HOME, AWAY, IDLE]})


def league_of_four(db):
    users = [
        auth.register_user(
            db, email=f"u{i}@example.com", display_name=f"u{i}", password="a long enough password"
        ).user
        for i in range(4)
    ]
    league, _ = leagues.register_league(
        db, users[0], name="L", season_id=11, size=4, team_name="T0"
    )
    for i in range(1, 4):
        leagues.join_league(db, users[i], league.invite_code, f"T{i}")
    league.status = "in_season"
    db.add(
        Draft(
            league_id=league.id,
            status="complete",
            pick_seconds=60,
            rounds=11,
            order=[],
            order_seed=1,
            current_pick=45,
            completed_at=datetime(2026, 12, 1, tzinfo=UTC),
        )
    )
    db.commit()
    teams = [db.scalar(select(FantasyTeam.id).where(FantasyTeam.owner_id == u.id)) for u in users]
    return league, teams


_n = [0]


def player(db, league, team_id, pos, *, pro=HOME, slot=None):
    _n[0] += 1
    p = Player(
        id=BASE + _n[0],
        first_name="Pat",
        last_name=f"{pos}{_n[0]}",
        position=pos,
        pro_team_id=pro,
        status="active",
    )
    db.add(p)
    db.flush()
    db.add(RosterEntry(league_id=league.id, team_id=team_id, player_id=p.id))
    db.flush()
    lineups.place_new_player(db, team_id, p)
    if slot:
        db.scalar(select(LineupSlot).where(LineupSlot.player_id == p.id)).slot = slot
    db.flush()
    return p


def game(db, gid, start, *, status="final", result="REG", home=HOME, away=AWAY, season=11):
    db.add(
        Game(
            id=gid,
            season_id=season,
            home_team_id=home,
            away_team_id=away,
            start_utc=start,
            status=status,
            result_type=result if status == "final" else None,
        )
    )
    db.flush()
    return gid


def stat(db, gid, p, **kw):
    kw.setdefault("toi_seconds", 1200)
    db.add(SkaterGameStats(game_id=gid, player_id=p.id, team_id=p.pro_team_id, **kw))


def goalie(db, gid, p, sa=30, ga=2, decision="W"):
    db.add(
        GoalieGameStats(
            game_id=gid,
            player_id=p.id,
            team_id=p.pro_team_id,
            toi_seconds=3600,
            shots_against=sa,
            goals_against=ga,
            saves=sa - ga,
            decision=decision,
            shutout=ga == 0,
        )
    )


def lock(db, team_id, p, gid, slot):
    from undertow.data.models import LineupLock

    db.add(LineupLock(team_id=team_id, player_id=p.id, game_id=gid, slot=slot))


def week(db, team_id, now=NOW):
    return ms.team_week(db, team_id, *WEEK, now=now)


def by_name(tw, p):
    return next(x for x in tw.players if x.player_id == p.id)


# ---- what counts ----


def test_only_games_locked_in_a_starting_slot_count_and_points_are_exact(db):
    league, teams = league_of_four(db)
    star = player(db, league, teams[0], "F")
    bench = player(db, league, teams[0], "F", slot="BN")
    g = game(db, 1, TUE)
    stat(db, g, star, goals=2, assists=1, shots=5)  # 4 + 1 + 0.5 = 5.5
    stat(db, g, bench, goals=3)  # 6 points, but benched at puck drop
    lock(db, teams[0], star, g, "F")
    lock(db, teams[0], bench, g, "BN")
    db.commit()
    tw = week(db, teams[0])
    assert by_name(tw, star).points == Fraction(11, 2)
    assert by_name(tw, bench).points == 0  # scored 6, counted 0
    assert by_name(tw, bench).games[0].points == 6 and by_name(tw, bench).games[0].counted is False
    assert tw.total == Fraction(11, 2)


def test_the_slot_frozen_at_puck_drop_decides_not_the_slot_now(db):
    league, teams = league_of_four(db)
    p = player(db, league, teams[0], "F")
    early, late = game(db, 1, TUE), game(db, 2, TUE + timedelta(days=2))
    stat(db, early, p, goals=1)  # 2 points
    stat(db, late, p, goals=1, assists=1)  # 3 points
    lock(db, teams[0], p, early, "F")  # started that day...
    lock(db, teams[0], p, late, "BN")  # ...benched before the second game
    db.scalar(
        select(LineupSlot).where(LineupSlot.player_id == p.id)
    ).slot = "F"  # and starting again now
    db.commit()
    got = by_name(week(db, teams[0]), p)
    assert got.points == 2 and [g.counted for g in got.games] == [True, False]
    assert [g.slot for g in got.games] == ["F", "BN"]


def test_a_game_with_no_lock_does_not_count_even_if_the_player_scored(db):
    league, teams = league_of_four(db)
    p = player(db, league, teams[0], "F")
    g = game(db, 1, TUE)
    stat(db, g, p, goals=4)
    db.commit()  # the team was drafted after this game: no lock exists
    got = by_name(week(db, teams[0]), p)
    assert got.points == 0 and got.games[0].counted is False and got.games[0].slot is None
    assert got.games[0].points == 8  # shown, not counted


def test_goalie_points_include_win_type_exactly(db):
    league, teams = league_of_four(db)
    g1 = player(db, league, teams[0], "G")
    reg, so = game(db, 1, TUE, result="REG"), game(db, 2, TUE + timedelta(days=1), result="SO")
    goalie(db, reg, g1, 30, 2, "W")  # 4 + 5.6 - 4 = 5.6
    goalie(db, so, g1, 20, 0, "W")  # 8/3 + 4 + 3
    lock(db, teams[0], g1, reg, "G")
    lock(db, teams[0], g1, so, "G")
    db.commit()
    got = by_name(week(db, teams[0]), g1)
    assert got.points == Fraction(28, 5) + Fraction(8, 3) + 4 + 3
    assert got.games[1].breakdown["ot_win"] == Fraction(8, 3)
    assert got.games[0].stats["Dec"] == "W" and got.games[0].stats["SV"] == "28"


def test_a_live_game_counts_what_has_happened_so_far(db):
    league, teams = league_of_four(db)
    p = player(db, league, teams[0], "F")
    g = game(db, 1, NOW - timedelta(minutes=30), status="live")
    stat(db, g, p, goals=1, shots=2)  # 2 + 0.2
    lock(db, teams[0], p, g, "F")
    db.commit()
    got = by_name(ms.team_week(db, teams[0], date(2026, 12, 14), date(2026, 12, 20), now=NOW), p)
    assert got.points == Fraction(11, 5) and got.games[0].status == "live"


def test_players_with_no_stat_row_or_who_did_not_play_score_zero(db):
    league, teams = league_of_four(db)
    a, b = player(db, league, teams[0], "F"), player(db, league, teams[0], "F")
    g = game(db, 1, TUE)
    stat(db, g, b, toi_seconds=0, goals=9)  # dressed, did not play
    lock(db, teams[0], a, g, "F")
    lock(db, teams[0], b, g, "F")
    db.commit()
    tw = week(db, teams[0])
    assert by_name(tw, a).points == 0 and by_name(tw, b).points == 0 and tw.total == 0


def test_a_dropped_player_keeps_the_points_they_scored_for_the_team(db):
    league, teams = league_of_four(db)
    gone = player(db, league, teams[0], "F")
    g = game(db, 1, TUE)
    stat(db, g, gone, goals=1)
    lock(db, teams[0], gone, g, "F")
    db.execute(text("DELETE FROM lineup_slot"))
    db.commit()
    tw = week(db, teams[0])
    row = by_name(tw, gone)
    assert row.slot is None and row.points == 2 and tw.total == 2


def test_teams_are_scored_separately(db):
    league, teams = league_of_four(db)
    a, b = player(db, league, teams[0], "F"), player(db, league, teams[1], "F")
    g = game(db, 1, TUE)
    stat(db, g, a, goals=1)
    stat(db, g, b, assists=1)
    lock(db, teams[0], a, g, "F")
    lock(db, teams[1], b, g, "F")
    db.commit()
    assert week(db, teams[0]).total == 2 and week(db, teams[1]).total == 1


# ---- the week window and upcoming games ----


def test_week_edges_follow_eastern_midnight(db):
    league, teams = league_of_four(db)
    p = player(db, league, teams[0], "F")
    sunday_night = game(
        db, 1, datetime(2026, 12, 14, 3, 0, tzinfo=UTC)
    )  # 10 pm Eastern, Sun Dec 13
    monday_early = game(db, 2, datetime(2026, 12, 14, 6, 0, tzinfo=UTC))  # 1 am Eastern, Mon Dec 14
    for gid in (sunday_night, monday_early):
        stat(db, gid, p, goals=1)
        lock(db, teams[0], p, gid, "F")
    db.commit()
    one = by_name(week(db, teams[0]), p)
    assert [g.game_id for g in one.games] == [sunday_night] and one.points == 2
    two = by_name(ms.team_week(db, teams[0], date(2026, 12, 14), date(2026, 12, 20), now=NOW), p)
    assert [g.game_id for g in two.games] == [monday_early]


def test_upcoming_games_are_listed_with_the_current_slot_but_score_nothing(db):
    league, teams = league_of_four(db)
    p = player(db, league, teams[0], "F")
    idle = player(db, league, teams[0], "F", pro=IDLE)
    played = game(db, 1, TUE - timedelta(days=1))
    stat(db, played, p, goals=1)
    lock(db, teams[0], p, played, "F")
    soon = game(db, 2, TUE + timedelta(days=2), status="scheduled")
    db.commit()
    tw = ms.team_week(db, teams[0], *WEEK, now=TUE)
    row = by_name(tw, p)
    assert [(g.game_id, g.status, g.counted, g.slot) for g in row.games] == [
        (1, "final", True, "F"),
        (2, "scheduled", False, "F"),
    ]
    assert row.games_total == 2 and row.games_left(TUE) == 1 and row.next_game(TUE).game_id == soon
    assert (
        row.pro_team == "HOM"
        and by_name(tw, idle).games == []
        and by_name(tw, idle).pro_team == "IDL"
    )
    assert row.points == 2


# ---- week and matchup status ----


def test_week_status_moves_from_scheduled_to_live_to_final(db):
    a, b = (
        game(db, 1, TUE, status="scheduled"),
        game(db, 2, TUE + timedelta(days=2), status="scheduled"),
    )
    db.commit()
    assert ms.week_status(db, 11, *WEEK, now=TUE - timedelta(hours=1)) == "scheduled"
    assert ms.week_status(db, 11, *WEEK, now=TUE + timedelta(hours=1)) == "live"
    db.get(Game, a).status = "final"
    db.commit()
    assert ms.week_status(db, 11, *WEEK, now=TUE + timedelta(days=1)) == "live"
    db.get(Game, b).status = "final"
    db.commit()
    assert ms.week_status(db, 11, *WEEK, now=TUE + timedelta(days=3)) == "final"
    assert (
        ms.week_status(db, 11, date(2027, 1, 1), date(2027, 1, 7), now=NOW) == "final"
    )  # empty week


def test_a_matchup_totals_both_teams_and_names_the_leader(db):
    league, teams = league_of_four(db)
    a, b = player(db, league, teams[0], "F"), player(db, league, teams[1], "F")
    g = game(db, 1, TUE)
    stat(db, g, a, goals=2)
    stat(db, g, b, goals=1)
    lock(db, teams[0], a, g, "F")
    lock(db, teams[1], b, g, "F")
    m = Matchup(
        league_id=league.id, week_no=1, last_week_no=1, home_team_id=teams[1], away_team_id=teams[0]
    )
    db.add(m)
    db.commit()
    scored = ms.score_matchup(db, m, now=NOW)
    assert (scored.home.total, scored.away.total) == (2, 4) and scored.leader == teams[0]
    assert scored.status == "final"
    row = db.scalar(select(SkaterGameStats).where(SkaterGameStats.player_id == b.id))
    row.goals = 2
    db.commit()
    assert ms.score_matchup(db, m, now=NOW).leader is None  # level on exact totals


def test_a_two_week_playoff_round_sums_both_weeks(db):
    league, teams = league_of_four(db)
    a = player(db, league, teams[0], "F")
    w1, w2 = game(db, 1, TUE), game(db, 2, datetime(2026, 12, 16, 0, 0, tzinfo=UTC))
    for gid in (w1, w2):
        stat(db, gid, a, goals=1)
        lock(db, teams[0], a, gid, "F")
    m = Matchup(
        league_id=league.id, week_no=1, last_week_no=2, home_team_id=teams[0], away_team_id=teams[1]
    )
    db.add(m)
    db.commit()
    scored = ms.score_matchup(db, m, now=NOW)
    assert scored.home.total == 4 and (scored.home.start, scored.home.end) == (
        date(2026, 12, 5),
        date(2026, 12, 20),
    )


def test_a_matchup_whose_weeks_are_not_in_the_calendar_cannot_be_scored(db):
    league, teams = league_of_four(db)
    m = Matchup(
        league_id=league.id, week_no=9, last_week_no=9, home_team_id=teams[0], away_team_id=teams[1]
    )
    db.add(m)
    db.commit()
    assert ms.score_matchup(db, m, now=NOW) is None
