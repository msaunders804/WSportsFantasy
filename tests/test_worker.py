import pytest
from sqlalchemy import create_engine

from undertow.config import get_settings
from undertow.worker.main import build_scheduler, try_acquire_scheduler_lock


def test_scheduler_has_heartbeat():
    assert [j.id for j in build_scheduler().get_jobs()] == ["heartbeat"]


@pytest.mark.db
def test_advisory_lock_is_exclusive():
    engine = create_engine(get_settings().database_url)
    with engine.connect() as a, engine.connect() as b:
        assert try_acquire_scheduler_lock(a) is True
        assert try_acquire_scheduler_lock(b) is False


def test_ingestion_jobs_registered_only_when_a_feed_is_given():
    ids = {j.id for j in build_scheduler(feed=object()).get_jobs()}
    assert {"daily_sync", "nightly_resync", "poll_live", "reconcile_finals"} <= ids
    assert {j.id for j in build_scheduler().get_jobs()} == {"heartbeat"}
