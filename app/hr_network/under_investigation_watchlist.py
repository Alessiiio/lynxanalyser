"""«In Abklärung» → Watchlist side-effects (Firma + aktuelle Organe)."""

from __future__ import annotations

import logging
from typing import Any

from app.hr_network.watch_intake import ensure_seed_link, upsert_watched_person
from app.hr_network.watched_companies import (
    SOURCE_UNDER_INVESTIGATION,
    upsert_watched_company,
)
from app.hr_network.shab_parser import _normalize_person_id

logger = logging.getLogger(__name__)


def _current_persons_from_payload(persons: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in persons or []:
        if not isinstance(p, dict):
            continue
        status = (p.get("status") or "current").strip().lower()
        if status and status != "current":
            continue
        name = (p.get("name") or p.get("display_name") or p.get("person_name") or "").strip()
        if not name:
            continue
        out.append(
            {
                "name": name,
                "residence": (p.get("residence") or "").strip() or None,
                "roles": list(p.get("roles") or []),
                "person_id": p.get("person_id") or p.get("id") or "",
            }
        )
    return out


async def _fetch_current_organs_l2(
    *,
    company_name: str | None,
    company_uid: str | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """
    L2 network fetch for current organs when the client did not send persons.

    Note: the server has no session copy of Firmenanalyse ``lastAnalysis``.
    Prefer passing ``persons`` from the UI (persons_table / current organs).
    Fall back to a level-2 Zefix/SHAB scan (no mandate expansion) when absent.
    """
    from app.hr_network.fraud_network import build_fraud_network

    data = await build_fraud_network(
        level=2,
        ad_hoc_company={"name": company_name or "", "uid": company_uid or ""},
        max_person_searches=0,
    )
    seed = (data.get("seed_companies") or [None])[0] or {}
    persons = _current_persons_from_payload(list(data.get("persons_table") or []))
    return seed, persons


async def enroll_under_investigation_watchlist(
    *,
    company_name: str | None,
    company_uid: str | None,
    added_by: str | None = None,
    address: str | None = None,
    legal_seat: str | None = None,
    company_ehraid: int | None = None,
    persons: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Upsert watched company + current organs as watched persons.

    Removing the «In Abklärung» tag does **not** auto-remove watchlist rows
    (safer; Tag ≠ Watchlist-Lebenszyklus).
    """
    name = (company_name or "").strip()
    uid = (company_uid or "").strip() or None
    addr = (address or "").strip() or None
    seat = (legal_seat or "").strip() or None
    ehraid = company_ehraid
    organ_list = _current_persons_from_payload(persons)

    # Resolve address / organs if client context incomplete
    if (not addr and not seat) or not organ_list:
        try:
            seed, fetched = await _fetch_current_organs_l2(
                company_name=name or None,
                company_uid=uid,
            )
            if seed:
                name = (seed.get("name") or name or "").strip() or name
                uid = (seed.get("uid") or uid or None) or None
                addr = addr or (seed.get("address") or None)
                seat = seat or (seed.get("legal_seat") or None)
                if ehraid is None and seed.get("ehraid") is not None:
                    try:
                        ehraid = int(seed["ehraid"])
                    except (TypeError, ValueError):
                        pass
            if not organ_list:
                organ_list = fetched
        except Exception as e:
            logger.info(
                "In-Abklärung watchlist: L2 fetch skipped (%s / %s): %s",
                name,
                uid,
                e,
            )

    company = await upsert_watched_company(
        company_name=name or uid,
        company_uid=uid,
        company_ehraid=ehraid,
        address=addr,
        legal_seat=seat,
        source_reason=SOURCE_UNDER_INVESTIGATION,
        added_by=added_by,
    )

    enrolled_persons: list[dict[str, Any]] = []
    for p in organ_list:
        display = p["name"]
        slug = _normalize_person_id(display)
        if not slug:
            continue
        wp = await upsert_watched_person(
            person_slug=slug,
            display_name=display,
            residence=p.get("residence"),
            source_company_ehraid=ehraid,
            source_company_name=company.get("company_name") or name,
            source_reason=SOURCE_UNDER_INVESTIGATION,
            status="active",
            notes="Auto: In Abklärung (aktuelle Organe)",
        )
        role = ", ".join(p.get("roles") or []) or None
        await ensure_seed_link(
            person_id=wp.id,
            company_ehraid=ehraid,
            company_name=company.get("company_name") or name or "Unbekannt",
            company_uid=uid,
            role=role,
        )
        enrolled_persons.append(
            {
                "person_id": wp.id,
                "display_name": display,
                "person_slug": slug,
            }
        )

    return {
        "company": company,
        "persons": enrolled_persons,
        "persons_enrolled": len(enrolled_persons),
    }
