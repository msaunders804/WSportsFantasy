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
from undertow.fantasy import rankings

pytestmark = pytest.mark.db

BASE = 910_000  # player ids for this module; the fixture removes them afterwards
TEAM = 9_001
START = datetime(2026, 1, 10, 20, tzinfo=UTC)


@pytest.fixture
def db(fantasy_db):
    with fantasy_db.begin() as conn:
        conn.execute(text("DELETE FROM player WHERE id >= :n"), {"n": BASE})
        conn.execute(text("DELETE FROM pro_team WHERE id = :t"), {"t": TEAM})
    with Session(fantasy_db) as s:
        s.add(ProTeam(id=TEAM, name="Test Team", code="TST"))
        s.add_all(
            [
                Season(id=8, name="2025-26 Preseason", is_playoff=False, is_current=False),
                Season(id=9, name="2026 Playoffs", is_playoff=True, is_current=False),
                Season(id=10, name="2025-26 Regular Season", is_playoff=False, is_current=False),
                Season(id=12, name="2027-28 Regular Season", is_playoff=False, is_current=False),
            ]
        )
        s.commit()
        yield s
    with fantasy_db.begin() as conn:
        conn.execute(text("TRUNCATE skater_game_stats, goalie_game_stats, game CASCADE"))
        conn.execute(text("DELETE FROM player WHERE id >= :n"), {"n": BASE})
        conn.execute(text("DELETE FROM pro_team WHERE id = :t"), {"t": TEAM})


def player(db, n, pos="F", status="active"):
    db.add(
        Player(
            id=BASE + n,
            first_name=f"First{n}",
            last_name=f"Last{n}",
            position=pos,
            pro_team_id=TEAM,
            status=status,
        )
    )
    db.flush()
    return BASE + n


def game(db, gid, season=10, status="final", result="REG"):
    db.add(
        Game(
            id=gid,
            season_id=season,
            home_team_id=TEAM,
            away_team_id=TEAM,
            start_utc=START,
            status=status,
            result_type=result if status == "final" else None,
        )
    )
    db.flush()
    return gid


def skater(db, gid, pid, goals=0, assists=0, shots=0, toi=1200):
    db.add(
        SkaterGameStats(
            game_id=gid,
            player_id=pid,
            team_id=TEAM,
            goals=goals,
            assists=assists,
            shots=shots,
            toi_seconds=toi,
        )
    )


def goalie(db, gid, pid, shots_against=30, goals_against=2, decision="W", toi=3600):
    db.add(
        GoalieGameStats(
            game_id=gid,
            player_id=pid,
            team_id=TEAM,
            toi_seconds=toi,
            shots_against=shots_against,
            goals_against=goals_against,
            saves=shots_against - goals_against,
            decision=decision,
            shutout=goals_against == 0,
        )
    )


def test_history_is_the_latest_regular_season_before_the_one_being_drafted(db):
    assert rankings.history_season_ids(db) == []  # no stats anywhere yet
    p = player(db, 1)
    skater(db, game(db, 1, season=8), p)  # preseason: not flagged as a playoff, still ignored
    skater(db, game(db, 2, season=9), p)  # playoffs: ignored
    skater(db, game(db, 3, season=11), p)  # the season being drafted (current): ignored
    skater(db, game(db, 4, season=12), p)  # a later season: ignored
    db.commit()
    assert rankings.history_season_ids(db) == []
    skater(db, game(db, 5, season=10), p)
    db.commit()
    assert rankings.history_season_ids(db) == [10]
    assert rankings.history_season_ids(db, before_season_id=10) == []


def test_skater_points_use_the_standard_table_and_only_games_played(db):
    p = player(db, 1)
    skater(db, game(db, 1), p, goals=1, assists=1, shots=3)  # 2 + 1 + 0.3
    skater(db, game(db, 2), p, goals=0, assists=0, shots=0, toi=0)  # dressed, did not play
    skater(db, game(db, 3, status="live"), p, goals=5)  # not final
    skater(db, game(db, 4, season=11), p, goals=5)  # other season
    db.commit()
    total, games = rankings.points_by_player(db, [10])[p]
    assert total == Fraction(33, 10) and games == 1


def test_goalie_points_include_win_type_saves_and_goals_against(db):
    g = player(db, 1, "G")
    goalie(db, game(db, 1, result="REG"), g, 30, 2, "W")  # 4 + 28*0.2 - 4 = 5.6
    goalie(db, game(db, 2, result="SO"), g, 20, 0, "W")  # 4*2/3 + 20*0.2 + 3 = 9.666..
    goalie(db, game(db, 3), g, 10, 1, "L", toi=0)  # did not play
    db.commit()
    total, games = rankings.points_by_player(db, [10])[g]
    assert games == 2
    assert total == Fraction(28, 5) + Fraction(8, 3) + 4 + 3


def test_pool_has_active_positioned_players_and_zeros_for_no_history(db):
    vet, rookie = player(db, 1), player(db, 2)
    player(db, 3, status="injured")
    unmapped = player(db, 4)
    db.get(Player, unmapped).position = None
    skater(db, game(db, 1), vet, goals=2, shots=5)
    db.commit()
    pool = {p.id: p for p in rankings.draft_pool(db) if p.id >= BASE}
    assert set(pool) == {vet, rookie}
    assert pool[vet].gp == 1 and pool[vet].total == pytest.approx(4.5) and pool[vet].team == "TST"
    assert pool[rookie].gp == 0 and pool[rookie].fpg == 0.0


def test_better_players_rank_higher_and_the_order_is_stable(db):
    stars = [player(db, i) for i in range(1, 4)]
    for n in range(1, 9):
        gid = game(db, n)
        skater(db, gid, stars[0], goals=2, assists=1, shots=4)
        skater(db, gid, stars[1], goals=1, assists=0, shots=2)
        skater(db, gid, stars[2], goals=0, assists=0, shots=1)
    db.commit()
    ids = [r.player.id for r in rankings.rank_players(db) if r.player.id >= BASE]
    assert ids == stars
    again = [r.player.id for r in rankings.rank_players(db) if r.player.id >= BASE]
    assert again == ids
    assert [i for i in rankings.default_ranking(db) if i >= BASE] == ids


def test_ranking_with_no_history_is_empty_of_value_but_still_lists_everyone(db):
    ids = [player(db, i) for i in range(1, 4)]
    db.commit()
    ranked = [r.player.id for r in rankings.rank_players(db) if r.player.id >= BASE]
    assert ranked == ids  # all equal, so the tie breaks on id


def test_basis_name_and_default_ranking_follow_the_season_being_drafted(db):
    assert rankings.basis_name(db, []) is None
    star, other = player(db, 1), player(db, 2)
    for n in range(1, 6):
        gid = game(db, n)  # season 10, "2025-26 Regular Season"
        skater(db, gid, star, goals=2, assists=1, shots=4)
        skater(db, gid, other, shots=1)
    db.commit()
    history = rankings.history_season_ids(db, 11)
    assert history == [10]
    assert rankings.basis_name(db, history) == "2025-26 Regular Season"
    ranked = [i for i in rankings.default_ranking(db, 11) if i >= BASE]
    assert ranked == [star, other]
    # Drafting season 10 itself, there is no earlier regular season with stats: all equal.
    flat = [i for i in rankings.default_ranking(db, 10) if i >= BASE]
    assert flat == [star, other]  # equal value, so the id order
