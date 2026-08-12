"""Company analysis, fraud list, watchlist, HR-network APIs + pages."""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

import config
from app.database import User
from app.hr_network.bulk_scan import (
    DEFAULT_LEVEL as BULK_DEFAULT_LEVEL,
    create_bulk_scan_job,
    get_bulk_scan_job,
)
from app.hr_network.demo_fixture import (
    DemoFixtureError,
    build_demo_fraud_network,
    build_demo_hr_network,
    demo_search_hits,
    is_demo_request,
    usable_company_query,
)
from app.hr_network.fraud_network import apply_identity_confirmation, build_fraud_network
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
from app.hr_network.search_history import (
    clear_own_company_searches,
    list_company_searches,
    log_company_search,
)
from app.hr_network.company_tags import (
    TAG_UNDER_INVESTIGATION,
    clear_company_tag,
    get_company_tag,
    list_company_tags,
    set_company_tag,
)
from app.hr_network.under_investigation_watchlist import (
    enroll_under_investigation_watchlist,
)
from app.hr_network.watch_intake import ensure_seed_link, upsert_watched_person
from app.hr_network.watched_companies import (
    SOURCE_BULK_SCAN,
    SOURCE_MANUAL,
    delete_watched_companies,
    export_companies_csv,
    export_persons_csv,
    list_watched_companies,
    update_watched_company_status,
    upsert_watched_company,
)
from app.hr_network.shab_parser import _normalize_person_id
from app.hr_network.service import build_hr_network, search_companies_preview
from app.routes.deps import enforce_rate_limit, get_current_user, require_role

logger = logging.getLogger(__name__)
router = APIRouter()


class FraudNetworkIdentityOverride(BaseModel):
    """Lock or skip Moneyhouse person identity for one organ."""

    person_name: Optional[str] = None
    person_id: Optional[str] = None
    person_graph_id: Optional[str] = None
    moneyhouse_person_key: Optional[str] = None
    mh_person_key: Optional[str] = None
    person_key: Optional[str] = None
    uri: Optional[str] = None
    action: str = Field("accept", description="accept | ignore")


class FraudNetworkAnalyzeRequest(BaseModel):
    level: int = Field(2, ge=1, le=5)
    company_ids: Optional[list[str]] = None
    ad_hoc_company: Optional[dict] = None
    max_person_searches: int = Field(8, ge=0, le=20)
    force_refresh: bool = False
    identity_overrides: Optional[list[FraudNetworkIdentityOverride]] = None


class ConfirmIdentityRequest(BaseModel):
    """Confirm or ignore a Moneyhouse person candidate (prefer incremental merge)."""

    level: int = Field(3, ge=3, le=5)
    ad_hoc_company: dict = Field(default_factory=dict)
    person_name: Optional[str] = None
    person_id: Optional[str] = None
    person_graph_id: Optional[str] = None
    moneyhouse_person_key: Optional[str] = None
    action: str = Field("accept", description="accept | ignore")
    max_person_searches: int = Field(8, ge=0, le=20)
    # Retain prior accept/ignore decisions across multi-step disambiguation
    identity_overrides: Optional[list[FraudNetworkIdentityOverride]] = None
    # Client lastAnalysis / lastGraph — preferred base for partial MH merge
    base_analysis: Optional[dict] = None


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
    # Offline demo firm — no disk cache / no external APIs.
    try:
        if is_demo_request(name=n, uid=u):
            return {"levels": {}, "demo_only": True}
    except DemoFixtureError as e:
        logger.warning("Demo fixture unavailable for cache-status: %s", e)
    return cache_status_for_company(company_name=n, company_uid=u)


@router.post("/api/fraud-network/analyze")
async def api_analyze_fraud_network(body: FraudNetworkAnalyzeRequest, http_request: Request):
    enforce_rate_limit(http_request)
    ad_hoc = body.ad_hoc_company or {}
    name = (ad_hoc.get("name") or "").strip() or None
    uid = (ad_hoc.get("uid") or "").strip() or None
    try:
        if is_demo_request(name=name, uid=uid):
            return build_demo_fraud_network(level=body.level)
    except DemoFixtureError as e:
        logger.exception("Demo fraud-network fixture failed")
        raise HTTPException(status_code=503, detail=str(e)) from e
    overrides = None
    if body.identity_overrides:
        overrides = [o.model_dump(exclude_none=True) for o in body.identity_overrides]
    # Identity locks must not serve a stale auto-selected graph.
    use_cache = body.level >= 4 and not body.company_ids and not overrides
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
            identity_overrides=overrides,
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


