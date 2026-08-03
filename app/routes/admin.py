"""Admin panel: app settings + diagnostics."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

import config
from app.database import User
from app.routes.deps import enforce_rate_limit, require_role
from app.settings_store import get_admin_settings, get_public_settings, set_setting

router = APIRouter()

_STATIC = Path(__file__).resolve().parents[2] / "static"


class AdminSettingsPatch(BaseModel):
    anonymize_mode: Optional[bool] = None


@router.get("/admin")
async def admin_page(_user: User = Depends(require_role("admin"))):
    return FileResponse(_STATIC / "admin.html")


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
