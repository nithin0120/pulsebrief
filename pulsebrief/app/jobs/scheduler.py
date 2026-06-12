"""APScheduler job for daily digest delivery."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from zoneinfo import ZoneInfo

from app.config import settings
from app.database import SessionLocal, init_db
from app.services.digest_service import DigestService

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _run_scheduled_digest() -> None:
    logger.info("Running scheduled digest job")
    db = SessionLocal()
    try:
        service = DigestService(db)
        asyncio.run(service.run_digest(send=True))
    except Exception:
        logger.exception("Scheduled digest failed")
    finally:
        db.close()


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    init_db()
    tz = ZoneInfo(settings.timezone)
    interval_hours = max(1, settings.digest_interval_hours)

    # First run shortly after startup (if enabled), then every interval.
    if settings.run_on_startup:
        first_run = datetime.now(tz) + timedelta(seconds=10)
    else:
        first_run = datetime.now(tz) + timedelta(hours=interval_hours)

    _scheduler = BackgroundScheduler(timezone=tz)
    _scheduler.add_job(
        _run_scheduled_digest,
        trigger=IntervalTrigger(hours=interval_hours, timezone=tz),
        id="recurring_digest",
        replace_existing=True,
        name="PulseBrief recurring digest",
        next_run_time=first_run,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    logger.info(
        "Scheduler started: digest every %d hours (%s); first run at %s",
        interval_hours,
        settings.timezone,
        first_run.strftime("%Y-%m-%d %H:%M:%S %Z"),
    )
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Scheduler stopped")