@router.post("/api/fraud-network/confirm-identity")
async def api_confirm_identity(body: ConfirmIdentityRequest, http_request: Request):
    """Accept/ignore MH identity: merge into existing graph; never full force-refresh scan."""
    enforce_rate_limit(http_request)
    ad_hoc = body.ad_hoc_company or {}
    name = (ad_hoc.get("name") or "").strip()
    uid = (ad_hoc.get("uid") or "").strip()
    if not name and not uid:
        raise HTTPException(status_code=400, detail="Firma (name/uid) erforderlich")
    action = (body.action or "accept").strip().lower()
    if action not in ("accept", "ignore"):
        raise HTTPException(status_code=400, detail="action muss accept oder ignore sein")
    if action == "accept" and not (body.moneyhouse_person_key or "").strip():
        raise HTTPException(
            status_code=400,
            detail="moneyhouse_person_key erforderlich zum Übernehmen",
        )
    person_id = body.person_id or body.person_graph_id
    if not (body.person_name or person_id):
        raise HTTPException(status_code=400, detail="person_name oder person_id erforderlich")

    # Prefer client graph, then disk cache — partial MH attach only.
    base: dict | None = None
    base_source = None
    if isinstance(body.base_analysis, dict) and "nodes" in body.base_analysis:
        base = body.base_analysis
        base_source = "client"
    else:
        # Try requested level first, then nearby deep levels that may be cached.
        for try_level in (body.level, 5, 4, 3):
            if try_level < 3:
                continue
            hit, key = load_cached_for_company(
                level=try_level, company_name=name or None, company_uid=uid or None
            )
            if hit is not None:
                base = hit
                base_source = f"cache_L{try_level}"
                break

    try:
        if base is not None:
            result = await apply_identity_confirmation(
                base=base,
                level=body.level,
                person_name=body.person_name,
                person_id=person_id,
                moneyhouse_person_key=(body.moneyhouse_person_key or "").strip() or None,
                action=action,
            )
            out = dict(result) if isinstance(result, dict) else result
            if isinstance(out, dict):
                out["cached"] = False
                # Preserve original cache timestamp for UI ("based on cache") if any
                if base_source and str(base_source).startswith("cache"):
                    out["base_cached"] = True
                    out["cached_at"] = base.get("cached_at") or out.get("cached_at")
                else:
                    out["base_cached"] = base_source == "client" and bool(
                        base.get("cached")
                    )
                    if base.get("cached_at"):
                        out["cached_at"] = base.get("cached_at")
                out["identity_confirmed"] = action == "accept"
                out["identity_action"] = action
                out["incremental_identity"] = True
                out["identity_base_source"] = base_source
            return out

        # No graph in hand: last-resort full rebuild with identity locks.
        # (Does not use force_refresh; shared intermediate caches still apply inside build.)
        logger.info(
            "confirm-identity full rebuild fallback for %r / %r (no client graph/cache)",
            name,
            uid,
        )
        overrides: list[dict] = []
        if body.identity_overrides:
            overrides.extend(
                o.model_dump(exclude_none=True) for o in body.identity_overrides
            )
        overrides.append(
            {
                "action": action,
                "person_name": body.person_name,
                "person_id": body.person_id,
                "person_graph_id": body.person_graph_id,
                "moneyhouse_person_key": (body.moneyhouse_person_key or "").strip()
                or None,
            }
        )
        result = await build_fraud_network(
            level=body.level,
            ad_hoc_company={"name": name or None, "uid": uid or None},
            max_person_searches=body.max_person_searches,
            identity_overrides=overrides,
        )
        out = dict(result) if isinstance(result, dict) else result
        if isinstance(out, dict):
            out["cached"] = False
            out["cached_at"] = None
            out["identity_confirmed"] = action == "accept"
            out["identity_action"] = action
            out["incremental_identity"] = False
            out["identity_base_source"] = "full_rebuild"
        return out
    except PermissionError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("Identity confirm failed")
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


