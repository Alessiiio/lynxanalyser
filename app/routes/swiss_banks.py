"""Swiss bank master lookup endpoints (local SIX snapshot)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.database import User
from app.routes.deps import get_current_user
from app.swiss_banks import load_bank_master, lookup_clearing, search_banks

router = APIRouter(prefix="/api/swiss-banks", tags=["swiss-banks"])


@router.get("/meta")
async def swiss_banks_meta(_user: User = Depends(get_current_user)):
    m = load_bank_master()
    return {
        "count": m.get("count") or 0,
        "valid_on": m.get("valid_on"),
        "source": m.get("source"),
        "source_url": m.get("source_url"),
    }


@router.get("/lookup")
async def swiss_banks_lookup(
    q: str = Query(..., min_length=1, max_length=64),
    _user: User = Depends(get_current_user),
):
    return lookup_clearing(q)


@router.get("/search")
async def swiss_banks_search(
    q: str = Query(..., min_length=2, max_length=80),
    _user: User = Depends(get_current_user),
):
    return search_banks(q)
