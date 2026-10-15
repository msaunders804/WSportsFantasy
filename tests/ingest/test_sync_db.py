import pytest
from sqlalchemy import func, select

from undertow.data.models import (
    Game,
    GoalieGameStats,
    Player,
    ProTeam,
    RawSnapshot,
    Season,
    SkaterGameStats,
)
from undertow.ingest import sync

pytestmark = pytest.mark.db


def seed(session, feed):
    sync.sync_seasons(session, feed, current_season_id=11)
    sync.sync_teams(session, feed, 11)
    sync.sync_schedule(session, feed, 8)
    session.flush()


def count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def test_reference_and_schedule_sync(session, feed):
    seed(session, feed)
    assert count(session, ProTeam) == 12
    assert session.get(Season, 11).is_current and not session.get(Season, 8).is_current
    assert count(session, Game) == 120
    assert session.get(ProTeam, 12).conference == "West"  # VGS
    assert session.get(ProTeam, 1).conference == "East"
    g = session.get(Game, 227)
    assert (g.status, g.result_type) == ("final", "SO")


def test_roster_sync_is_idempotent_and_f_d_g(session, feed):
    seed(session, feed)
    n = sync.sync_roster(session, feed, 1, 10)
    sync.sync_roster(session, feed, 1, 10)
    session.flush()
    assert count(session, Player) == n
    assert set(session.scalars(select(Player.position)).all()) <= {"F", "D", "G"}


def test_box_score_sync_is_idempotent(session, feed):
    seed(session, feed)
    assert sync.sync_game(session, feed, 227)
    session.flush()
    skaters, goalies, players = (
        count(session, m) for m in (SkaterGameStats, GoalieGameStats, Player)
    )
    assert (skaters, goalies) == (38, 4)
    sync.sync_game(session, feed, 227)
    session.flush()
    assert (
        count(session, SkaterGameStats),
        count(session, GoalieGameStats),
        count(session, Player),
    ) == (
        skaters,
        goalies,
        players,
    )
    win = session.get(GoalieGameStats, (227, 28))
    assert (win.decision, win.saves, win.goals_against, win.shutout) == ("W", 35, 1, False)


def test_box_score_does_not_overwrite_roster_player_and_unknown_game_is_skipped(session, feed):
    seed(session, feed)
    sync.sync_roster(session, feed, 1, 8)
    session.flush()
    before = {p.id: (p.pro_team_id, p.feed_status) for p in session.scalars(select(Player))}
    sync.sync_game(session, feed, 210)
    session.flush()
    for pid, snapshot in before.items():
        p = session.get(Player, pid)
        assert (p.pro_team_id, p.feed_status) == snapshot
    session.execute(Game.__table__.delete().where(Game.id == 227))
    assert sync.sync_game(session, feed, 227) is False


def test_raw_snapshot_dedupes_by_content_hash_and_url_has_no_key(session, feed):
    seed(session, feed)
    n = count(session, RawSnapshot)
    sync.sync_teams(session, feed, 11)
    session.flush()
    assert count(session, RawSnapshot) == n  # identical response: nothing new stored
    assert not any("key=" in u for u in session.scalars(select(RawSnapshot.url)))


def test_stored_box_score_scores_end_to_end(session, feed):
    from fractions import Fraction

    from undertow.fantasy.scoring import ScoringConfig, score_goalie
    from undertow.fantasy.stat_lines import goalie_stat_line

    seed(session, feed)
    sync.sync_game(session, feed, 227)
    session.flush()
    game = session.get(Game, 227)
    cfg = ScoringConfig()
    mtl = score_goalie(
        goalie_stat_line(session.get(GoalieGameStats, (227, 28)), game.result_type), cfg
    )
    tor = score_goalie(
        goalie_stat_line(session.get(GoalieGameStats, (227, 211)), game.result_type), cfg
    )
    assert mtl.total == Fraction(23, 3)  # SO win 8/3 + 35 saves * 0.2 - 2 GA
    assert tor.total == 5  # SOL 1 + 30 saves * 0.2 - 2 GA
