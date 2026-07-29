"""Zefix helpers + thin HR-network wrapper around fraud_network engine."""

from __future__ import annotations

import asyncio
from typing import Any

import config
from app.checks.zefix_check import _format_uid, _zefix_search


def _legal_form_label(legal_form: Any) -> str:
    if isinstance(legal_form, dict):
        name = legal_form.get("name")
        if isinstance(name, dict):
            return name.get("de") or name.get("fr") or name.get("en") or ""
        return str(name or "")
    return str(legal_form or "")


def _format_address(address: Any) -> str | None:
    if not isinstance(address, dict):
        return None
    parts = [
        address.get("organisation"),
        " ".join(filter(None, [address.get("street"), address.get("houseNumber")])),
        " ".join(filter(None, [address.get("swissZipCode"), address.get("town")])),
    ]
    text = ", ".join(p for p in parts if p)
    return text or None


async def build_hr_network(company: str | None = None, uid: str | None = None) -> dict[str, Any]:
    """
    Back-compat wrapper: single-company level-2 analysis via build_fraud_network.
    """
    from app.hr_network.fraud_network import build_fraud_network

    if not config.ZEFIX_USERNAME or not config.ZEFIX_PASSWORD:
        raise PermissionError(
            "Zefix-Zugangsdaten fehlen — ZEFIX_USERNAME und ZEFIX_PASSWORD in .env setzen"
        )

    data = await build_fraud_network(
        level=2,
        ad_hoc_company={"name": company or "", "uid": uid or ""},
        max_person_searches=0,
    )
    if data.get("errors") and not data.get("seed_companies"):
        err = data["errors"][0].get("error") or "Firma nicht gefunden"
        raise LookupError(err)

    seed = (data.get("seed_companies") or [None])[0] or {}
    persons = [
        {
            "id": p.get("person_id") or p.get("id"),
            "name": p.get("name"),
            "roles": p.get("roles") or [],
            "residence": p.get("residence"),
            "nationality": p.get("nationality"),
            "heimatort": p.get("heimatort"),
            "gender": p.get("gender"),
            "status": p.get("status"),
            "source_date": None,
            "seed_company": p.get("seed_company"),
        }
        for p in (data.get("persons_table") or [])
    ]
    by_id = {
        n["id"].removeprefix("person:"): n
        for n in data.get("nodes") or []
        if n.get("type") == "person"
    }
    for p in persons:
        node = by_id.get(p.get("id") or "")
        if node:
            p["roles"] = node.get("roles") or p.get("roles") or []
            p["residence"] = node.get("residence") or p.get("residence")
            p["nationality"] = node.get("nationality") or p.get("nationality")
            p["heimatort"] = node.get("heimatort") or p.get("heimatort")
            p["gender"] = node.get("gender") or p.get("gender")
            p["source_date"] = node.get("first_seen") or node.get("last_seen")

    return {
        "query": company or uid,
        "company": {
            "name": seed.get("name"),
            "ehraid": seed.get("ehraid"),
            "uid": seed.get("uid"),
            "status": seed.get("status"),
            "legal_form": seed.get("legal_form"),
            "canton": (
                (seed.get("canton") or {}).get("id")
                if isinstance(seed.get("canton"), dict)
                else seed.get("canton")
            ),
            "registry_office_id": seed.get("registry_office_id"),
            "legal_seat": seed.get("legal_seat"),
            "address": seed.get("address"),
            "capital": seed.get("capital"),
            "purpose_short": seed.get("purpose_short"),
            "zefix_url": seed.get("zefix_url"),
            "cantonal_excerpt_url": seed.get("cantonal_excerpt_url"),
        },
        "persons": persons,
        "nodes": data.get("nodes") or [],
        "edges": data.get("edges") or [],
        "warnings": seed.get("warnings") or [],
        "mutation_analysis": seed.get("mutation_analysis"),
        "publication_count": seed.get("publication_count", 0),
        "recent_publications": seed.get("recent_publications") or [],
        "search_matches": None,
        "level": data.get("level"),
        "stats": data.get("stats"),
        "persons_table": data.get("persons_table"),
        "seed_companies": data.get("seed_companies"),
    }


async def search_companies_preview(name: str, limit: int = 8) -> list[dict]:
    if not config.ZEFIX_USERNAME or not config.ZEFIX_PASSWORD:
        raise PermissionError("Zefix-Zugangsdaten fehlen")
    results = await asyncio.to_thread(_zefix_search, name)
    preview = []
    for item in (results or [])[:limit]:
        status = item.get("status", {})
        status_label = (
            status.get("shortDescription", status.get("key", ""))
            if isinstance(status, dict)
            else status
        )
        preview.append({
            "name": item.get("name"),
            "ehraid": item.get("ehraid"),
            "uid": _format_uid(str(item.get("uid", "") or "")),
            "status": status_label,
            "canton": (
                (item.get("canton") or {}).get("id")
                if isinstance(item.get("canton"), dict)
                else item.get("canton")
            ),
            "legal_seat": item.get("legalSeat"),
        })
    return preview
