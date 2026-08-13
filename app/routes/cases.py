"""CompanyCase wizard APIs + case detail page."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from app.case_report import build_bank_lookup_sheet
from app.database import User
from app.hr_network.company_cases import (
    FRAUD_TYPES,
    action_reported_case,
    add_bank_check_item,
    add_journal_entry,
    branch_signal,
    clear_case,
    close_documented_case,
    confirm_fraud,
    delete_company_case,
    enroll_former_officers_for_case,
    find_open_case_for_company,
    generate_case_report,
    get_case_report_path,
    get_company_case,
    list_company_cases,
    mark_case_suspicious,
    open_case,
    open_case_from_alert,
    update_bank_check,
    update_hit_context,
    update_payment_flags,
)
from app.routes.deps import enforce_rate_limit, get_current_user, require_role

router = APIRouter(dependencies=[Depends(enforce_rate_limit)])


class OpenCaseBody(BaseModel):
    company_name: str = Field(..., min_length=1, max_length=512)
    company_uid: Optional[str] = None
    company_ehraid: Optional[int] = None
    company_purpose: Optional[str] = None


class ConfirmBody(BaseModel):
    fraud_type: str


class ClearBody(BaseModel):
    note: str = Field("", max_length=2000)


class MarkSuspiciousBody(BaseModel):
    note: str = Field("", max_length=2000)


class JournalBody(BaseModel):
    text: str = Field(..., min_length=3, max_length=4000)


class PaymentBody(BaseModel):
    payment_blocked: Optional[bool] = None
    payment_blocked_note: Optional[str] = Field(None, max_length=512)


class HitContextBody(BaseModel):
    hit_amount: Optional[float] = None
    hit_currency: Optional[str] = Field(None, max_length=3)
    hit_reference: Optional[str] = Field(None, max_length=256)
    hit_note: Optional[str] = Field(None, max_length=1024)


class BankCheckBody(BaseModel):
    status: str = Field(..., pattern="^(no_relationship|relationship_found)$")
    note: Optional[str] = Field(None, max_length=512)


class AddBankCheckBody(BaseModel):
    entity_type: str = Field(..., pattern="^(company|person)$")
    entity_label: str = Field(..., min_length=2, max_length=512)
    entity_ref: Optional[str] = Field(None, max_length=128)


class ActionBody(BaseModel):
    compliance_note: str = Field(..., min_length=3, max_length=1024)


class CloseCaseBody(BaseModel):
    note: str = Field("", max_length=2000)


@router.get("/cases")
async def cases_list_page(_user: User = Depends(get_current_user)):
    return FileResponse("static/cases.html")


@router.get("/cases/{case_id}")
async def case_detail_page(case_id: int, _user: User = Depends(get_current_user)):
    return FileResponse("static/case.html")


@router.get("/api/company-cases")
async def api_list_cases(
    status: Optional[str] = Query(None),
    _user: User = Depends(get_current_user),
):
    return {"cases": await list_company_cases(status=status)}


@router.get("/api/company-cases/branch-signal")
async def api_branch_signal(
    months: int = Query(6, ge=1, le=36),
    _user: User = Depends(get_current_user),
):
    return await branch_signal(months=months)


@router.get("/api/company-cases/lookup")
async def api_lookup_case(
    uid: Optional[str] = None,
    ehraid: Optional[int] = None,
    name: Optional[str] = None,
    _user: User = Depends(get_current_user),
):
    hit = await find_open_case_for_company(uid=uid, ehraid=ehraid, name=name)
    return {"case": hit}


@router.get("/api/company-cases/{case_id}")
async def api_get_case(case_id: int, _user: User = Depends(get_current_user)):
    try:
        return await get_company_case(case_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.delete("/api/company-cases/{case_id}")
async def api_delete_case(case_id: int, _user: User = Depends(require_role("case_manager", "admin"))):
    try:
        return await delete_company_case(case_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/api/company-cases")
async def api_open_case(body: OpenCaseBody, user: User = Depends(require_role("case_manager", "admin"))):
    try:
        return await open_case(
            company_name=body.company_name,
            company_uid=body.company_uid,
            company_ehraid=body.company_ehraid,
            company_purpose=body.company_purpose,
            opened_by=user.username,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/api/company-cases/from-alert/{alert_id}")
async def api_from_alert(alert_id: int, user: User = Depends(require_role("case_manager", "admin"))):
    try:
        return await open_case_from_alert(alert_id, opened_by=user.username)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/api/company-cases/{case_id}/confirm")
async def api_confirm(
    case_id: int,
    body: ConfirmBody,
    user: User = Depends(require_role("case_manager", "admin")),
):
    if body.fraud_type not in FRAUD_TYPES:
        raise HTTPException(status_code=400, detail=f"fraud_type: {FRAUD_TYPES}")
    try:
        return await confirm_fraud(case_id, fraud_type=body.fraud_type, by=user.username)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/api/company-cases/{case_id}/clear")
async def api_clear(
    case_id: int,
    body: ClearBody,
    user: User = Depends(require_role("case_manager", "admin")),
):
    try:
        return await clear_case(case_id, by=user.username, note=body.note)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/api/company-cases/{case_id}/mark-suspicious")
async def api_mark_suspicious(
    case_id: int,
    body: MarkSuspiciousBody,
    user: User = Depends(require_role("case_manager", "admin")),
):
    """Tag «In Abklärung» + Watchlist, Akte schliessen."""
    try:
        return await mark_case_suspicious(
            case_id,
            by=user.username,
            note=body.note,
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/api/company-cases/{case_id}/enroll-former")
async def api_enroll_former(
    case_id: int,
    user: User = Depends(require_role("case_manager", "admin")),
):
    """Ehemalige Organe auf Watchlist + Checkliste (nach Bestätigung)."""
    try:
        return await enroll_former_officers_for_case(case_id, by=user.username)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/api/company-cases/{case_id}/journal")
async def api_journal(
    case_id: int,
    body: JournalBody,
    user: User = Depends(get_current_user),
):
    try:
        return await add_journal_entry(case_id, author=user.username, text=body.text)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.patch("/api/company-cases/{case_id}/payment")
async def api_payment(
    case_id: int,
    body: PaymentBody,
    user: User = Depends(require_role("case_manager", "admin")),
):
    try:
        return await update_payment_flags(
            case_id,
            payment_blocked=body.payment_blocked,
            payment_blocked_note=body.payment_blocked_note,
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.patch("/api/company-cases/{case_id}/hit-context")
async def api_hit_context(
    case_id: int,
    body: HitContextBody,
    _user: User = Depends(require_role("case_manager", "admin")),
):
    try:
        return await update_hit_context(
            case_id,
            hit_amount=body.hit_amount,
            hit_currency=body.hit_currency,
            hit_reference=body.hit_reference,
            hit_note=body.hit_note,
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.patch("/api/company-cases/{case_id}/bank-checks/{item_id}")
async def api_bank_check(
    case_id: int,
    item_id: int,
    body: BankCheckBody,
    user: User = Depends(require_role("case_manager", "admin")),
):
    try:
        return await update_bank_check(
            case_id,
            item_id,
            status=body.status,
            note=body.note,
            checked_by=user.username,
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/api/company-cases/{case_id}/bank-checks")
async def api_add_bank_check(
    case_id: int,
    body: AddBankCheckBody,
    _user: User = Depends(require_role("case_manager", "admin")),
):
    try:
        return await add_bank_check_item(
            case_id,
            entity_type=body.entity_type,
            entity_label=body.entity_label,
            entity_ref=body.entity_ref,
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/api/company-cases/{case_id}/lookup-sheet")
async def api_lookup_sheet(case_id: int, _user: User = Depends(get_current_user)):
    """PDF with company + checklist names for core-banking relationship checks."""
    try:
        case = await get_company_case(case_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    pdf = build_bank_lookup_sheet(case)
    filename = f"abgleich_akte_{case_id}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/api/company-cases/{case_id}/report")
async def api_report(case_id: int, user: User = Depends(require_role("case_manager", "admin"))):
    try:
        return await generate_case_report(case_id, by=user.username)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/api/company-cases/{case_id}/report")
async def api_download_report(case_id: int, _user: User = Depends(get_current_user)):
    try:
        path, _case = await get_case_report_path(case_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"case_{case_id}.pdf",
    )


@router.post("/api/company-cases/{case_id}/close")
async def api_close_documented(
    case_id: int,
    body: CloseCaseBody,
    user: User = Depends(require_role("case_manager", "admin")),
):
    """Interner Abschluss nach Dokumentation (ohne Reporting/Compliance)."""
    try:
        return await close_documented_case(
            case_id, by=user.username, note=body.note or ""
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/api/company-cases/{case_id}/action")
async def api_action(
    case_id: int,
    body: ActionBody,
    user: User = Depends(require_role("case_manager", "compliance", "admin")),
):
    try:
        return await action_reported_case(
            case_id, note=body.compliance_note, actioned_by=user.username
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
