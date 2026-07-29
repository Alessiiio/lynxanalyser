"""Compliance queue — reported CompanyCase entries."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from app.database import User
from app.hr_network.company_cases import list_company_cases
from app.routes.deps import require_role

router = APIRouter()


@router.get("/compliance")
async def compliance_page(_user: User = Depends(require_role("compliance", "admin"))):
    return FileResponse("static/compliance.html")


@router.get("/api/compliance/reported-cases")
async def api_reported_cases(_user: User = Depends(require_role("compliance", "admin"))):
    """Open compliance queue: cases with status=reported awaiting Actioned."""
    cases = await list_company_cases(status="reported")
    return {"cases": cases}
