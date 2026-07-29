"""Blocklist, goldlist, history, feedback, PDF."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response

from app.blocklist import get_blocklist, remove_fraud
from app.database import User, get_scan_by_id, get_scan_history
from app.fraud_confirm import apply_fraud_confirmation
from app.goldlist import add_domain, get_goldlist, remove_domain
from app.llm_feedback import apply_llm_user_feedback
from app.models import FraudConfirmRequest, FullReport, GoldlistUpdateRequest, LlmFeedbackRequest
from app.pdf_export import build_pdf_report
from app.routes.deps import get_current_user

router = APIRouter()


@router.get("/history")
async def history_page():
    return FileResponse("static/history.html")


@router.get("/goldlist")
async def goldlist_page():
    return FileResponse("static/goldlist.html")


@router.get("/compare")
async def compare_page():
    return FileResponse("static/compare.html")


@router.get("/blocklist")
async def blocklist_page():
    return FileResponse("static/blocklist.html")


@router.post("/api/fraud-confirm")
async def fraud_confirm(request: FraudConfirmRequest, user: User = Depends(get_current_user)):
    entry = await apply_fraud_confirmation(
        domain=request.domain,
        url=request.url,
        fraud_category=request.fraud_category,
        feedback_text=request.feedback_text,
        checks=request.checks,
        analyst_id=user.username,
    )
    return {"confirmed": True, "entry": entry, "blocklist": get_blocklist()}


@router.get("/api/blocklist")
async def list_blocklist():
    return {"entries": get_blocklist()}


@router.delete("/api/blocklist")
async def delete_blocklist_domain(domain: str = Query(...)):
    removed = remove_fraud(domain)
    return {"removed": removed, "entries": get_blocklist()}


@router.post("/api/llm-feedback", response_model=FullReport)
async def llm_feedback(request: LlmFeedbackRequest, user: User = Depends(get_current_user)):
    report = await apply_llm_user_feedback(
        checks=request.checks,
        url=request.url,
        domain=request.domain,
        feedback_text=request.feedback_text,
        analyst_id=user.username,
    )
    if request.previous_scan:
        report = report.model_copy(update={"previous_scan": request.previous_scan})
    return report


@router.post("/api/report/pdf")
async def export_pdf(report: FullReport):
    pdf_bytes = build_pdf_report(report)
    filename = f"pruefbericht-{report.domain.replace('.', '-')}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/api/scan/{scan_id}")
async def get_scan(scan_id: int):
    scan = await get_scan_by_id(scan_id)
    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")
    return scan


@router.get("/api/goldlist")
async def list_goldlist():
    return {"domains": get_goldlist()}


@router.post("/api/goldlist")
async def add_goldlist_domain(request: GoldlistUpdateRequest):
    added = add_domain(request.domain)
    return {"added": added, "domains": get_goldlist()}


@router.delete("/api/goldlist")
async def delete_goldlist_domain(domain: str = Query(...)):
    removed = remove_domain(domain)
    return {"removed": removed, "domains": get_goldlist()}


@router.get("/api/history")
async def scan_history(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    verdict_filter: Optional[str] = Query(None),
    domain_search: Optional[str] = Query(None),
):
    return await get_scan_history(
        limit=limit,
        offset=offset,
        verdict_filter=verdict_filter or None,
        domain_search=domain_search or None,
    )
