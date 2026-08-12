"""Product changelog + wishlist / feedback API and pages."""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.changelog_md import changelog_flat_entries, parse_changelog
from app.database import User
from app.routes.deps import enforce_rate_limit, get_current_user, require_role
from app.wishlist_store import (
    STATUS_DE,
    TYPE_DE,
    VALID_STATUSES,
    VALID_TYPES,
    add_item,
    list_items,
    update_status,
)
from pathlib import Path

router = APIRouter()

_STATIC = Path(__file__).resolve().parents[2] / "static"


class FeedbackCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=200)
    description: str = Field("", max_length=4000)
    type: Literal["bug", "feature"] = "feature"


class WishlistStatusPatch(BaseModel):
    status: Literal["open", "reviewing", "in_progress", "done", "rejected"]


@router.get("/changelog")
async def changelog_page(_user: User = Depends(get_current_user)):
    path = _STATIC / "changelog.html"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Changelog-Seite fehlt")
    return FileResponse(path)


@router.get("/feedback")
async def feedback_page(_user: User = Depends(get_current_user)):
    path = _STATIC / "feedback.html"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Feedback-Seite fehlt")
    return FileResponse(path)


@router.get("/api/changelog")
async def api_changelog(_user: User = Depends(get_current_user)):
    releases = parse_changelog()
    return {
        "releases": releases,
        "entries": changelog_flat_entries(),
        "source": "CHANGELOG.md",
    }


@router.post("/api/feedback")
async def api_create_feedback(
    body: FeedbackCreate,
    http_request: Request,
    user: User = Depends(get_current_user),
):
    enforce_rate_limit(http_request)
    try:
        item = add_item(
            title=body.title,
            description=body.description,
            type_=body.type,
            created_by=user.display_name or user.username,
            created_by_user_id=user.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"item": item}


@router.get("/api/wishlist")
async def api_list_wishlist(
    status: Optional[str] = Query(None),
    type: Optional[str] = Query(None, alias="type"),
    _user: User = Depends(get_current_user),
):
    if status and status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail="Ungültiger Status")
    if type and type not in VALID_TYPES:
        raise HTTPException(status_code=400, detail="Ungültiger Typ")
    items = list_items(status=status, type_=type)
    return {
        "items": items,
        "labels": {"status": STATUS_DE, "type": TYPE_DE},
    }


@router.patch("/api/wishlist/{item_id}")
async def api_patch_wishlist(
    item_id: str,
    body: WishlistStatusPatch,
    http_request: Request,
    user: User = Depends(require_role("admin")),
):
    enforce_rate_limit(http_request)
    try:
        item = update_status(
            item_id,
            status=body.status,
            updated_by=user.display_name or user.username,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Eintrag nicht gefunden") from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"item": item}
