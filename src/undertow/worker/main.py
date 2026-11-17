"""Worker process: the only place scheduled jobs run.

A Postgres advisory lock guarantees a single active scheduler even if the worker is
accidentally scaled to more than one instance. A standby instance waits and retries.
"""

import logging
import signal
import threading
from datetime import UTC, datetime

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import text
from sqlalchemy.engine import Connection

from undertow.config import get_settings
from undertow.data import get_engine
from undertow.data.session import session_factory
from undertow.fantasy import draft_room, lineups, matchup_jobs, rankings, season_calendar
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


def draft_clock_tick() -> None:
    """Autopick for any draft whose pick timer has run out. Never raises, so the job keeps going."""
    try:
        draft_room.process_due_drafts(session_factory(), rankings.default_ranking)
    except Exception:
        log.exception("draft clock tick failed")


def lineup_lock_tick() -> None:
    """Freeze lineups for games that have started. Runs every 30 seconds, so it only logs when it
    actually did something (or failed), and never raises."""
    try:
        summary = lineups.run_lock_job(session_factory())
        if summary != "locked=0 released=0 repaired=0":
            log.info("lineup lock: %s", summary)
    except Exception:
        log.exception("lineup lock tick failed")


def matchup_job_tick() -> None:
    """Update matchup statuses and finalize results whose time has come. Every 5 minutes; only
    logs when something changed, and never raises."""
    try:
        summary = matchup_jobs.run_matchup_job(session_factory())
        if summary != "status_changes=0 finalized=0":
            log.info("matchups: %s", summary)
    except Exception:
        log.exception("matchup job failed")


def calendar_sync_job() -> None:
    """Rebuild the fantasy calendar from the stored schedule. Weeks that have started never move."""
    try:
        sf = session_factory()
        for season_id in get_settings().season_ids:
            with sf() as session:
                weeks = season_calendar.sync_calendar(session, season_id)
            log.info("calendar: season %s has %s fantasy weeks", season_id, len(weeks))
    except Exception:
        log.exception("calendar sync failed")


def build_scheduler(feed: Feed | None = None) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(heartbeat, "interval", minutes=5, id="heartbeat")
    # The draft clock does not need the HockeyTech feed, so it runs even when ingestion is off.
    scheduler.add_job(
        draft_clock_tick,
        "interval",
        seconds=1,
        id="draft_clock",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        lineup_lock_tick,
        "interval",
        seconds=30,
        id="lineup_lock",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        matchup_job_tick,
        "interval",
        minutes=5,
        id="matchup_status",
        max_instances=1,
        coalesce=True,
    )
    # After the 04:30 schedule sync, and once at startup so a fresh deploy has a calendar.
    scheduler.add_job(
        calendar_sync_job,
        "cron",
        hour=4,
        minute=45,
        timezone="America/New_York",
        id="calendar_sync",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(UTC),
    )
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
    # Later phases register scoring recompute and waiver processing here.
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
