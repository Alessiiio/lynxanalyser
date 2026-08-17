"""Admin hub: settings, exports, audit, diagnostics, planning."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

import config
from app.audit_log import export_audit_csv, list_audit_events, record_audit
from app.database import CompanyCase, NetworkAlert, User, WatchedCompany, WatchedPerson, async_session
from app.hr_network.company_cases import (
    ACTIVE_FRAUD_STATUSES,
    export_flagged_company_names_csv,
    export_flagged_person_names_csv,
    export_fraud_companies_csv,
)
from app.hr_network.person_monitoring import list_watched_persons
from app.hr_network.watched_companies import (
    export_companies_csv,
    export_persons_csv,
    list_watched_companies,
)
from app.planning_store import (
    PRIORITY_DE,
    STATUS_DE,
    VALID_PRIORITIES,
    VALID_STATUSES,
    add_item as planning_add,
    delete_item as planning_delete,
    list_items as planning_list,
    update_item as planning_update,
)
from app.routes.deps import enforce_rate_limit, require_role
from app.settings_store import get_admin_settings, get_public_settings, set_setting

router = APIRouter()

_STATIC = Path(__file__).resolve().parents[2] / "static"

PriorityLit = Literal["low", "med", "high"]
StatusLit = Literal["idea", "planned", "building", "done", "parked"]


class AdminSettingsPatch(BaseModel):
    anonymize_mode: Optional[bool] = None


class PlanningCreate(BaseModel):
    title: str = Field(..., min_length=2, max_length=200)
    body: str = Field("", max_length=20000)
    status: StatusLit = "idea"
    priority: PriorityLit = "med"
    tags: Union[list[str], str] = Field(default_factory=list)


class PlanningPatch(BaseModel):
    title: Optional[str] = Field(None, min_length=2, max_length=200)
    body: Optional[str] = Field(None, max_length=20000)
    status: Optional[StatusLit] = None
    priority: Optional[PriorityLit] = None
    tags: Optional[Union[list[str], str]] = None
    meta: Optional[dict[str, Any]] = None


def _csv_response(content: str, filename: str) -> Response:
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/admin")
async def admin_page(_user: User = Depends(require_role("admin"))):
    return FileResponse(_STATIC / "admin.html")


@router.get("/admin/planning")
async def admin_planning_page(_user: User = Depends(require_role("admin"))):
    path = _STATIC / "planning.html"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Planungsseite fehlt")
    return FileResponse(path)


@router.get("/api/admin/overview")
async def api_admin_overview(_user: User = Depends(require_role("admin"))):
    async with async_session() as session:
        fraud_n = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(CompanyCase)
                    .where(CompanyCase.status.in_(ACTIVE_FRAUD_STATUSES))
                )
            ).scalar_one()
            or 0
        )
        persons_n = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(WatchedPerson)
                    .where(WatchedPerson.status != "cleared")
                )
            ).scalar_one()
            or 0
        )
        companies_n = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(WatchedCompany)
                    .where(WatchedCompany.status == "active")
                )
            ).scalar_one()
            or 0
        )
        alerts_n = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(NetworkAlert)
                    .where(NetworkAlert.acknowledged.is_(False))
                )
            ).scalar_one()
            or 0
        )
    return {
        "fraud_cases_active": fraud_n,
        "watched_persons": persons_n,
        "watched_companies": companies_n,
        "open_alerts": alerts_n,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


@router.get("/api/admin/exports/fraud-companies.csv")
async def api_export_fraud_companies(
    request: Request,
    user: User = Depends(require_role("admin")),
):
    csv_text = await export_fraud_companies_csv()
    await record_audit(
        action="export_fraud_companies",
        actor_username=user.username,
        actor_display=user.display_name,
        detail=f"bytes={len(csv_text.encode('utf-8'))}",
        request=request,
    )
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    return _csv_response(csv_text, f"lynx_fraud_companies_{day}.csv")


@router.get("/api/admin/export/flagged-company-names")
async def api_export_flagged_company_names(
    request: Request,
    user: User = Depends(require_role("admin")),
):
    csv_text = await export_flagged_company_names_csv()
    await record_audit(
        action="ds_export_company_names",
        actor_username=user.username,
        actor_display=user.display_name,
        detail=f"bytes={len(csv_text.encode('utf-8'))}",
        request=request,
    )
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    return _csv_response(csv_text, f"lynx_ds_firmennamen_{day}.csv")


@router.get("/api/admin/export/flagged-person-names")
async def api_export_flagged_person_names(
    request: Request,
    user: User = Depends(require_role("admin")),
):
    csv_text = await export_flagged_person_names_csv()
    await record_audit(
        action="ds_export_person_names",
        actor_username=user.username,
        actor_display=user.display_name,
        detail=f"bytes={len(csv_text.encode('utf-8'))}",
        request=request,
    )
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    return _csv_response(csv_text, f"lynx_ds_personennamen_{day}.csv")


@router.get("/api/admin/exports/watched-persons.csv")
async def api_export_watched_persons_admin(
    request: Request,
    user: User = Depends(require_role("admin")),
):
    items: list = []
    offset = 0
    while True:
        data = await list_watched_persons(limit=200, offset=offset)
        batch = data.get("items") or []
        items.extend(batch)
        if len(batch) < 200:
            break
        offset += 200
        if offset > 10000:
            break
    csv_text = export_persons_csv(items)
    await record_audit(
        action="export_watched_persons",
        actor_username=user.username,
        actor_display=user.display_name,
        detail=f"count={len(items)}",
        request=request,
    )
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    return _csv_response(csv_text, f"lynx_watchlist_persons_{day}.csv")


@router.get("/api/admin/exports/watched-companies.csv")
async def api_export_watched_companies_admin(
    request: Request,
    user: User = Depends(require_role("admin")),
):
    # status=None → alle (active + cleared); API-Limit max 500/Seite
    items: list = []
    offset = 0
    page_size = 500
    while True:
        data = await list_watched_companies(status=None, limit=page_size, offset=offset)
        batch = data.get("items") or []
        items.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
        if offset > 10000:
            break
    csv_text = export_companies_csv(items)
    await record_audit(
        action="export_watched_companies",
        actor_username=user.username,
        actor_display=user.display_name,
        detail=f"count={len(items)}",
        request=request,
    )
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    return _csv_response(csv_text, f"lynx_watchlist_companies_{day}.csv")


@router.get("/api/admin/audit")
async def api_admin_audit(
    action: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _user: User = Depends(require_role("admin")),
):
    return await list_audit_events(action=action, limit=limit, offset=offset)


@router.get("/api/admin/audit/export.csv")
async def api_admin_audit_export(
    request: Request,
    action: Optional[str] = Query(None),
    user: User = Depends(require_role("admin")),
):
    data = await list_audit_events(action=action, limit=500, offset=0)
    csv_text = export_audit_csv(data.get("items") or [])
    await record_audit(
        action="export_audit",
        actor_username=user.username,
        actor_display=user.display_name,
        detail=f"count={len(data.get('items') or [])}",
        request=request,
    )
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    return _csv_response(csv_text, f"lynx_audit_{day}.csv")


@router.get("/api/admin/settings")
async def api_get_admin_settings(_user: User = Depends(require_role("admin"))):
    settings = await get_admin_settings()
    return {
        "settings": {k: v for k, v in settings.items() if not k.startswith("_")},
        "meta": settings.get("_meta") or {},
        "runtime": {
            "environment": config.ENVIRONMENT,
            "cache_ttl_seconds": config.CACHE_TTL_SECONDS,
            "rate_limit_per_minute": config.RATE_LIMIT_PER_MINUTE,
            "https_only": config.HTTPS_ONLY,
            "zefix_configured": bool(config.ZEFIX_USERNAME and config.ZEFIX_PASSWORD),
            "virustotal_configured": bool(config.VIRUSTOTAL_API_KEY),
            "safebrowsing_configured": bool(config.GOOGLE_SAFEBROWSING_API_KEY),
            "urlscan_configured": bool(config.URLSCAN_API_KEY),
            "ollama_configured": bool(config.OLLAMA_BASE_URL),
            "anthropic_configured": bool(config.ANTHROPIC_API_KEY),
        },
    }


@router.patch("/api/admin/settings")
async def api_patch_admin_settings(
    body: AdminSettingsPatch,
    http_request: Request,
    user: User = Depends(require_role("admin")),
):
    enforce_rate_limit(http_request)
    updated: dict[str, Any] = {}
    by = user.display_name or user.username
    if body.anonymize_mode is not None:
        updated["anonymize_mode"] = await set_setting(
            "anonymize_mode", bool(body.anonymize_mode), updated_by=by
        )
        await record_audit(
            action="setting_change",
            actor_username=user.username,
            actor_display=user.display_name,
            target="anonymize_mode",
            detail=str(bool(body.anonymize_mode)),
            request=http_request,
        )
    return {"settings": await get_public_settings(), "updated": updated}


@router.get("/api/admin/planning")
async def api_list_planning(
    status: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    tag: Optional[str] = Query(None),
    q: Optional[str] = Query(None, max_length=120),
    _user: User = Depends(require_role("admin")),
):
    if status and status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail="Ungültiger Status")
    if priority and priority not in VALID_PRIORITIES and priority not in ("medium", "mittel"):
        raise HTTPException(status_code=400, detail="Ungültige Priorität")
    try:
        items = planning_list(status=status, priority=priority, tag=tag, q=q)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {
        "items": items,
        "labels": {"status": STATUS_DE, "priority": PRIORITY_DE},
        "statuses": sorted(VALID_STATUSES),
        "priorities": sorted(VALID_PRIORITIES),
    }


@router.post("/api/admin/planning")
async def api_create_planning(
    body: PlanningCreate,
    http_request: Request,
    user: User = Depends(require_role("admin")),
):
    enforce_rate_limit(http_request)
    try:
        item = planning_add(
            title=body.title,
            body=body.body or "",
            status=body.status,
            priority=body.priority,
            tags=body.tags,
            created_by=user.display_name or user.username,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"item": item}


@router.patch("/api/admin/planning/{item_id}")
async def api_patch_planning(
    item_id: str,
    body: PlanningPatch,
    http_request: Request,
    user: User = Depends(require_role("admin")),
):
    enforce_rate_limit(http_request)
    if (
        body.title is None
        and body.body is None
        and body.status is None
        and body.priority is None
        and body.tags is None
        and body.meta is None
    ):
        raise HTTPException(status_code=400, detail="Keine Änderungen")
    try:
        item = planning_update(
            item_id,
            title=body.title,
            body=body.body,
            status=body.status,
            priority=body.priority,
            tags=body.tags,
            meta=body.meta,
            updated_by=user.display_name or user.username,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Eintrag nicht gefunden") from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"item": item}


@router.delete("/api/admin/planning/{item_id}")
async def api_delete_planning(
    item_id: str,
    http_request: Request,
    _user: User = Depends(require_role("admin")),
):
    enforce_rate_limit(http_request)
    try:
        planning_delete(item_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Eintrag nicht gefunden") from None
    return {"ok": True, "id": item_id}
