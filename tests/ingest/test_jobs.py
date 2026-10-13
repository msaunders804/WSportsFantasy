from datetime import timedelta

import pytest
from sqlalchemy import select

from undertow.ingest import jobs, sync
from undertow.ingest.feed import FeedError
from undertow.models import Game, GoalieGameStats, JobRun, Player

pytestmark = pytest.mark.db


class NoNetworkFeed:
    """Any call fails: proves a job made no requests."""

    def __getattr__(self, name):
        raise AssertionError(f"unexpected feed call: {name}")


@pytest.fixture
def seeded(session_factory, feed):
    with session_factory() as s:
        sync.sync_seasons(s, feed)
        sync.sync_teams(s, feed, 11)
        sync.sync_schedule(s, feed, 8)
        s.commit()
        return s.get(Game, 227).start_utc


def game(session_factory, game_id=227):
    with session_factory() as s:
        return s.get(Game, game_id)


def test_poll_live_makes_no_requests_when_nothing_is_live(session_factory, seeded):
    detail = jobs.poll_live(session_factory, NoNetworkFeed(), now=seeded - timedelta(days=3))
    assert detail == "no live games"


def test_poll_live_fetches_games_in_window_and_stamps_final_at(session_factory, feed, seeded):
    now = seeded + timedelta(hours=2)
    # Other games starting near game 227 have no fixture; they fail individually and are skipped.
    jobs.poll_live(session_factory, feed, now=now)
    g = game(session_factory)
    assert g.status == "final" and g.final_at == now
    with session_factory() as s:
        assert s.get(GoalieGameStats, (227, 28)).decision == "W"


def test_poll_live_window_excludes_finished_and_old_games(session_factory, feed, seeded):
    jobs.poll_live(session_factory, feed, now=seeded + timedelta(hours=2))
    with session_factory() as s:
        assert 227 not in jobs.live_game_ids(s, seeded + timedelta(hours=3))  # already final
        assert jobs.live_game_ids(s, seeded + timedelta(days=2)) == []


def test_reconcile_runs_at_6h_then_24h_after_final_only(session_factory, feed, seeded):
    t0 = seeded + timedelta(hours=2)
    jobs.poll_live(session_factory, feed, now=t0)

    jobs.reconcile_finals(session_factory, feed, now=t0 + timedelta(hours=5, minutes=59))
    assert game(session_factory).reconcile_count == 0
    jobs.reconcile_finals(session_factory, feed, now=t0 + timedelta(hours=6))
    assert game(session_factory).reconcile_count == 1
    jobs.reconcile_finals(session_factory, feed, now=t0 + timedelta(hours=23))
    assert game(session_factory).reconcile_count == 1
    jobs.reconcile_finals(session_factory, feed, now=t0 + timedelta(hours=24))
    assert game(session_factory).reconcile_count == 2
    # Both follow-ups done: nothing further, and no requests.
    assert jobs.reconcile_finals(session_factory, NoNetworkFeed(), now=t0 + timedelta(days=9)) == (
        "reconciled=0/0"
    )


def test_nightly_resync_refetches_last_seven_days_only(session_factory, feed, seeded):
    jobs.nightly_resync(session_factory, feed, [8], now=seeded + timedelta(days=3))
    with session_factory() as s:
        assert s.get(GoalieGameStats, (227, 28)) is not None
    with session_factory() as s:
        s.execute(GoalieGameStats.__table__.delete())
        s.commit()
    jobs.nightly_resync(session_factory, feed, [8], now=seeded + timedelta(days=30))
    with session_factory() as s:
        assert s.get(GoalieGameStats, (227, 28)) is None  # too old to be re-fetched


def test_daily_sync_isolates_roster_failures(session_factory, feed):
    # Only BOS has a roster fixture; the other 11 teams raise and must not abort the job.
    detail = jobs.daily_sync(session_factory, feed, [8])
    with session_factory() as s:
        assert s.scalar(select(Game).limit(1)) is not None
        assert s.scalars(select(Player)).first() is not None
    assert "games=120" in detail


def test_run_job_records_success_and_failure_without_raising(session_factory):
    assert jobs.run_job(session_factory, "ok_job", lambda: "did things") is True

    def boom():
        raise FeedError("feed down")

    assert jobs.run_job(session_factory, "bad_job", boom) is False
    with session_factory() as s:
        runs = {r.job: r for r in s.scalars(select(JobRun))}
    assert runs["ok_job"].ok and runs["ok_job"].detail == "did things"
    assert not runs["bad_job"].ok and "feed down" in runs["bad_job"].detail
