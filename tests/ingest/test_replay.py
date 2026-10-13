import pytest

from undertow.ingest.replay import CachingFeed, leaderboard, run_replay, validate
from undertow.models import GoalieGameStats

pytestmark = pytest.mark.db


def test_caching_feed_fetches_once_then_serves_from_disk(tmp_path, feed):
    calls = []

    class Counting:
        def game_summary(self, game_id):
            calls.append(game_id)
            return feed.game_summary(game_id)

    cf = CachingFeed(Counting(), tmp_path)
    first = cf.game_summary(227)
    second = cf.game_summary(227)
    assert calls == [227] and first.body == second.body
    offline = CachingFeed(None, tmp_path)
    assert offline.game_summary(227).source_url == first.source_url
    with pytest.raises(RuntimeError):
        offline.game_summary(210)


def test_replay_with_simulated_clock_validates_and_scores(session_factory, feed):
    # Fixtures hold only 2 box scores; all other games fail individually and are skipped.
    run_replay(session_factory, feed, 8)
    with session_factory() as s:
        assert validate(s, 8, expect_complete=False) == []
        assert s.get(GoalieGameStats, (227, 28)).decision == "W"
        assert s.get(GoalieGameStats, (210, 211)).decision == "W"
        assert validate(s, 8, expect_complete=True)  # the other 118 games are reported missing
        board = leaderboard(s, 8, top=3)
    assert len(board) == 3 and board[0][3] >= board[1][3] >= board[2][3]


def test_validate_flags_a_corrupted_box_score(session_factory, feed):
    run_replay(session_factory, feed, 8)
    with session_factory() as s:
        row = s.get(GoalieGameStats, (227, 28))
        row.decision = None
        s.commit()
        problems = validate(s, 8, expect_complete=False)
    assert any("game 227" in p and "winning goalies" in p for p in problems)
