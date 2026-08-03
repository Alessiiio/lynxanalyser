"""Company analysis, fraud list, watchlist, HR-network APIs + pages."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

import config
from app.database import User
from app.hr_network.fraud_network import build_fraud_network
from app.hr_network.fraud_network_cache import (
    cache_status_for_company,
    cached_at_iso,
    load_cached_for_company,
    store_cached_for_company,
)
from app.hr_network.person_monitoring import (
    acknowledge_alert,
    add_watched_person_manual,
    delete_watched_persons,
    get_watched_person_dossier,
    list_network_alerts,
    list_status_history,
    list_watched_person_cases,
    list_watched_persons,
    list_watchlist_inbox,
    merge_watched_persons,
    run_person_monitoring,
    scan_watched_person_incremental,
    update_watched_person_case_notes,
    update_watched_person_flags,
    update_watched_person_status,
)
from app.investigation_dossier import build_investigation_dossier_pdf
from app.profiler_export import build_profiler_screening_pdf
from app.hr_network.person_search import search_person_in_sogc
from app.hr_network.service import build_hr_network, search_companies_preview
from app.routes.deps import enforce_rate_limit, get_current_user, require_role

logger = logging.getLogger(__name__)
router = APIRouter()


class FraudNetworkAnalyzeRequest(BaseModel):
    level: int = Field(2, ge=1, le=5)
    company_ids: Optional[list[str]] = None
    ad_hoc_company: Optional[dict] = None
    max_person_searches: int = Field(8, ge=0, le=20)
    force_refresh: bool = False


class WatchedPersonAddRequest(BaseModel):
    display_name: str
    residence: Optional[str] = None
    notes: Optional[str] = None


class WatchedPersonStatusRequest(BaseModel):
    status: str
    reason: str = Field(..., min_length=3, max_length=1024)


class WatchedPersonMergeRequest(BaseModel):
    canonical_id: int
    duplicate_id: int
    reason: str = Field("Merge: dieselbe Person", min_length=3, max_length=1024)


class CaseNotesRequest(BaseModel):
    case_notes: str = Field("", max_length=4000)


class WatchedPersonFlagsRequest(BaseModel):
    flag_undesired_customer: Optional[bool] = None
    flag_aml: Optional[bool] = None


class WatchedPersonDeleteRequest(BaseModel):
    ids: list[int] = Field(..., min_length=1, max_length=100)


class ProfilerScreeningPdfBody(BaseModel):
    seed_name: str = ""
    seed_uid: str = ""
    companies: list[str] = Field(default_factory=list, max_length=200)
    persons: list[str] = Field(default_factory=list, max_length=200)


@router.get("/")
async def company_analysis_page():
    return FileResponse("static/company-analysis.html")


@router.get("/watchlist")
async def watchlist_page():
    return FileResponse("static/watchlist.html")


@router.get("/profiler-cases")
async def profiler_cases_page(_user: User = Depends(require_role("admin"))):
    """Admin-only: list of open Profiler snips (client-side localStorage UI)."""
    return FileResponse("static/profiler-cases.html")


@router.get("/profiler")
async def profiler_page(_user: User = Depends(require_role("admin"))):
    """Admin Profiler fall-cockpit (full page)."""
    return FileResponse("static/profiler-page.html")


@router.post("/api/profiler/screening-pdf")
async def api_profiler_screening_pdf(
    body: ProfilerScreeningPdfBody,
    _user: User = Depends(require_role("admin")),
):
    """Name list PDF for core-banking paste / screening (no bank account secrets)."""
    pdf = build_profiler_screening_pdf(
        seed_name=body.seed_name.strip(),
        seed_uid=body.seed_uid.strip(),
        companies=body.companies,
        persons=body.persons,
        prepared_by=_user.display_name or _user.username or "",
    )
    slug = "".join(ch if ch.isalnum() else "-" for ch in (body.seed_name or "fall"))[:40].strip("-") or "fall"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="profiler-screening-{slug}.pdf"'},
    )


@router.get("/hr-network")
async def hr_network_redirect():
    return RedirectResponse(url="/", status_code=302)


@router.get("/fraud-network")
async def fraud_network_redirect():
    return RedirectResponse(url="/cases", status_code=302)


@router.get("/api/fraud-network/cache-status")
async def api_fraud_network_cache_status(
    name: str = Query(""),
    uid: str = Query(""),
    _user: User = Depends(get_current_user),
):
    """L4/L5 disk-cache presence for a firm (shared across users, 7 days)."""
    n = name.strip() or None
    u = uid.strip() or None
    if not n and not u:
        raise HTTPException(status_code=400, detail="name or uid required")
    return cache_status_for_company(company_name=n, company_uid=u)


@router.post("/api/fraud-network/analyze")
async def api_analyze_fraud_network(body: FraudNetworkAnalyzeRequest, http_request: Request):
    enforce_rate_limit(http_request)
    ad_hoc = body.ad_hoc_company or {}
    name = (ad_hoc.get("name") or "").strip() or None
    uid = (ad_hoc.get("uid") or "").strip() or None
    use_cache = body.level >= 4 and not body.company_ids
    if use_cache and (name or uid) and not body.force_refresh:
        hit, key = load_cached_for_company(
            level=body.level, company_name=name, company_uid=uid
        )
        if hit is not None and key:
            out = dict(hit)
            out["cached"] = True
            out["cached_at"] = cached_at_iso(key)
            out["level"] = body.level
            return out
    try:
        result = await build_fraud_network(
            level=body.level,
            company_ids=body.company_ids,
            ad_hoc_company=body.ad_hoc_company,
            max_person_searches=body.max_person_searches,
        )
        if use_cache and (name or uid) and isinstance(result, dict):
            store_cached_for_company(
                level=body.level,
                company_name=name,
                company_uid=uid,
                payload=result,
            )
        out = dict(result) if isinstance(result, dict) else result
        if isinstance(out, dict):
            out["cached"] = False
            out["cached_at"] = None
        return out
    except PermissionError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("Fraud network analysis failed")
        raise HTTPException(status_code=502, detail=str(e)[:160]) from e


@router.get("/api/watched-persons")
async def api_list_watched_persons(
    status: Optional[str] = Query(None, description="Comma-separated; default active,confirmed_fraud"),
    q: Optional[str] = None,
    source_reason: Optional[str] = None,
    has_open_alert: Optional[bool] = None,
    sort: str = Query("priority", pattern="^(priority|added_at)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    result = await list_watched_persons(
        status=status,
        q=q,
        source_reason=source_reason,
        has_open_alert=has_open_alert,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    # Backward-compatible alias
    result["persons"] = result["items"]
    return result


@router.get("/api/watched-persons/cases")
async def api_watched_person_cases(
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return await list_watched_person_cases(status=status, limit=limit, offset=offset)


@router.get("/api/watchlist/inbox")
async def api_watchlist_inbox(limit: int = Query(100, ge=1, le=300)):
    return await list_watchlist_inbox(limit=limit)


@router.post("/api/watched-persons")
async def api_add_watched_person(body: WatchedPersonAddRequest):
    try:
        return await add_watched_person_manual(
            display_name=body.display_name,
            residence=body.residence,
            notes=body.notes,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/api/watched-persons/{person_id}/scan")
async def api_scan_watched_person(
    person_id: int,
    http_request: Request,
    canton: str = Query("", description="Optional — only used if include_shab=1"),
    include_shab: bool = Query(
        False,
        description="Optional slow SHAB supplement. Default: Moneyhouse person search + Zefix firm resolve.",
    ),
):
    enforce_rate_limit(http_request)
    try:
        return await scan_watched_person_incremental(
            person_id,
            canton=canton.strip().upper() or None,
            include_shab=include_shab,
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        logger.exception("Manual person scan failed")
        raise HTTPException(status_code=502, detail=str(e)[:160]) from e


@router.get("/api/watched-persons/{person_id}")
async def api_watched_person_dossier(person_id: int, _user: User = Depends(get_current_user)):
    try:
        return await get_watched_person_dossier(person_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.patch("/api/watched-persons/{person_id}/case-notes")
async def api_update_case_notes(
    person_id: int,
    body: CaseNotesRequest,
    user: User = Depends(get_current_user),
):
    try:
        return await update_watched_person_case_notes(person_id, body.case_notes)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.patch("/api/watched-persons/{person_id}/flags")
async def api_update_watched_flags(
    person_id: int,
    body: WatchedPersonFlagsRequest,
    _user: User = Depends(require_role("case_manager", "admin", "compliance")),
):
    if body.flag_undesired_customer is None and body.flag_aml is None:
        raise HTTPException(status_code=400, detail="Mindestens ein Flag setzen")
    try:
        return await update_watched_person_flags(
            person_id,
            flag_undesired_customer=body.flag_undesired_customer,
            flag_aml=body.flag_aml,
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/api/watched-persons/{person_id}/investigation-report")
async def api_investigation_report(
    person_id: int,
    user: User = Depends(get_current_user),
):
    try:
        dossier = await get_watched_person_dossier(person_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    pdf = build_investigation_dossier_pdf(
        dossier,
        case_note=dossier.get("case_notes") or "",
        prepared_by=user.username,
    )
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="dossier_person_{person_id}.pdf"'
        },
    )


@router.patch("/api/watched-persons/{person_id}/status")
async def api_update_watched_status(
    person_id: int,
    body: WatchedPersonStatusRequest,
    user: User = Depends(get_current_user),
):
    try:
        return await update_watched_person_status(
            person_id,
            body.status,
            reason=body.reason,
            changed_by=user.username,
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/api/watched-persons/{person_id}/status-history")
async def api_status_history(person_id: int, _user: User = Depends(get_current_user)):
    return {"history": await list_status_history(person_id)}


@router.post("/api/watched-persons/merge")
async def api_merge_persons(body: WatchedPersonMergeRequest, user: User = Depends(get_current_user)):
    try:
        return await merge_watched_persons(
            canonical_id=body.canonical_id,
            duplicate_id=body.duplicate_id,
            changed_by=user.username,
            reason=body.reason,
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/api/watched-persons/delete")
async def api_delete_persons(
    body: WatchedPersonDeleteRequest,
    _user: User = Depends(require_role("case_manager", "admin")),
):
    try:
        return await delete_watched_persons(body.ids)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.delete("/api/watched-persons/{person_id}")
async def api_delete_person(
    person_id: int,
    _user: User = Depends(require_role("case_manager", "admin")),
):
    try:
        return await delete_watched_persons([person_id])
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/api/network-alerts")
async def api_network_alerts(
    acknowledged: Optional[bool] = False,
    severity: Optional[str] = None,
    since: Optional[str] = None,
    person_id: Optional[int] = None,
):
    return {
        "alerts": await list_network_alerts(
            acknowledged=acknowledged,
            severity=severity,
            since=since,
            person_id=person_id,
        )
    }


@router.post("/api/network-alerts/{alert_id}/ack")
async def api_ack_network_alert(alert_id: int, user: User = Depends(get_current_user)):
    try:
        return await acknowledge_alert(alert_id, by=user.username)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/api/watched-persons/run-monitoring")
async def api_run_monitoring(http_request: Request, limit: int = Query(10, ge=1, le=50)):
    enforce_rate_limit(http_request)
    return await run_person_monitoring(limit=limit)


@router.get("/api/hr-network")
async def api_hr_network(
    http_request: Request,
    company: str = Query(""),
    uid: str = Query(""),
):
    enforce_rate_limit(http_request)
    if not company.strip() and not uid.strip():
        raise HTTPException(status_code=400, detail="company or uid required")
    try:
        return await build_hr_network(
            company=company.strip() or None,
            uid=uid.strip() or None,
        )
    except PermissionError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("HR network lookup failed")
        raise HTTPException(status_code=502, detail=f"Zefix lookup failed: {str(e)[:120]}") from e


@router.get("/api/hr-network/person-search")
async def api_hr_network_person_search(
    http_request: Request,
    name: str = Query(..., min_length=3),
    exclude_uid: str = Query(""),
    registry_office_id: Optional[int] = Query(None),
    canton: str = Query(""),
    deep: bool = Query(False),
):
    enforce_rate_limit(http_request)
    if not config.ZEFIX_USERNAME or not config.ZEFIX_PASSWORD:
        raise HTTPException(status_code=503, detail="Zefix-Zugangsdaten fehlen")
    try:
        reg_id = registry_office_id
        canton_code = canton.strip().upper() or None
        # Prefer cantonal SHAB scope (same as fraud-network L3).
        if reg_id is None and not canton_code and exclude_uid.strip():
            try:
                from app.hr_network.zefix_resolve import resolve_company_detail

                detail = await resolve_company_detail(None, exclude_uid.strip())
                reg_id = detail.get("registryOfCommerceId")
                raw_canton = detail.get("canton")
                if isinstance(raw_canton, dict):
                    canton_code = (raw_canton.get("id") or raw_canton.get("shortName") or "").strip().upper() or None
                elif raw_canton:
                    canton_code = str(raw_canton).strip().upper() or None
            except Exception:
                logger.debug("Could not resolve registry for exclude_uid=%s", exclude_uid, exc_info=True)

        return await search_person_in_sogc(
            name.strip(),
            exclude_uid=exclude_uid.strip() or None,
            registry_office_id=reg_id,
            canton=canton_code,
            deep=deep,
            max_seconds=120.0 if deep else 80.0,
            years_back=20 if deep else 12,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("HR person search failed")
        raise HTTPException(status_code=502, detail=str(e)[:120]) from e


@router.get("/api/hr-network/search")
async def api_hr_network_search(
    http_request: Request,
    q: str = Query(..., min_length=2),
    limit: int = Query(12, ge=1, le=25),
):
    enforce_rate_limit(http_request)
    try:
        return {"results": await search_companies_preview(q, limit=limit)}
    except PermissionError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)[:120]) from e
