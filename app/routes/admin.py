"""Admin panel: app settings + diagnostics + feature planning notes."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import config
from app.database import User
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


@router.get("/admin")
async def admin_page(_user: User = Depends(require_role("admin"))):
    return FileResponse(_STATIC / "admin.html")


@router.get("/admin/planning")
async def admin_planning_page(_user: User = Depends(require_role("admin"))):
    path = _STATIC / "planning.html"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Planungsseite fehlt")
    return FileResponse(path)


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
    return {"settings": await get_public_settings(), "updated": updated}


# ── Feature planning (admin-only mini board) ─────────────────────────────


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