@router.get("/api/watched-persons/export.csv")
async def api_export_watched_persons_csv(
    status: Optional[str] = Query("active,confirmed_fraud"),
    _user: User = Depends(get_current_user),
):
    data = await list_watched_persons(status=status, limit=500, offset=0)
    csv_text = export_persons_csv(data.get("items") or [])
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="watchlist-personen.csv"'},
    )


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
    demo: str = Query(""),
    user: User = Depends(get_current_user),
):
    enforce_rate_limit(http_request)
    company_q = company.strip()
    uid_q = uid.strip()
    demo_q = demo.strip()
    if not company_q and not uid_q and not demo_q:
        raise HTTPException(status_code=400, detail="company or uid required")
    if not demo_q and not usable_company_query(company=company_q, uid=uid_q):
        raise HTTPException(
            status_code=400,
            detail="Ungültiger Firmenname — mindestens zwei Buchstaben/Ziffern oder eine UID angeben",
        )
    try:
        if is_demo_request(name=company_q or None, uid=uid_q or None, demo=demo_q or None):
            result = build_demo_hr_network(
                company=company_q or None,
                uid=uid_q or None,
            )
            firm = (result or {}).get("company") if isinstance(result, dict) else None
            await log_company_search(
                company_name=(firm or {}).get("name") or company_q or "DEMO-FRAUD GmbH",
                company_uid=(firm or {}).get("uid") or uid_q or "CHE-000.000.001",
                searched_by=user.display_name or user.username or "Team",
                searched_by_username=user.username,
            )
            return result
    except DemoFixtureError as e:
        logger.exception("Demo HR-network fixture failed")
        raise HTTPException(status_code=503, detail=str(e)) from e
    try:
        result = await build_hr_network(
            company=company_q or None,
            uid=uid_q or None,
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

    firm = (result or {}).get("company") if isinstance(result, dict) else None
    if isinstance(firm, dict):
        await log_company_search(
            company_name=firm.get("name") or company_q,
            company_uid=firm.get("uid") or uid_q,
            searched_by=user.display_name or user.username or "Team",
            searched_by_username=user.username,
        )
    else:
        await log_company_search(
            company_name=company_q,
            company_uid=uid_q,
            searched_by=user.display_name or user.username or "Team",
            searched_by_username=user.username,
        )
    return result


@router.get("/api/hr-network/search-history")
async def api_search_history(
    limit: int = Query(15, ge=1, le=50),
    _user: User = Depends(get_current_user),
):
    """Team-wide recent Firmenanalyse queries for the idle start page."""
    return {"items": await list_company_searches(limit=limit)}


@router.delete("/api/hr-network/search-history")
async def api_clear_own_search_history(user: User = Depends(get_current_user)):
    """Remove only the current user's entries from the shared team history."""
    deleted = await clear_own_company_searches(user.username)
    return {"deleted": deleted}


class SetCompanyTagBody(BaseModel):
    company_name: str = Field("", max_length=512)
    company_uid: Optional[str] = Field(None, max_length=32)
    tag: str = Field(TAG_UNDER_INVESTIGATION, max_length=64)
    # Optional context from Firmenanalyse lastAnalysis (preferred over L2 re-fetch)
    address: Optional[str] = Field(None, max_length=1024)
    legal_seat: Optional[str] = Field(None, max_length=255)
    company_ehraid: Optional[int] = None
    persons: Optional[list[dict[str, Any]]] = None


class BulkScanCreateBody(BaseModel):
    names: list[str] = Field(default_factory=list, max_length=80)
    text: str = Field("", max_length=50_000)
    level: int = Field(BULK_DEFAULT_LEVEL, ge=1, le=5)
    max_person_searches: int = Field(4, ge=0, le=12)


class WatchlistBulkAddEntry(BaseModel):
    type: str = Field(..., description="company | person")
    company_name: Optional[str] = Field(None, max_length=512)
    company_uid: Optional[str] = Field(None, max_length=32)
    company_ehraid: Optional[int] = None
    address: Optional[str] = Field(None, max_length=1024)
    legal_seat: Optional[str] = Field(None, max_length=255)
    display_name: Optional[str] = Field(None, max_length=255)
    residence: Optional[str] = Field(None, max_length=255)
    source_company_name: Optional[str] = Field(None, max_length=512)
    source_company_uid: Optional[str] = Field(None, max_length=32)
    role: Optional[str] = Field(None, max_length=255)


class WatchlistBulkAddBody(BaseModel):
    entries: list[WatchlistBulkAddEntry] = Field(..., min_length=1, max_length=200)
    source_reason: str = Field(SOURCE_BULK_SCAN, max_length=64)


class WatchedCompanyStatusBody(BaseModel):
    status: str = Field(..., pattern="^(active|cleared)$")


class WatchedCompanyDeleteBody(BaseModel):
    ids: list[int] = Field(..., min_length=1, max_length=100)


@router.get("/api/company-tags")
async def api_list_company_tags(
    tag: Optional[str] = Query(None),
    _user: User = Depends(get_current_user),
):
    """Team-visible firm tags (MVP: «In Abklärung»)."""
    return {"tags": await list_company_tags(tag=tag)}


@router.get("/api/company-tags/lookup")
async def api_lookup_company_tag(
    uid: Optional[str] = None,
    name: Optional[str] = None,
    tag: str = Query(TAG_UNDER_INVESTIGATION),
    _user: User = Depends(get_current_user),
):
    hit = await get_company_tag(uid=uid, name=name, tag=tag)
    return {"tag": hit}


@router.post("/api/company-tags")
async def api_set_company_tag(
    body: SetCompanyTagBody,
    user: User = Depends(get_current_user),
):
    try:
        row = await set_company_tag(
            company_name=body.company_name,
            company_uid=body.company_uid,
            tag=body.tag or TAG_UNDER_INVESTIGATION,
            set_by=user.display_name or user.username or "Team",
            set_by_username=user.username,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    watchlist_side: dict[str, Any] | None = None
    tag_k = (body.tag or TAG_UNDER_INVESTIGATION).strip()
    if tag_k == TAG_UNDER_INVESTIGATION:
        try:
            watchlist_side = await enroll_under_investigation_watchlist(
                company_name=body.company_name,
                company_uid=body.company_uid,
                added_by=user.display_name or user.username or "Team",
                address=body.address,
                legal_seat=body.legal_seat,
                company_ehraid=body.company_ehraid,
                persons=body.persons,
            )
        except Exception:
            logger.exception("Watchlist enroll after In Abklärung failed")
            watchlist_side = {"error": "Watchlist-Aufnahme teilweise fehlgeschlagen"}
    return {"tag": row, "watchlist": watchlist_side}


@router.delete("/api/company-tags")
async def api_clear_company_tag(
    uid: Optional[str] = None,
    name: Optional[str] = None,
    tag: str = Query(TAG_UNDER_INVESTIGATION),
    _user: User = Depends(get_current_user),
):
    # Watchlist entries are intentionally kept when the tag is cleared.
    cleared = await clear_company_tag(uid=uid, name=name, tag=tag)
    return {"cleared": cleared, "watchlist_unchanged": True}


# ── Firmen-Watchlist ─────────────────────────────────────────────────────


@router.get("/api/watched-companies")
async def api_list_watched_companies(
    status: Optional[str] = Query("active"),
    q: Optional[str] = None,
    source_reason: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _user: User = Depends(get_current_user),
):
    return await list_watched_companies(
        status=status,
        q=q,
        source_reason=source_reason,
        limit=limit,
        offset=offset,
    )


@router.get("/api/watched-companies/export.csv")
async def api_export_watched_companies_csv(
    status: Optional[str] = Query("active"),
    _user: User = Depends(get_current_user),
):
    data = await list_watched_companies(status=status, limit=500, offset=0)
    csv_text = export_companies_csv(data.get("items") or [])
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="watchlist-firmen.csv"'},
    )


@router.patch("/api/watched-companies/{company_id}/status")
async def api_watched_company_status(
    company_id: int,
    body: WatchedCompanyStatusBody,
    _user: User = Depends(require_role("case_manager", "admin", "compliance")),
):
    try:
        return await update_watched_company_status(company_id, status=body.status)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/api/watched-companies/delete")
async def api_delete_watched_companies(
    body: WatchedCompanyDeleteBody,
    _user: User = Depends(require_role("case_manager", "admin")),
):
    return await delete_watched_companies(body.ids)


@router.post("/api/watchlist/bulk-add")
async def api_watchlist_bulk_add(
    body: WatchlistBulkAddBody,
    user: User = Depends(require_role("admin")),
):
    """Auswahl aus Bulk-Scan → Firmen- und Personen-Watchlist."""
    reason = (body.source_reason or SOURCE_BULK_SCAN).strip() or SOURCE_BULK_SCAN
    by = user.display_name or user.username or "Admin"
    companies: list[dict[str, Any]] = []
    persons: list[dict[str, Any]] = []
    for entry in body.entries:
        kind = (entry.type or "").strip().lower()
        if kind == "company":
            try:
                row = await upsert_watched_company(
                    company_name=entry.company_name,
                    company_uid=entry.company_uid,
                    company_ehraid=entry.company_ehraid,
                    address=entry.address,
                    legal_seat=entry.legal_seat,
                    source_reason=reason,
                    added_by=by,
                )
                companies.append(row)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e)) from e
        elif kind == "person":
            display = (entry.display_name or "").strip()
            if not display:
                raise HTTPException(status_code=400, detail="display_name für Person erforderlich")
            slug = _normalize_person_id(display)
            wp = await upsert_watched_person(
                person_slug=slug,
                display_name=display,
                residence=entry.residence,
                source_company_ehraid=entry.company_ehraid,
                source_company_name=entry.source_company_name or entry.company_name,
                source_reason=reason,
                status="active",
                notes="Bulk-Scan Auswahl",
            )
            seed_name = (entry.source_company_name or entry.company_name or "").strip()
            if seed_name:
                await ensure_seed_link(
                    person_id=wp.id,
                    company_ehraid=entry.company_ehraid,
                    company_name=seed_name,
                    company_uid=entry.source_company_uid or entry.company_uid,
                    role=entry.role,
                )
            persons.append(
                {
                    "id": wp.id,
                    "display_name": wp.display_name,
                    "person_slug": wp.person_slug,
                }
            )
        else:
            raise HTTPException(status_code=400, detail=f"Unbekannter Typ: {entry.type}")
    return {
        "companies_added": len(companies),
        "persons_added": len(persons),
        "companies": companies,
        "persons": persons,
        "source_reason": reason or SOURCE_MANUAL,
    }


