"""Auto-enroll officers from fraud companies / shell-takeover hits onto the watchlist."""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select

from app.checks.shell_takeover import detect_shell_takeover_pattern
from app.database import PersonCompanyLink, WatchedPerson, async_session
from app.hr_network.shab_parser import build_person_timeline
from app.hr_network.zefix_resolve import (
    company_age_years,
    format_company_uid,
    oldest_sogc_date,
    resolve_company_detail,
)

logger = logging.getLogger(__name__)

# Officers who joined within this fraction of company age (or absolute years) = takeover suspects.
_RECENT_JOIN_YEARS = 2.0
_OLD_OFFICER_MIN_YEARS = 5.0


def _parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _priority_for_person(
    *,
    first_seen: str | None,
    company_age: float | None,
    reference: date,
) -> str:
    """
    Return watch status: active (takeover suspect) or low_priority (long-established).
    """
    joined = _parse_iso_date(first_seen)
    if joined is None:
        return "active"
    years_in_company = (reference - joined).days / 365.25

    # Joined recently relative to now → likely the takeover actor.
    if years_in_company <= _RECENT_JOIN_YEARS:
        return "active"

    # Present for most of a long company life → lower priority (possible straw man, not primary).
    if company_age is not None and company_age >= _OLD_OFFICER_MIN_YEARS and years_in_company >= _OLD_OFFICER_MIN_YEARS:
        return "low_priority"

    # Mid tenure on an old firm still interesting.
    if company_age is not None and company_age >= _OLD_OFFICER_MIN_YEARS and years_in_company <= 4:
        return "active"

    return "active"


async def upsert_watched_person(
    *,
    person_slug: str,
    display_name: str,
    residence: str | None,
    source_company_ehraid: int | None,
    source_company_name: str | None,
    source_reason: str,
    status: str,
    notes: str | None = None,
) -> WatchedPerson:
    async with async_session() as session:
        result = await session.execute(
            select(WatchedPerson).where(WatchedPerson.person_slug == person_slug)
        )
        existing = result.scalar_one_or_none()
        if existing:
            # Escalate low_priority → active if new stronger reason
            if existing.status == "low_priority" and status == "active":
                existing.status = "active"
            if source_reason and existing.source_reason != "fraud_list_officer":
                if source_reason == "fraud_list_officer":
                    existing.source_reason = source_reason
            if residence and not existing.residence:
                existing.residence = residence
            if notes and not existing.notes:
                existing.notes = notes
            await session.commit()
            await session.refresh(existing)
            return existing

        person = WatchedPerson(
            person_slug=person_slug,
            display_name=display_name,
            residence=residence,
            source_company_ehraid=source_company_ehraid,
            source_company_name=source_company_name,
            source_reason=source_reason,
            status=status,
            notes=notes,
            added_at=datetime.now(timezone.utc),
        )
        session.add(person)
        await session.commit()
        await session.refresh(person)
        return person


async def ensure_seed_link(
    *,
    person_id: int,
    company_ehraid: int | None,
    company_name: str,
    company_uid: str | None,
    role: str | None,
) -> PersonCompanyLink:
    async with async_session() as session:
        q = select(PersonCompanyLink).where(PersonCompanyLink.person_id == person_id)
        if company_ehraid:
            q = q.where(PersonCompanyLink.company_ehraid == company_ehraid)
        else:
            q = q.where(PersonCompanyLink.company_name == company_name)
        result = await session.execute(q)
        existing = result.scalar_one_or_none()
        if existing:
            return existing

        link = PersonCompanyLink(
            person_id=person_id,
            company_ehraid=company_ehraid,
            company_name=company_name,
            company_uid=company_uid,
            role=role,
            relation_type="seed",
            is_seed_company=True,
            first_detected_at=datetime.now(timezone.utc),
            match_confidence="high",
        )
        session.add(link)
        await session.commit()
        await session.refresh(link)
        return link


