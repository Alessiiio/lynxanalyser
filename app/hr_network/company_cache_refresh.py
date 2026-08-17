"""Refresh disk cache for watched companies (level 3, rolling)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.hr_network.fraud_network_cache import store_cached_for_company
from app.hr_network.watched_companies import (
    SOURCE_CASE_OPEN,
    SOURCE_UNDER_INVESTIGATION,
    list_watched_companies,
)

logger = logging.getLogger(__name__)

CACHE_LEVEL = 3
_HIGH_SOURCE = {SOURCE_UNDER_INVESTIGATION, SOURCE_CASE_OPEN}


def _refresh_sort_key(item: dict[str, Any]) -> tuple[int, int, str]:
    state = item.get("cache_state") or "missing"
    state_rank = 0 if state == "missing" else (1 if state == "stale" else 2)
    prio = 0 if (item.get("source_reason") or "") in _HIGH_SOURCE else 1
    return (state_rank, prio, item.get("added_at") or "")


async def refresh_watched_company_caches(
    *,
    limit: int = 8,
    force: bool = False,
) -> dict[str, Any]:
    import config

    delay = float(getattr(config, "WATCHLIST_SCAN_DELAY_SEC", 2) or 0)
    listed = await list_watched_companies(status="active", limit=500, offset=0)
    items = list(listed.get("items") or [])
    if not force:
        items = [c for c in items if c.get("cache_state") in ("missing", "stale")]
    items.sort(key=_refresh_sort_key)
    batch = items[: max(1, min(int(limit or 8), 40))]

    refreshed: list[str] = []
    errors: list[dict[str, str]] = []

    from app.hr_network.fraud_network import build_fraud_network

    for i, company in enumerate(batch):
        name = (company.get("company_name") or "").strip()
        uid = (company.get("company_uid") or "").strip() or None
        if not name and not uid:
            continue
        try:
            data = await build_fraud_network(
                level=CACHE_LEVEL,
                ad_hoc_company={"name": name, "uid": uid or ""},
                max_person_searches=4,
            )
            if data.get("errors") and not data.get("seed_companies"):
                errors.append(
                    {
                        "name": name,
                        "error": (data["errors"][0] or {}).get("error") or "nicht gefunden",
                    }
                )
            else:
                store_cached_for_company(
                    level=CACHE_LEVEL,
                    company_name=name,
                    company_uid=uid,
                    payload=data if isinstance(data, dict) else {},
                )
                refreshed.append(name or uid or "")
        except Exception as e:
            logger.exception("Company cache refresh failed for %s", name)
            errors.append({"name": name, "error": str(e)[:200]})
        if delay and i < len(batch) - 1:
            await asyncio.sleep(delay)

    return {
        "refreshed": len(refreshed),
        "names": refreshed,
        "errors": errors,
        "queued": len(items),
        "limit": len(batch),
    }
