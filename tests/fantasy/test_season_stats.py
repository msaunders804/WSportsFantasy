from datetime import UTC, datetime
from fractions import Fraction

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from undertow.data.models import (
    Game,
    GoalieGameStats,
    Player,
    ProTeam,
    Season,
    SkaterGameStats,
)
from undertow.fantasy import season_stats as ss

pytestmark = pytest.mark.db

BASE = 930_000
HOME, AWAY = 9_101, 9_102


@pytest.fixture
def db(fantasy_db):
    ss.clear_cache()
    with fantasy_db.begin() as conn:
        conn.execute(text("DELETE FROM player WHERE id >= :n"), {"n": BASE})
        conn.execute(text("DELETE FROM pro_team WHERE id IN (:a, :b)"), {"a": HOME, "b": AWAY})
    with Session(fantasy_db) as s:
        s.add_all(
            [ProTeam(id=HOME, name="Home", code="HOM"), ProTeam(id=AWAY, name="Away", code="AWY")]
        )
        s.add_all(
            [
                Season(id=8, name="2025-26 Preseason", is_playoff=False, is_current=False),
                Season(id=9, name="2026 Playoffs", is_playoff=True, is_current=False),
                Season(id=10, name="2025-26 Regular Season", is_playoff=False, is_current=False),
            ]
        )
        s.commit()
        yield s
    ss.clear_cache()
    with fantasy_db.begin() as conn:
        conn.execute(text("TRUNCATE skater_game_stats, goalie_game_stats, game CASCADE"))
        conn.execute(text("DELETE FROM player WHERE id >= :n"), {"n": BASE})
        conn.execute(text("DELETE FROM pro_team WHERE id IN (:a, :b)"), {"a": HOME, "b": AWAY})


def player(db, n, pos="F"):
    db.add(
        Player(
            id=BASE + n, first_name=f"First{n}", last_name=f"Last{n}", position=pos, status="active"
        )
    )
    db.flush()
    return BASE + n


def game(db, gid, season=10, status="final", result="REG", day=10):
    db.add(
        Game(
            id=gid,
            season_id=season,
            home_team_id=HOME,
            away_team_id=AWAY,
            start_utc=datetime(2026, 1, day, 20, tzinfo=UTC),
            status=status,
            result_type=result if status == "final" else None,
        )
    )
    db.flush()
    return gid


def skater(db, gid, pid, team=HOME, toi=1200, **kw):
    db.add(SkaterGameStats(game_id=gid, player_id=pid, team_id=team, toi_seconds=toi, **kw))


def goalie(db, gid, pid, sa=30, ga=2, decision="W", team=HOME, toi=3600):
    db.add(
        GoalieGameStats(
            game_id=gid,
            player_id=pid,
            team_id=team,
            toi_seconds=toi,
            shots_against=sa,
            goals_against=ga,
            saves=sa - ga,
            decision=decision,
            shutout=ga == 0,
        )
    )


def test_show_rounds_half_up_and_plain_drops_trailing_zeros():
    assert ss.show(Fraction(33, 10)) == "3.3"
    assert ss.show(Fraction(5, 2), 0) == "3"  # half up, not banker's rounding
    assert ss.show(Fraction(-7, 2), 0) == "-4"
    assert ss.show(Fraction(2, 3), 2) == "0.67"
    assert [ss.plain(Fraction(x)) for x in (2, Fraction(1, 2), Fraction(8, 3), Fraction(-2))] == [
        "2",
        "0.5",
        "2.67",
        "-2",
    ]


def test_only_regular_seasons_with_stats_are_offered(db):
    p = player(db, 1)
    assert ss.regular_seasons_with_stats(db) == []
    skater(db, game(db, 1, season=8), p)  # preseason
    skater(db, game(db, 2, season=9), p)  # playoffs
    db.commit()
    assert ss.regular_seasons_with_stats(db) == []
    skater(db, game(db, 3, season=10), p)
    db.commit()
    assert ss.regular_seasons_with_stats(db) == [(10, "2025-26 Regular Season")]


def test_skater_totals_and_fantasy_points(db):
    a, b = player(db, 1), player(db, 2, "D")
    skater(db, game(db, 1), a, goals=1, assists=1, shots=3, hits=2, blocks=1, pp_goals=1)
    skater(db, game(db, 2, day=11), a, goals=0, assists=2, shots=1)
    skater(db, game(db, 3, day=12), a, toi=0, goals=9)  # dressed but did not play
    skater(db, game(db, 4, day=13, status="live"), a, goals=9)  # not final
    skater(db, game(db, 5, season=9), a, goals=9)  # other season
    skater(db, game(db, 6, day=14), b, goals=0, assists=1)
    db.commit()
    data = ss.season_stats(db, 10)
    assert (
        data.season_name == "2025-26 Regular Season" and data.games == 4
    )  # final games 1, 2, 3, 6
    top, second = data.skaters
    assert top.player_id == a and top.gp == 2 and top.goals == 1 and top.assists == 3
    assert (
        top.points == 4
        and top.shots == 4
        and top.hits == 2
        and top.blocks == 1
        and top.pp_points == 1
    )
    # Game 1: goal 2 + assist 1 + PP bonus 0.5 + 3 shots 0.3 + 2 hits 0.2 + block 0.5 = 4.5
    # Game 2: 2 assists 2 + 1 shot 0.1 = 2.1
    assert top.fantasy == Fraction(66, 10) and top.fpg == Fraction(33, 10)
    assert sum(top.breakdown.values(), Fraction(0)) == top.fantasy
    assert second.player_id == b and second.pos == "D" and second.fantasy == 1


