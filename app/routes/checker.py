"""Website checker APIs and page."""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, StreamingResponse

from app.checker import run_all_checks, stream_checks
from app.models import CheckRequest, FullReport
from app.routes.deps import enforce_rate_limit
from app.transaction_context import parse_transaction_context

router = APIRouter()


@router.get("/website-check")
async def website_check_page():
    return FileResponse("static/website-check.html")


@router.post("/api/check", response_model=FullReport)
async def check_website(request: CheckRequest, http_request: Request):
    enforce_rate_limit(http_request)
    transaction = parse_transaction_context(
        request.transaction_amount,
        request.transaction_currency,
        request.transaction_purpose,
    )
    return await run_all_checks(request.url, request.company or None, transaction)


@router.get("/api/stream")
async def stream_website(
    http_request: Request,
    url: str = Query(...),
    company: str = Query(""),
    transaction_amount: Optional[float] = Query(None),
    transaction_currency: Optional[str] = Query(None),
    transaction_purpose: Optional[str] = Query(None),
):
    enforce_rate_limit(http_request)
    transaction = parse_transaction_context(
        transaction_amount,
        transaction_currency,
        transaction_purpose,
    )

    async def event_generator():
        try:
            async for event_type, payload in stream_checks(url, company or None, transaction):
                if event_type == "check":
                    data = json.dumps({"type": "check", "result": payload.model_dump()})
                elif event_type == "report":
                    data = json.dumps({"type": "report", "report": payload.model_dump()})
                elif event_type == "retry":
                    data = json.dumps({"type": "retry", **payload})
                elif event_type == "goldlist":
                    data = json.dumps({"type": "goldlist", **payload})
                elif event_type == "blocklist":
                    data = json.dumps({"type": "blocklist", **payload})
                elif event_type == "thought":
                    data = json.dumps({"type": "thought", **payload})
                else:
                    continue
                yield f"data: {data}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/api/compare")
async def compare_websites(
    http_request: Request,
    url_a: str = Query(...),
    url_b: str = Query(...),
    company_a: str = Query(""),
    company_b: str = Query(""),
):
    enforce_rate_limit(http_request)
    import asyncio

    report_a, report_b = await asyncio.gather(
        run_all_checks(url_a, company_a or None),
        run_all_checks(url_b, company_b or None),
    )
    return {"report_a": report_a.model_dump(), "report_b": report_b.model_dump()}
