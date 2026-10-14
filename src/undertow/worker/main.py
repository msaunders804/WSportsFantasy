"""Worker process: the only place scheduled jobs run.

A Postgres advisory lock guarantees a single active scheduler even if the worker is
accidentally scaled to more than one instance. A standby instance waits and retries.
"""

import logging
import signal
import threading

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import text
from sqlalchemy.engine import Connection

from undertow.config import get_settings
from undertow.db import get_engine
from undertow.db.session import session_factory
from undertow.ingest import jobs
from undertow.ingest.feed import Feed, FeedError, HockeyTechFeed

log = logging.getLogger("undertow.worker")

# Arbitrary constant identifying the scheduler lock ("UNDR").
SCHEDULER_LOCK_ID = 0x554E4452


def try_acquire_scheduler_lock(conn: Connection) -> bool:
    return bool(
        conn.execute(text("SELECT pg_try_advisory_lock(:id)"), {"id": SCHEDULER_LOCK_ID}).scalar()
    )


def heartbeat() -> None:
    log.info("worker heartbeat")


def build_scheduler(feed: Feed | None = None) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(heartbeat, "interval", minutes=5, id="heartbeat")
    if feed is not None:
        season_ids = get_settings().season_ids
        sf = session_factory()

        def add(fn, name: str, trigger: str, **trigger_args) -> None:
            scheduler.add_job(
                jobs.run_job,
                trigger,
                args=[sf, name, fn],
                id=name,
                max_instances=1,
                coalesce=True,
                **trigger_args,
            )

        add(
            lambda: jobs.daily_sync(sf, feed, season_ids),
            "daily_sync",
            "cron",
            hour=4,
            minute=30,
            timezone="America/New_York",
        )
        add(
            lambda: jobs.nightly_resync(sf, feed, season_ids),
            "nightly_resync",
            "cron",
            hour=5,
            minute=0,
            timezone="America/New_York",
        )
        add(lambda: jobs.poll_live(sf, feed), "poll_live", "interval", seconds=90)
        add(lambda: jobs.reconcile_finals(sf, feed), "reconcile_finals", "interval", minutes=30)
    # Later phases register scoring recompute, waiver processing and the draft clock here.
    return scheduler


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # httpx logs full request URLs at INFO, which would put the API key in the logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())

    # The lock is session-scoped, so hold this connection open for the process lifetime.
    # AUTOCOMMIT so the connection is not left "idle in transaction" while holding the lock.
    with get_engine().connect().execution_options(isolation_level="AUTOCOMMIT") as lock_conn:
        while not stop.is_set():
            if try_acquire_scheduler_lock(lock_conn):
                break
            log.info("scheduler lock held by another instance; standing by")
            stop.wait(15)
        else:
            return
        log.info("scheduler lock acquired")
        feed: Feed | None = None
        try:
            feed = HockeyTechFeed()
        except FeedError as exc:
            log.warning("ingestion jobs disabled: %s", exc)
        scheduler = build_scheduler(feed)
        scheduler.start()
        stop.wait()
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    main()