# ── Bulk-Scan (Admin) ────────────────────────────────────────────────────


@router.post("/api/bulk-scan")
async def api_create_bulk_scan(
    body: BulkScanCreateBody,
    user: User = Depends(require_role("admin")),
):
    names = list(body.names or [])
    if body.text.strip():
        names.extend(body.text.splitlines())
    try:
        job = await create_bulk_scan_job(
            names=names,
            level=body.level,
            created_by=user.display_name or user.username or "Admin",
            created_by_username=user.username,
            max_person_searches=body.max_person_searches,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"job": job}


@router.get("/api/bulk-scan/{job_id}")
async def api_get_bulk_scan(
    job_id: int,
    _user: User = Depends(require_role("admin")),
):
    job = await get_bulk_scan_job(job_id, include_items=True)
    if not job:
        raise HTTPException(status_code=404, detail="Bulk-Scan-Job nicht gefunden")
    return {"job": job}


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
        demo_hits = demo_search_hits(q, limit=limit)
    except DemoFixtureError as e:
        logger.warning("Demo search fixture unavailable: %s", e)
        demo_hits = []
    try:
        live = await search_companies_preview(q, limit=limit)
    except PermissionError as e:
        # Offline / missing Zefix — still serve demo fixture if it matches.
        if demo_hits:
            return {"results": demo_hits}
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        if demo_hits:
            return {"results": demo_hits}
        raise HTTPException(status_code=502, detail=str(e)[:120]) from e
    # Prefer demo hit first when query matches (clearly marked offline).
    if demo_hits:
        seen = {
            (r.get("uid") or "", (r.get("name") or "").lower())
            for r in demo_hits
        }
        merged = list(demo_hits)
        for r in live or []:
            key = (r.get("uid") or "", (r.get("name") or "").lower())
            if key in seen:
                continue
            merged.append(r)
            if len(merged) >= limit:
                break
        return {"results": merged[:limit]}
    return {"results": live}
