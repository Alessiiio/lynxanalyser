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
from app.hr_network.person_names import names_same_person, person_identity_key
from app.hr_network.shab_parser import _normalize_person_id


def _slug_from_person_node(node: dict[str, Any]) -> str:
    nid = str(node.get("id") or "")
    if nid.startswith("person:"):
        return nid.split(":", 1)[1]
    label = node.get("label") or node.get("name") or ""
    return _normalize_person_id(str(label)) if label else ""


def _register_case_flags(
    by_slug: dict[str, dict[str, Any]],
    by_name: list[tuple[str, dict[str, Any]]],
    *,
    slug: str,
    display_name: str | None,
    flags: dict[str, Any],
) -> None:
    """Index under exact slug + display name.

    Do **not** index solely by surname+first-given fingerprint: conflicting
    middle names share that key and must stay separate (``names_same_person``).
    Middle-name variants are matched at lookup via ``names_same_person``.
    """
    if slug:
        existing = by_slug.get(slug)
        if existing:
            existing.update({k: v for k, v in flags.items() if v})
            flags = existing
        else:
            by_slug[slug] = flags
    if display_name:
        raw = _normalize_person_id(display_name)
        if raw and raw not in by_slug:
            by_slug[raw] = flags
        by_name.append((display_name, flags))


def _lookup_case_flags(
    by_slug: dict[str, dict[str, Any]],
    by_name: list[tuple[str, dict[str, Any]]],
    *,
    slug: str,
    label: str | None,
) -> dict[str, Any] | None:
    if slug and slug in by_slug:
        return by_slug[slug]
    if label:
        raw = _normalize_person_id(str(label))
        if raw and raw in by_slug:
            return by_slug[raw]
        for name, flags in by_name:
            if names_same_person(label, name):
                return flags
    if slug:
        # Match stored full-name slug, or identity key only when a registered
        # display name collapses to that key (middle-name-safe via by_name above).
        for name, flags in by_name:
            if _normalize_person_id(name) == slug:
                return flags
            # Watchlist short slug «barbul-michael» ↔ node «barbul-michael-gabriel»
            if person_identity_key(name) == slug:
                return flags
    return None


async def load_case_person_index() -> dict[str, Any]:
    """
    Map person_slug → involvement flags (+ name list for middle-name matching).

    A person is «case involved» when:
    - on the watchlist (not cleared), and/or
    - listed on a CompanyCase bank-check as person entity
    """
    by_slug: dict[str, dict[str, Any]] = {}
    by_name: list[tuple[str, dict[str, Any]]] = []

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
            if not slug and not p.display_name:
                continue
            src = (p.source_reason or "").strip()
            from_case = src in {
                "case_open",
                "fraud_list_officer",
                "under_investigation",
            } or (p.status or "") == "confirmed_fraud"
            flags = {
                "case_involved": True,
                "on_watchlist": True,
                "watched_person_id": p.id,
                "watch_status": p.status,
                "case_flag_label": "Fraudfall" if from_case else "Watchlist",
            }
            _register_case_flags(
                by_slug,
                by_name,
                slug=slug,
                display_name=p.display_name,
                flags=flags,
            )

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
                display = item.entity_label
                if ref.isdigit():
                    wp = await session.get(WatchedPerson, int(ref))
                    if wp:
                        slug = wp.person_slug or _normalize_person_id(wp.display_name or "")
                        display = display or wp.display_name
                if not slug and item.entity_label:
                    slug = _normalize_person_id(item.entity_label)
                if not slug and not display:
                    continue
                flags = {
                    "case_involved": True,
                    "on_watchlist": False,
                    "watched_person_id": int(ref) if ref.isdigit() else None,
                    "watch_status": None,
                    "case_flag_label": "Fraudfall",
                }
                hit = by_slug.get(slug) if slug else None
                if hit:
                    hit["case_involved"] = True
                    if hit.get("case_flag_label") in (None, "", "Watchlist"):
                        hit["case_flag_label"] = "Fraudfall / Watchlist"
                    elif "Fraudfall" not in str(hit.get("case_flag_label") or ""):
                        hit["case_flag_label"] = "Fraudfall / Watchlist"
                else:
                    _register_case_flags(
                        by_slug,
                        by_name,
                        slug=slug,
                        display_name=display,
                        flags=flags,
                    )

    return {"by_slug": by_slug, "by_name": by_name}


def apply_case_flags_to_network(
    data: dict[str, Any],
    index: dict[str, Any],
) -> dict[str, Any]:
    """Mutate nodes + persons_table in place with case involvement flags."""
    # Backward compatible: plain slug→flags dict still works
    if not index:
        return data
    if "by_slug" in index:
        by_slug: dict[str, dict[str, Any]] = index.get("by_slug") or {}
        by_name: list[tuple[str, dict[str, Any]]] = list(index.get("by_name") or [])
    else:
        by_slug = index  # type: ignore[assignment]
        by_name = []

    if not by_slug and not by_name:
        return data

    flagged = 0
    for node in data.get("nodes") or []:
        if node.get("type") != "person":
            continue
        slug = _slug_from_person_node(node)
        label = node.get("label") or node.get("name")
        hit = _lookup_case_flags(by_slug, by_name, slug=slug, label=str(label) if label else None)
        if not hit:
            continue
        node.update(hit)
        flagged += 1

    for person in data.get("persons_table") or []:
        pid = person.get("person_id") or person.get("id") or ""
        label = person.get("name")
        hit = _lookup_case_flags(
            by_slug, by_name, slug=str(pid), label=str(label) if label else None
        )
        if hit:
            person.update(hit)

    # Also annotate persons list used by service wrapper
    for person in data.get("persons") or []:
        pid = person.get("id") or ""
        label = person.get("name")
        hit = _lookup_case_flags(
            by_slug, by_name, slug=str(pid), label=str(label) if label else None
        )
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
