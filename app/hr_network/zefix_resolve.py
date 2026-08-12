"""Shared Zefix company resolution (fraud network + watch intake)."""

from __future__ import annotations

import asyncio
import re
from datetime import date, datetime
from typing import Any

from app.checks.zefix_check import _format_uid, _is_active, _zefix_get, _zefix_search


def uid_digits(uid: str | None) -> str:
    return re.sub(r"\D", "", uid or "")


def oldest_sogc_date(sogc_pub: list | None) -> date | None:
    dates: list[date] = []
    for pub in sogc_pub or []:
        if not isinstance(pub, dict):
            continue
        raw = pub.get("sogcDate") or pub.get("shabDate")
        if not raw:
            continue
        try:
            dates.append(datetime.strptime(str(raw)[:10], "%Y-%m-%d").date())
        except ValueError:
            continue
    return min(dates) if dates else None


def company_age_years(sogc_pub: list | None, *, reference: date | None = None) -> float | None:
    oldest = oldest_sogc_date(sogc_pub)
    if not oldest:
        return None
    ref = reference or date.today()
    return round((ref - oldest).days / 365.25, 2)


def _norm_company_name(name: str | None) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def _pick_search_hit(query_name: str, results: list[dict]) -> dict:
    """Prefer exact name match (incl. liquidated); then active; else first hit."""
    q = _norm_company_name(query_name)
    exact = [
        c
        for c in results
        if isinstance(c, dict) and _norm_company_name(c.get("name")) == q
    ]
    pool = exact or [c for c in results if isinstance(c, dict)]
    if not pool:
        raise LookupError(f"Keine Firma gefunden für «{query_name}»")
    # Exact name wins even when cancelled/liquidated — mandate graphs need those firms.
    if exact:
        return next((c for c in exact if _is_active(c)), exact[0])
    return next((c for c in pool if _is_active(c)), pool[0])


async def resolve_company_detail(name: str | None, uid: str | None) -> dict[str, Any]:
    """Resolve Zefix company detail by UID (preferred) or name search."""
    if uid:
        digits = uid_digits(uid)
        if len(digits) == 9:
            data = await asyncio.to_thread(_zefix_get, f"/company/uid/CHE{digits}")
            if isinstance(data, list):
                if not data:
                    raise LookupError(f"Keine Firma für UID CHE{digits}")
                active = next((c for c in data if _is_active(c)), data[0])
                return active if isinstance(active, dict) else data[0]
            if isinstance(data, dict):
                return data
        raise ValueError(f"Ungültige UID: {uid}")

    if not name or not str(name).strip():
        raise ValueError("Firmenname oder UID erforderlich")

    q = str(name).strip()
    results = await asyncio.to_thread(_zefix_search, q)
    if not results:
        raise LookupError(f"Keine Firma gefunden für «{q}»")
    best = _pick_search_hit(q, results)
    ehraid = best.get("ehraid")
    if not ehraid:
        raise LookupError(f"Zefix-Treffer ohne EHRA-ID für «{name}»")
    return await asyncio.to_thread(_zefix_get, f"/company/ehraid/{ehraid}")


def format_company_uid(detail: dict) -> str | None:
    uid_raw = str(detail.get("uid") or "")
    return _format_uid(uid_raw) if uid_raw else None
