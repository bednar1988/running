import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler

from db import SessionLocal
from garmin_sync import run_full_sync

logger = logging.getLogger("running.scheduler")

_scheduler: BackgroundScheduler | None = None


def _scheduled_sync():
    db = SessionLocal()
    try:
        result = run_full_sync(db)
        logger.info("Scheduled sync done: %s", result)
    except Exception:
        logger.exception("Scheduled sync failed")
    finally:
        db.close()


def start_scheduler() -> BackgroundScheduler | None:
    global _scheduler
    interval_hours = float(os.getenv("SYNC_INTERVAL_HOURS", "6"))
    if interval_hours <= 0:
        logger.info("SYNC_INTERVAL_HOURS <= 0, background sync disabled (manual /api/sync only)")
        return None

    _scheduler = BackgroundScheduler()
    _scheduler.add_job(_scheduled_sync, "interval", hours=interval_hours)
    _scheduler.start()
    logger.info("Background sync scheduled every %s hours", interval_hours)
    return _scheduler


def stop_scheduler() -> None:
    if _scheduler:
        _scheduler.shutdown(wait=False)
