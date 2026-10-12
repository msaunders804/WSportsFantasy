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

from undertow.db import get_engine

log = logging.getLogger("undertow.worker")

# Arbitrary constant identifying the scheduler lock ("UNDR").
SCHEDULER_LOCK_ID = 0x554E4452


def try_acquire_scheduler_lock(conn: Connection) -> bool:
    return bool(
        conn.execute(text("SELECT pg_try_advisory_lock(:id)"), {"id": SCHEDULER_LOCK_ID}).scalar()
    )


def heartbeat() -> None:
    log.info("worker heartbeat")


def build_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(heartbeat, "interval", minutes=5, id="heartbeat")
    # Later phases register ingestion, scoring, waiver and draft-clock jobs here.
    return scheduler


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
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
        scheduler = build_scheduler()
        scheduler.start()
        stop.wait()
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    main()
