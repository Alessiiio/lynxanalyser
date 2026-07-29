"""APScheduler jobs for person watchlist monitoring."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_scheduler = None


def start_monitoring_scheduler() -> None:
    global _scheduler
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
    except ImportError:
        logger.warning("APScheduler not installed — person monitoring cron disabled")
        return

    if _scheduler is not None:
        return

    scheduler = AsyncIOScheduler()

    async def _job() -> None:
        from app.hr_network.person_monitoring import run_person_monitoring

        try:
            result = await run_person_monitoring(limit=25)
            logger.info(
                "Person monitoring: scanned=%s links=%s alerts=%s",
                result.get("scanned"),
                result.get("new_links"),
                result.get("alerts"),
            )
        except Exception:
            logger.exception("Person monitoring job failed")

    scheduler.add_job(_job, "cron", hour=4, minute=15, id="person_watch_daily")
    scheduler.start()
    _scheduler = scheduler
    logger.info("Person monitoring scheduler started (daily 04:15)")


def shutdown_monitoring_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
