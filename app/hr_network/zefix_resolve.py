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

    results = await asyncio.to_thread(_zefix_search, str(name).strip())
    if not results:
        raise LookupError(f"Keine Firma gefunden für «{str(name).strip()}»")
    best = next((c for c in results if _is_active(c)), results[0])
    ehraid = best.get("ehraid")
    if not ehraid:
        raise LookupError(f"Zefix-Treffer ohne EHRA-ID für «{name}»")
    return await asyncio.to_thread(_zefix_get, f"/company/ehraid/{ehraid}")


def format_company_uid(detail: dict) -> str | None:
    uid_raw = str(detail.get("uid") or "")
    return _format_uid(uid_raw) if uid_raw else None
