from decimal import Decimal

import pytest
from sqlalchemy import func, select

from undertow.data.models import PenaltyEvent, Player, ShotEvent
from undertow.ingest import backfill, sync
from undertow.ingest.parsing import parse_play_by_play, strip_jsonp


def pbp(feed, game_id=210):
    return parse_play_by_play(strip_jsonp(feed.play_by_play(game_id).body), game_id)


def test_parse_counts_and_goal_merge(feed):
    d = pbp(feed)
    by_kind = {k: sum(s.kind == k for s in d.shots) for k in {s.kind for s in d.shots}}
    # 49 shots on net (3 are goals) + 20 blocked + 1 penalty shot; no standalone goal rows
    assert by_kind == {"shot": 49, "blocked_shot": 20, "penalty_shot": 1}
    goals = [s for s in d.shots if s.is_goal]
    assert len(goals) == 3
    assert all(s.kind == "shot" and s.x is not None for s in goals)
    assert len(d.penalties) == 6
    assert d.penalties[0].minutes == Decimal("2.00")
    # every shooter and goalie is available as a player stub
    ids = {p.id for p in d.players}
    assert {s.shooter_id for s in d.shots} <= ids


def test_blocked_shot_has_blocker_and_penalty_shot_has_no_coordinates(feed):
    d = pbp(feed)
    blocked = next(s for s in d.shots if s.kind == "blocked_shot")
    assert blocked.blocker_id is not None and blocked.goalie_id is not None
    ps = next(s for s in d.shots if s.kind == "penalty_shot")
    assert ps.is_penalty_shot and ps.x is None and ps.period_seconds == 949


@pytest.mark.db
def test_store_is_idempotent_and_history_players_have_no_team(session, feed):
    sync.sync_seasons(session, feed, current_season_id=11)
    sync.sync_teams(session, feed, 11)
    sync.sync_schedule(session, feed, 8)
    session.flush()
    for _ in range(2):  # second run must replace, not duplicate
        assert sync.sync_play_by_play(session, feed, 210, history_only=True)
        session.flush()
    assert session.scalar(select(func.count()).select_from(ShotEvent)) == 70
    assert session.scalar(select(func.count()).select_from(PenaltyEvent)) == 6
    stubs = list(session.scalars(select(Player)))
    assert stubs and all(p.pro_team_id is None and p.status == "inactive" for p in stubs)


@pytest.mark.db
def test_backfill_play_by_play_validates_against_box_score(session_factory, feed):
    with session_factory() as s:
        sync.sync_seasons(s, feed, current_season_id=11)
        sync.sync_teams(s, feed, 11)
        sync.sync_schedule(s, feed, 8)
        sync.sync_game(s, feed, 210, history_only=True)
        s.commit()
    from undertow.ingest.backfill import _guard_seasons

    _guard_seasons(session_factory, [8])
    with session_factory() as s:
        assert sync.sync_play_by_play(s, feed, 210, history_only=True)
        s.commit()
    with session_factory() as s:
        problems = [p for p in backfill.validate_play_by_play(s, 8) if p.startswith("game 210 ")]
    assert problems == []


def test_player_zero_is_no_player():
    from undertow.ingest.parsing import _player_from_pxp

    assert _player_from_pxp({"player_id": "0", "first_name": None}) is None
    assert _player_from_pxp({"player_id": 0}) is None
    assert _player_from_pxp(None) is None


def test_player_with_id_but_no_profile_is_kept_with_blank_names():
    from undertow.ingest.parsing import _player_from_pxp

    p = _player_from_pxp(
        {"player_id": "275", "first_name": None, "last_name": None, "team_id": None}
    )
    assert p is not None and (p.id, p.first_name, p.last_name, p.team_id) == (275, "", "", None)