async def intake_from_fraud_company(
    *,
    name: str | None,
    uid: str | None,
    include_former: bool = False,
) -> dict[str, Any]:
    """
    Resolve company, prioritize recent officers, enroll onto watchlist.

    By default only *current* HR persons are enrolled. Former officers stay
    optional (manual Watch from Firmenanalyse / case checklist).
    """
    detail = await resolve_company_detail(name, uid)
    sogc = detail.get("sogcPub")
    age = company_age_years(sogc)
    oldest = oldest_sogc_date(sogc)
    ehraid = detail.get("ehraid")
    company_name = detail.get("name") or name or "Unbekannt"
    company_uid = format_company_uid(detail)
    reference = date.today()

    timeline = build_person_timeline(sogc)
    enrolled: list[dict[str, Any]] = []
    skipped_former: list[dict[str, Any]] = []

    for person in timeline:
        slug = person.get("id")
        display = person.get("name")
        if not slug or not display:
            continue

        if person.get("status") == "former" and not include_former:
            skipped_former.append({
                "person_slug": slug,
                "display_name": display,
                "person_hr_status": "former",
                "roles": list(person.get("roles") or []),
                "exited_date": person.get("exited_date"),
            })
            continue

        status = _priority_for_person(
            first_seen=person.get("first_seen"),
            company_age=age,
            reference=reference,
        )
        if person.get("status") == "former":
            exited = _parse_iso_date(person.get("exited_date"))
            if exited and (reference - exited).days > 365 * 3:
                status = "low_priority"

        wp = await upsert_watched_person(
            person_slug=slug,
            display_name=display,
            residence=person.get("residence"),
            source_company_ehraid=int(ehraid) if ehraid else None,
            source_company_name=company_name,
            source_reason="fraud_list_officer",
            status=status,
            notes=f"first_seen={person.get('first_seen')} status={person.get('status')}",
        )
        role = ", ".join(person.get("roles") or []) or None
        await ensure_seed_link(
            person_id=wp.id,
            company_ehraid=int(ehraid) if ehraid else None,
            company_name=company_name,
            company_uid=company_uid,
            role=role,
        )
        enrolled.append({
            "person_id": wp.id,
            "person_slug": slug,
            "display_name": display,
            "watch_status": status,
            "person_hr_status": person.get("status"),
            "roles": list(person.get("roles") or []),
            "first_seen": person.get("first_seen"),
        })

    return {
        "company_name": company_name,
        "company_uid": company_uid,
        "ehraid": ehraid,
        "company_age_years": age,
        "company_first_seen": oldest.isoformat() if oldest else None,
        "enrolled": enrolled,
        "enrolled_count": len(enrolled),
        "skipped_former": skipped_former,
        "skipped_former_count": len(skipped_former),
        "include_former": include_former,
    }


async def intake_from_shell_takeover(
    detail: dict[str, Any],
    pattern: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Enroll new officers when shell-takeover pattern fires (preventive)."""
    pattern = pattern or detect_shell_takeover_pattern(detail.get("sogcPub"))
    if not pattern.get("pattern_detected"):
        return {"enrolled_count": 0, "skipped": True, "reason": pattern.get("reason")}

    ehraid = detail.get("ehraid")
    company_name = detail.get("name") or "Unbekannt"
    company_uid = format_company_uid(detail)
    enrolled: list[dict[str, Any]] = []

    officers = pattern.get("new_officers") or []
    if not officers:
        # Fall back to current timeline persons with recent first_seen
        timeline = build_person_timeline(detail.get("sogcPub"))
        officers = [
            {"id": p.get("id"), "name": p.get("name"), "first_seen": p.get("first_seen"), "roles": p.get("roles"), "residence": p.get("residence")}
            for p in timeline
            if p.get("status") == "current" and p.get("id") in (pattern.get("new_officer_slugs") or [])
        ]

    for person in officers:
        slug = person.get("id")
        display = person.get("name")
        if not slug or not display:
            continue
        wp = await upsert_watched_person(
            person_slug=slug,
            display_name=display,
            residence=person.get("residence"),
            source_company_ehraid=int(ehraid) if ehraid else None,
            source_company_name=company_name,
            source_reason="shell_takeover_pattern",
            status="active",
            notes=f"shell_takeover confidence={pattern.get('confidence')}",
        )
        role = ", ".join(person.get("roles") or []) or None
        await ensure_seed_link(
            person_id=wp.id,
            company_ehraid=int(ehraid) if ehraid else None,
            company_name=company_name,
            company_uid=company_uid,
            role=role,
        )
        enrolled.append({"person_id": wp.id, "person_slug": slug, "display_name": display})

    return {
        "company_name": company_name,
        "ehraid": ehraid,
        "pattern": pattern,
        "enrolled": enrolled,
        "enrolled_count": len(enrolled),
    }
