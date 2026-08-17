"""APScheduler jobs for person watchlist monitoring + SHAB daily ingest."""

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
        import config
        from app.hr_network.person_monitoring import run_person_monitoring

        try:
            result = await run_person_monitoring(
                limit=config.WATCHLIST_SCAN_BATCH,
                include_high_priority=True,
                high_priority_cap=config.WATCHLIST_SCAN_HIGH_PRIORITY_CAP,
            )
            email = result.get("email") or {}
            cov = result.get("coverage") or {}
            sel = result.get("selection") or {}
            logger.info(
                "Person monitoring: scanned=%s high=%s rolling=%s links=%s alerts=%s "
                "email_sent=%s coverage=%s",
                result.get("scanned"),
                sel.get("high_priority_selected"),
                sel.get("rolling_selected"),
                result.get("new_links"),
                result.get("alerts"),
                email.get("sent"),
                cov.get("hint"),
            )
        except Exception:
            logger.exception("Person monitoring job failed")

    async def _shab_daily_job() -> None:
        import config
        from app.hr_network.shab_daily import ingest_enabled, run_shab_daily_ingest

        if not ingest_enabled():
            logger.debug("SHAB daily ingest skipped (SHAB_DAILY_INGEST off)")
            return
        try:
            result = await run_shab_daily_ingest()
            if result.get("skipped"):
                logger.info("SHAB daily ingest skipped: %s", result.get("reason"))
                return
            if result.get("error"):
                logger.error("SHAB daily ingest error: %s", result.get("error"))
                return
            match = result.get("match") or {}
            logger.info(
                "SHAB daily ingest: window=%s..%s fetched=%s upserted=%s alerts=%s",
                result.get("window_start"),
                result.get("window_end"),
                result.get("fetched"),
                (result.get("upsert") or {}).get("upserted"),
                match.get("alerts"),
            )
        except Exception:
            logger.exception("SHAB daily ingest job failed")

    async def _company_cache_job() -> None:
        import config
        from app.hr_network.company_cache_refresh import refresh_watched_company_caches

        try:
            result = await refresh_watched_company_caches(
                limit=config.COMPANY_CACHE_REFRESH_BATCH
            )
            logger.info(
                "Company cache refresh: refreshed=%s queued=%s errors=%s",
                result.get("refreshed"),
                result.get("queued"),
                len(result.get("errors") or []),
            )
        except Exception:
            logger.exception("Company cache refresh job failed")

    scheduler.add_job(_job, "cron", hour=4, minute=15, id="person_watch_daily")
    # After MH batch (04:15): CH-wide SHAB day archive (+ optional watchlist match)
    scheduler.add_job(_shab_daily_job, "cron", hour=5, minute=45, id="shab_daily_ingest")
    scheduler.add_job(_company_cache_job, "cron", hour=6, minute=15, id="company_watch_cache_daily")
    scheduler.start()
    _scheduler = scheduler
    import config as _cfg

    logger.info(
        "Person monitoring scheduler started (daily 04:15, "
        "high-prio cap=%s + rolling batch=%s; SHAB daily 05:45 ingest=%s; "
        "company cache 06:15 batch=%s)",
        _cfg.WATCHLIST_SCAN_HIGH_PRIORITY_CAP,
        _cfg.WATCHLIST_SCAN_BATCH,
        "on" if _cfg.SHAB_DAILY_INGEST else "off",
        _cfg.COMPANY_CACHE_REFRESH_BATCH,
    )


def shutdown_monitoring_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