def test_goalie_totals_and_decisions(db):
    g = player(db, 1, "G")
    goalie(db, game(db, 1, result="REG"), g, 30, 2, "W")  # 4 + 5.6 - 4 = 5.6
    goalie(db, game(db, 2, day=11, result="SO"), g, 20, 0, "W")  # 8/3 + 4 + 3
    goalie(db, game(db, 3, day=12), g, 25, 3, "L")  # 4.4 - 6
    goalie(db, game(db, 4, day=13, result="OT"), g, 10, 1, "OTL")  # 1.8 - 2 + 1
    goalie(db, game(db, 5, day=14), g, 10, 1, "L", toi=0)  # did not play
    db.commit()
    (row,) = ss.season_stats(db, 10).goalies
    assert (row.gp, row.wins, row.losses, row.ot_losses, row.shutouts) == (4, 2, 1, 1, 1)
    assert row.shots_against == 85 and row.saves == 79 and row.goals_against == 6
    assert row.save_pct == "0.929"
    assert row.fantasy == Fraction(28, 5) + Fraction(8, 3) + 4 + 3 + Fraction(22, 5) - 6 + Fraction(
        4, 5
    )
    assert set(row.breakdown) >= {"win", "ot_win", "save", "goal_against", "shutout", "ot_loss"}


def test_a_player_traded_mid_season_shows_their_latest_team(db):
    p = player(db, 1)
    skater(db, game(db, 1, day=10), p, team=HOME, goals=1)
    skater(db, game(db, 2, day=20), p, team=AWAY, goals=1)
    db.commit()
    (row,) = ss.season_stats(db, 10).skaters
    assert row.team == "AWY" and row.gp == 2


def test_unknown_season_and_the_cache(db):
    assert ss.season_stats(db, 999) is None
    p = player(db, 1)
    skater(db, game(db, 1), p, goals=1)
    db.commit()
    first = ss.season_stats(db, 10)
    assert ss.season_stats(db, 10) is first  # served from the cache


def test_rank_of_and_summary_lookup(db):
    star, other, keeper = player(db, 1), player(db, 2, "D"), player(db, 3, "G")
    gid = game(db, 1)
    skater(db, gid, star, goals=3)
    skater(db, gid, other, goals=1)
    goalie(db, gid, keeper, 30, 0, "W")  # 4 + 6 + 3 = 13 > 6
    db.commit()
    data = ss.season_stats(db, 10)
    assert ss.rank_of(data, ss.player_summary(data, keeper)) == (1, 1)
    assert ss.rank_of(data, ss.player_summary(data, star)) == (2, 1)
    assert ss.rank_of(data, ss.player_summary(data, other)) == (3, 1)
    assert ss.player_summary(data, BASE + 99) is None


def test_game_log_lists_each_game_with_opponent_and_points(db):
    p, g = player(db, 1), player(db, 2, "G")
    skater(db, game(db, 1, day=10), p, team=HOME, goals=1, shots=2)
    skater(db, game(db, 2, day=11), p, team=AWAY, assists=1)
    goalie(db, 1, g, 30, 2, "W", team=HOME)
    db.commit()
    log = ss.game_log(db, p, 10)
    assert [(x.opponent, x.home) for x in log] == [("AWY", True), ("HOM", False)]
    assert (
        log[0].fantasy == Fraction(22, 10)
        and log[0].stats["G"] == "1"
        and log[0].stats["SOG"] == "2"
    )
    assert log[1].fantasy == 1 and log[1].day.day == 11
    (gl,) = ss.game_log(db, g, 10)
    assert gl.stats["Dec"] == "W" and gl.stats["SV"] == "28" and gl.fantasy == Fraction(28, 5)
    assert ss.game_log(db, p, 9) == []


def test_game_log_dates_are_eastern_not_utc(db):
    p = player(db, 1)
    # 7 pm Eastern on Jan 13 is 00:00 UTC on Jan 14. The log must say Jan 13.
    db.add(
        Game(
            id=77,
            season_id=10,
            home_team_id=HOME,
            away_team_id=AWAY,
            start_utc=datetime(2026, 1, 14, 0, 0, tzinfo=UTC),
            status="final",
            result_type="REG",
        )
    )
    db.flush()
    skater(db, 77, p, goals=1)
    g = player(db, 2, "G")
    goalie(db, 77, g, 30, 2, "W")
    db.commit()
    assert ss.game_log(db, p, 10)[0].day.isoformat() == "2026-01-13"
    assert ss.game_log(db, g, 10)[0].day.isoformat() == "2026-01-13"
