"""Annotate network person nodes with watchlist / CompanyCase involvement."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.database import (
    CaseBankCheckItem,
    CompanyCase,
    WatchedPerson,
    async_session,
)
from app.hr_network.company_cases import ACTIVE_FRAUD_STATUSES, OPEN_STATUSES
from app.hr_network.shab_parser import _normalize_person_id


def _slug_from_person_node(node: dict[str, Any]) -> str:
    nid = str(node.get("id") or "")
    if nid.startswith("person:"):
        return nid.split(":", 1)[1]
    label = node.get("label") or node.get("name") or ""
    return _normalize_person_id(str(label)) if label else ""


async def load_case_person_index() -> dict[str, dict[str, Any]]:
    """
    Map person_slug → involvement flags.

    A person is «case involved» when:
    - on the watchlist (not cleared), and/or
    - listed on a CompanyCase bank-check as person entity
    """
    index: dict[str, dict[str, Any]] = {}

    async with async_session() as session:
        people = list(
            (
                await session.execute(
                    select(WatchedPerson).where(WatchedPerson.status != "cleared")
                )
            ).scalars().all()
        )
        for p in people:
            slug = (p.person_slug or "").strip()
            if not slug:
                slug = _normalize_person_id(p.display_name or "")
            if not slug:
                continue
            index[slug] = {
                "case_involved": True,
                "on_watchlist": True,
                "watched_person_id": p.id,
                "watch_status": p.status,
                "case_flag_label": (
                    "Confirmed Fraud"
                    if p.status == "confirmed_fraud"
                    else "Watchlist"
                ),
            }

        # Persons named on open / confirmed company cases (checklist)
        case_ids = list(
            (
                await session.execute(
                    select(CompanyCase.id).where(
                        CompanyCase.status.in_(tuple(set(OPEN_STATUSES + ACTIVE_FRAUD_STATUSES)))
                    )
                )
            ).scalars().all()
        )
        if case_ids:
            items = list(
                (
                    await session.execute(
                        select(CaseBankCheckItem).where(
                            CaseBankCheckItem.case_id.in_(case_ids),
                            CaseBankCheckItem.entity_type == "person",
                        )
                    )
                ).scalars().all()
            )
            for item in items:
                slug = ""
                ref = (item.entity_ref or "").strip()
                if ref.isdigit():
                    wp = await session.get(WatchedPerson, int(ref))
                    if wp:
                        slug = wp.person_slug or _normalize_person_id(wp.display_name or "")
                if not slug and item.entity_label:
                    slug = _normalize_person_id(item.entity_label)
                if not slug:
                    continue
                hit = index.setdefault(
                    slug,
                    {
                        "case_involved": True,
                        "on_watchlist": False,
                        "watched_person_id": int(ref) if ref.isdigit() else None,
                        "watch_status": None,
                        "case_flag_label": "Fall",
                    },
                )
                hit["case_involved"] = True
                if hit.get("case_flag_label") == "Watchlist":
                    hit["case_flag_label"] = "Fall / Watchlist"

    return index


def apply_case_flags_to_network(
    data: dict[str, Any],
    index: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Mutate nodes + persons_table in place with case involvement flags."""
    if not index:
        return data

    flagged = 0
    for node in data.get("nodes") or []:
        if node.get("type") != "person":
            continue
        slug = _slug_from_person_node(node)
        hit = index.get(slug)
        if not hit and node.get("label"):
            hit = index.get(_normalize_person_id(str(node["label"])))
        if not hit:
            continue
        node.update(hit)
        flagged += 1

    for person in data.get("persons_table") or []:
        pid = person.get("person_id") or person.get("id") or ""
        slug = str(pid)
        hit = index.get(slug)
        if not hit and person.get("name"):
            hit = index.get(_normalize_person_id(str(person["name"])))
        if hit:
            person.update(hit)

    # Also annotate persons list used by service wrapper
    for person in data.get("persons") or []:
        pid = person.get("id") or ""
        hit = index.get(str(pid))
        if not hit and person.get("name"):
            hit = index.get(_normalize_person_id(str(person["name"])))
        if hit:
            person.update(hit)

    stats = data.setdefault("stats", {})
    stats["case_flagged_persons"] = flagged
    return data


async def annotate_network_with_case_flags(data: dict[str, Any]) -> dict[str, Any]:
    try:
        index = await load_case_person_index()
    except Exception:
        return data
    return apply_case_flags_to_network(data, index)
