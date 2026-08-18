"""Async bulk firm scan jobs (Admin): paste names → SW3 → Auswahl-Kandidaten."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.database import BulkScanItem, BulkScanJob, async_session

logger = logging.getLogger(__name__)

# Keep a strong ref so GC does not cancel background workers.
_RUNNING_TASKS: set[asyncio.Task] = set()
_JOB_TASKS: dict[int, asyncio.Task] = {}

DEFAULT_LEVEL = 3
DEFAULT_MAX_PERSON_SEARCHES = 4
DEFAULT_CONCURRENCY = 2
MAX_NAMES_PER_JOB = 80
# Per-firm cap so one slow Zefix/Moneyhouse call cannot stall the whole job.
_TIMEOUT_BY_LEVEL = {1: 90.0, 2: 120.0, 3: 300.0, 4: 420.0, 5: 540.0}


def _iso_utc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        aware = dt.replace(tzinfo=timezone.utc)
    else:
        aware = dt.astimezone(timezone.utc)
    return aware.isoformat().replace("+00:00", "Z")


def parse_company_names(raw: str | list[str]) -> list[str]:
    """One firm per line; CSV first column if comma/semicolon present."""
    if isinstance(raw, list):
        lines = [str(x) for x in raw]
    else:
        lines = str(raw or "").splitlines()
    names: list[str] = []
    seen: set[str] = set()
    for line in lines:
        text = (line or "").strip()
        if not text or text.startswith("#"):
            continue
        # Skip simple CSV header
        if text.lower() in ("name", "firma", "firmenname", "company", "company_name"):
            continue
        if ";" in text or ("," in text and not text.upper().startswith("CHE")):
            sep = ";" if ";" in text else ","
            text = text.split(sep, 1)[0].strip().strip('"').strip("'")
        if not text:
            continue
        key = re.sub(r"\s+", " ", text.lower())
        if key in seen:
            continue
        seen.add(key)
        names.append(text[:512])
    return names


def item_timeout_sec(level: int, override: float | None = None) -> float:
    if override is not None:
        try:
            return max(0.05, min(900.0, float(override)))
        except (TypeError, ValueError):
            pass
    return _TIMEOUT_BY_LEVEL.get(int(level or DEFAULT_LEVEL), 300.0)


def _job_dict(job: BulkScanJob, *, items: list[BulkScanItem] | None = None) -> dict[str, Any]:
    running = [
        {"id": r.id, "input_name": r.input_name or "", "status": r.status}
        for r in (items or [])
        if r.status == "running"
    ]
    queued = sum(1 for r in (items or []) if r.status == "pending")
    return {
        "id": job.id,
        "status": job.status,
        "level": job.level,
        "created_by": job.created_by or "",
        "created_by_username": job.created_by_username or "",
        "created_at": _iso_utc(job.created_at),
        "finished_at": _iso_utc(job.finished_at),
        "total_items": job.total_items or 0,
        "completed_items": job.completed_items or 0,
        "error_count": job.error_count or 0,
        "options": job.options_json or {},
        "currently_scanning": running,
        "queued_count": queued,
        "item_timeout_sec": item_timeout_sec(
            job.level or DEFAULT_LEVEL,
            (job.options_json or {}).get("item_timeout_sec"),
        ),
    }


def _item_dict(item: BulkScanItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "job_id": item.job_id,
        "sort_order": item.sort_order,
        "input_name": item.input_name or "",
        "status": item.status,
        "resolved_uid": item.resolved_uid or "",
        "resolved_name": item.resolved_name or "",
        "address": item.address or "",
        "legal_seat": item.legal_seat or "",
        "ehraid": item.ehraid,
        "result": item.result_json or {},
        "error_message": item.error_message or "",
    }


def _slim_graph(data: dict[str, Any]) -> dict[str, Any]:
    """Keep enough nodes/edges for the bulk-review network (not the full payload)."""
    nodes: list[dict[str, Any]] = []
    for n in data.get("nodes") or []:
        if not isinstance(n, dict) or not n.get("id"):
            continue
        nodes.append(
            {
                "id": n.get("id"),
                "type": n.get("type") or "",
                "label": n.get("label") or n.get("name") or "",
                "uid": n.get("uid") or "",
                "ehraid": n.get("ehraid"),
                "legal_seat": n.get("legal_seat") or "",
                "address": n.get("address") or "",
                "is_seed": bool(n.get("is_seed")),
                "person_status": n.get("person_status") or "",
                "roles": list(n.get("roles") or [])[:8],
                "residence": n.get("residence") or "",
            }
        )
        if len(nodes) >= 200:
            break
    keep_ids = {n["id"] for n in nodes}
    edges: list[dict[str, Any]] = []
    for e in data.get("edges") or []:
        if not isinstance(e, dict):
            continue
        frm, to = e.get("from"), e.get("to")
        if frm not in keep_ids or to not in keep_ids:
            continue
        edges.append(
            {
                "from": frm,
                "to": to,
                "label": (e.get("label") or "")[:80],
                "person_status": e.get("person_status") or "",
            }
        )
        if len(edges) >= 400:
            break
    return {"nodes": nodes, "edges": edges}


def _via_persons(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    company_id: str,
) -> list[str]:
    by_id = {n["id"]: n for n in nodes}
    names: list[str] = []
    seen: set[str] = set()
    for e in edges:
        ends = {e.get("from"), e.get("to")}
        if company_id not in ends:
            continue
        other = next((x for x in ends if x != company_id), None)
        node = by_id.get(other or "")
        if not node or node.get("type") != "person":
            continue
        name = (node.get("label") or "").strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        names.append(name)
        if len(names) >= 4:
            break
    return names


def _compact_scan_result(data: dict[str, Any]) -> dict[str, Any]:
    """Compact firm + persons + slim graph for review/selection."""
    seed = (data.get("seed_companies") or [None])[0] or {}
    persons: list[dict[str, Any]] = []
    for p in data.get("persons_table") or []:
        if not isinstance(p, dict):
            continue
        status = (p.get("status") or "current").strip().lower()
        persons.append(
            {
                "name": p.get("name") or p.get("person_name") or "",
                "person_id": p.get("person_id") or p.get("id") or "",
                "residence": p.get("residence") or "",
                "roles": list(p.get("roles") or []),
                "status": status,
                "nationality": p.get("nationality") or "",
            }
        )
    graph = _slim_graph(data if isinstance(data, dict) else {})
    g_nodes = graph.get("nodes") or []
    g_edges = graph.get("edges") or []
    related: list[dict[str, Any]] = []
    for n in g_nodes:
        if n.get("type") != "company" or n.get("is_seed"):
            continue
        via = _via_persons(g_nodes, g_edges, n.get("id") or "")
        related.append(
            {
                "name": n.get("label") or "",
                "uid": n.get("uid") or "",
                "ehraid": n.get("ehraid"),
                "legal_seat": n.get("legal_seat") or "",
                "address": n.get("address") or "",
                "via": via,
            }
        )
        if len(related) >= 40:
            break
    return {
        "company": {
            "name": seed.get("name") or "",
            "uid": seed.get("uid") or "",
            "ehraid": seed.get("ehraid"),
            "address": seed.get("address") or "",
            "legal_seat": seed.get("legal_seat") or "",
            "status": seed.get("status") or "",
        },
        "persons": persons,
        "related_companies": related,
        "graph": graph,
        "stats": data.get("stats") or {},
        "cached": bool(data.get("cached")),
    }


async def create_bulk_scan_job(
    *,
    names: list[str],
    level: int = DEFAULT_LEVEL,
    created_by: str,
    created_by_username: str | None = None,
    max_person_searches: int = DEFAULT_MAX_PERSON_SEARCHES,
    concurrency: int = DEFAULT_CONCURRENCY,
    timeout_sec: float | None = None,
) -> dict[str, Any]:
    cleaned = parse_company_names(names)
    if not cleaned:
        raise ValueError("Mindestens einen Firmennamen angeben")
    if len(cleaned) > MAX_NAMES_PER_JOB:
        raise ValueError(f"Maximal {MAX_NAMES_PER_JOB} Firmen pro Job")
    level_i = max(1, min(5, int(level or DEFAULT_LEVEL)))
    opts = {
        "max_person_searches": max(0, min(12, int(max_person_searches))),
        "concurrency": max(1, min(4, int(concurrency))),
        "item_timeout_sec": item_timeout_sec(level_i, timeout_sec),
    }
    async with async_session() as session:
        job = BulkScanJob(
            created_by=(created_by or "").strip() or "Admin",
            created_by_username=(created_by_username or "").strip() or None,
            created_at=datetime.now(timezone.utc),
            status="pending",
            level=level_i,
            options_json=opts,
            total_items=len(cleaned),
            completed_items=0,
            error_count=0,
        )
        session.add(job)
        await session.flush()
        for i, name in enumerate(cleaned):
            session.add(
                BulkScanItem(
                    job_id=job.id,
                    sort_order=i,
                    input_name=name,
                    status="pending",
                )
            )
        await session.commit()
        await session.refresh(job)
        job_id = job.id
        payload = _job_dict(job)

    _spawn_worker(job_id)
    return payload


def _spawn_worker(job_id: int) -> bool:
    existing = _JOB_TASKS.get(job_id)
    if existing is not None and not existing.done():
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.error("No event loop to start bulk-scan job %s", job_id)
        return False
    task = loop.create_task(run_bulk_scan_job(job_id), name=f"bulk-scan-{job_id}")
    _RUNNING_TASKS.add(task)
    _JOB_TASKS[job_id] = task

    def _cleanup(done: asyncio.Task) -> None:
        _RUNNING_TASKS.discard(done)
        if _JOB_TASKS.get(job_id) is done:
            _JOB_TASKS.pop(job_id, None)

    task.add_done_callback(_cleanup)
    return True


async def resume_unfinished_bulk_scans() -> dict[str, Any]:
    """After process restart: reset stuck 'running' items and continue unfinished jobs."""
    async with async_session() as session:
        jobs = list(
            (
                await session.execute(
                    select(BulkScanJob).where(BulkScanJob.status.in_(("pending", "running")))
                )
            )
            .scalars()
            .all()
        )
        job_ids = [j.id for j in jobs]
        if job_ids:
            items = list(
                (
                    await session.execute(
                        select(BulkScanItem).where(BulkScanItem.job_id.in_(job_ids))
                    )
                )
                .scalars()
                .all()
            )
            for item in items:
                if item.status == "running":
                    item.status = "pending"
                    item.error_message = None
            await session.commit()

    resumed: list[int] = []
    finished: list[int] = []
    for job_id in job_ids:
        async with async_session() as session:
            rows = list(
                (
                    await session.execute(
                        select(BulkScanItem).where(BulkScanItem.job_id == job_id)
                    )
                )
                .scalars()
                .all()
            )
            leftover = [r for r in rows if r.status in ("pending", "running")]
            job = await session.get(BulkScanJob, job_id)
            if not job:
                continue
            if not leftover:
                job.status = "done"
                job.finished_at = datetime.now(timezone.utc)
                job.completed_items = sum(
                    1 for r in rows if r.status not in ("pending", "running")
                )
                await session.commit()
                finished.append(job_id)
                continue
        if _spawn_worker(job_id):
            resumed.append(job_id)
            logger.info("Resumed bulk-scan job %s (%s items left)", job_id, len(leftover))
    return {"resumed": resumed, "marked_done": finished}


async def get_bulk_scan_job(job_id: int, *, include_items: bool = True) -> dict[str, Any] | None:
    async with async_session() as session:
        job = await session.get(BulkScanJob, job_id)
        if not job:
            return None
        rows: list[BulkScanItem] = []
        if include_items:
            rows = list(
                (
                    await session.execute(
                        select(BulkScanItem)
                        .where(BulkScanItem.job_id == job_id)
                        .order_by(BulkScanItem.sort_order.asc())
                    )
                )
                .scalars()
                .all()
            )
        else:
            rows = list(
                (
                    await session.execute(
                        select(BulkScanItem).where(
                            BulkScanItem.job_id == job_id,
                            BulkScanItem.status.in_(("pending", "running")),
                        )
                    )
                )
                .scalars()
                .all()
            )
        out = _job_dict(job, items=rows)
        if include_items:
            out["items"] = [_item_dict(r) for r in rows]
        return out


async def run_bulk_scan_job(job_id: int) -> None:
    """Process all pending items with limited concurrency."""
    async with async_session() as session:
        job = await session.get(BulkScanJob, job_id)
        if not job:
            return
        if job.status in ("done", "cancelled"):
            return
        job.status = "running"
        opts = dict(job.options_json or {})
        level = int(job.level or DEFAULT_LEVEL)
        await session.commit()

    concurrency = max(1, min(4, int(opts.get("concurrency") or DEFAULT_CONCURRENCY)))
    max_ps = max(0, min(12, int(opts.get("max_person_searches") or DEFAULT_MAX_PERSON_SEARCHES)))
    timeout_sec = item_timeout_sec(level, opts.get("item_timeout_sec"))
    sem = asyncio.Semaphore(concurrency)

    async with async_session() as session:
        items = list(
            (
                await session.execute(
                    select(BulkScanItem)
                    .where(BulkScanItem.job_id == job_id)
                    .order_by(BulkScanItem.sort_order.asc())
                )
            )
            .scalars()
            .all()
        )
        item_ids = [i.id for i in items if i.status in ("pending", "running")]

    async def _one(item_id: int) -> None:
        async with sem:
            await _process_item(
                item_id,
                level=level,
                max_person_searches=max_ps,
                timeout_sec=timeout_sec,
            )

    try:
        await asyncio.gather(*[_one(iid) for iid in item_ids], return_exceptions=True)
    except Exception:
        logger.exception("Bulk-scan job %s failed", job_id)

    async with async_session() as session:
        job = await session.get(BulkScanJob, job_id)
        if not job:
            return
        rows = list(
            (
                await session.execute(
                    select(BulkScanItem).where(BulkScanItem.job_id == job_id)
                )
            )
            .scalars()
            .all()
        )
        done_n = sum(1 for r in rows if r.status not in ("pending", "running"))
        err_n = sum(1 for r in rows if r.status in ("error", "not_found"))
        job.completed_items = done_n
        job.error_count = err_n
        job.status = "done"
        job.finished_at = datetime.now(timezone.utc)
        await session.commit()


async def _process_item(
    item_id: int,
    *,
    level: int,
    max_person_searches: int,
    timeout_sec: float,
) -> None:
    async with async_session() as session:
        item = await session.get(BulkScanItem, item_id)
        if not item or item.status not in ("pending", "running"):
            return
        item.status = "running"
        job_id = item.job_id
        input_name = item.input_name
        await session.commit()

    try:
        from app.hr_network.fraud_network import build_fraud_network
        from app.hr_network.fraud_network_cache import (
            load_cached_for_company,
            store_cached_for_company,
        )

        cached = False
        hit, _key = load_cached_for_company(
            level=level, company_name=input_name, company_uid=None
        )
        if hit is not None:
            data = dict(hit)
            data["cached"] = True
            cached = True
        else:
            data = await asyncio.wait_for(
                build_fraud_network(
                    level=level,
                    ad_hoc_company={"name": input_name, "uid": ""},
                    max_person_searches=max_person_searches,
                ),
                timeout=timeout_sec,
            )
        if data.get("errors") and not data.get("seed_companies"):
            err = (data["errors"][0] or {}).get("error") or "Firma nicht gefunden"
            async with async_session() as session:
                item = await session.get(BulkScanItem, item_id)
                if not item:
                    return
                item.status = "not_found"
                item.error_message = str(err)[:512]
                await session.commit()
            await _bump_job_progress(job_id, error=True)
            return

        compact = _compact_scan_result(data if isinstance(data, dict) else {})
        company = compact.get("company") or {}
        store_cached_for_company(
            level=level,
            company_name=input_name,
            company_uid=company.get("uid"),
            payload={k: v for k, v in data.items() if k not in ("cached", "cached_at")},
        )
        resolved_name = (company.get("name") or "").strip()
        if resolved_name and resolved_name.lower() != (input_name or "").strip().lower():
            store_cached_for_company(
                level=level,
                company_name=resolved_name,
                company_uid=company.get("uid"),
                payload={k: v for k, v in data.items() if k not in ("cached", "cached_at")},
            )
        compact["cached"] = cached
        async with async_session() as session:
            item = await session.get(BulkScanItem, item_id)
            if not item:
                return
            item.status = "matched"
            item.resolved_name = (company.get("name") or input_name)[:512]
            item.resolved_uid = (company.get("uid") or "")[:32] or None
            item.address = (company.get("address") or "")[:1024] or None
            item.legal_seat = (company.get("legal_seat") or "")[:255] or None
            ehraid = company.get("ehraid")
            try:
                item.ehraid = int(ehraid) if ehraid is not None else None
            except (TypeError, ValueError):
                item.ehraid = None
            item.result_json = compact
            item.error_message = None
            await session.commit()
        await _bump_job_progress(job_id, error=False)
    except LookupError as e:
        async with async_session() as session:
            item = await session.get(BulkScanItem, item_id)
            if not item:
                return
            item.status = "not_found"
            item.error_message = str(e)[:512]
            await session.commit()
        await _bump_job_progress(job_id, error=True)
    except (TimeoutError, asyncio.TimeoutError):
        logger.warning(
            "Bulk-scan item %s timed out after %.0fs (%s)",
            item_id,
            timeout_sec,
            input_name,
        )
        async with async_session() as session:
            item = await session.get(BulkScanItem, item_id)
            if not item:
                return
            item.status = "error"
            item.error_message = (
                f"Zeitüberschreitung nach {int(timeout_sec)}s — Firma übersprungen, Rest läuft weiter."
            )[:512]
            await session.commit()
        await _bump_job_progress(job_id, error=True)
    except Exception as e:
        logger.exception("Bulk-scan item %s failed (%s)", item_id, input_name)
        async with async_session() as session:
            item = await session.get(BulkScanItem, item_id)
            if not item:
                return
            item.status = "error"
            item.error_message = str(e)[:512]
            await session.commit()
        await _bump_job_progress(job_id, error=True)


async def _bump_job_progress(job_id: int, *, error: bool) -> None:
    async with async_session() as session:
        job = await session.get(BulkScanJob, job_id)
        if not job:
            return
        job.completed_items = int(job.completed_items or 0) + 1
        if error:
            job.error_count = int(job.error_count or 0) + 1
        await session.commit()
