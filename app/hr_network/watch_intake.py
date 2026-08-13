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

SOURCE_CASE_OPEN = "case_open"
SOURCE_FRAUD_LIST_OFFICER = "fraud_list_officer"
SOURCE_UNDER_INVESTIGATION = "under_investigation"
SOURCE_SHELL_TAKEOVER = "shell_takeover_pattern"

# Nightly scan-first tier (see person_monitoring.select_monitoring_batch).
HIGH_SCAN_PRIORITY_SOURCES = frozenset(
    {
        SOURCE_CASE_OPEN,
        SOURCE_FRAUD_LIST_OFFICER,
        SOURCE_UNDER_INVESTIGATION,
        SOURCE_SHELL_TAKEOVER,
    }
)

SCAN_PRIORITY_HIGH = "high"
SCAN_PRIORITY_NORMAL = "normal"


def default_scan_priority(source_reason: str | None) -> str:
    if (source_reason or "") in HIGH_SCAN_PRIORITY_SOURCES:
        return SCAN_PRIORITY_HIGH
    return SCAN_PRIORITY_NORMAL


def _escalate_scan_priority(current: str | None, requested: str | None) -> str:
    """high wins; never downgrade high → normal on upsert."""
    if (current or "") == SCAN_PRIORITY_HIGH or (requested or "") == SCAN_PRIORITY_HIGH:
        return SCAN_PRIORITY_HIGH
    return SCAN_PRIORITY_NORMAL



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
    scan_priority: str | None = None,
) -> WatchedPerson:
    wanted_prio = scan_priority or default_scan_priority(source_reason)
    async with async_session() as session:
        result = await session.execute(
            select(WatchedPerson).where(WatchedPerson.person_slug == person_slug)
        )
        existing = result.scalar_one_or_none()
        if existing:
            # Escalate low_priority → active if new stronger reason
            if existing.status == "low_priority" and status == "active":
                existing.status = "active"
            # Prefer stronger source labels (confirmed fraud > case open > soft tags)
            _SOURCE_RANK = {
                SOURCE_FRAUD_LIST_OFFICER: 0,
                SOURCE_CASE_OPEN: 1,
                SOURCE_UNDER_INVESTIGATION: 2,
                SOURCE_SHELL_TAKEOVER: 3,
            }
            if source_reason:
                cur_rank = _SOURCE_RANK.get(existing.source_reason, 99)
                new_rank = _SOURCE_RANK.get(source_reason, 99)
                if new_rank < cur_rank:
                    existing.source_reason = source_reason
            if residence and not existing.residence:
                existing.residence = residence
            if notes and not existing.notes:
                existing.notes = notes
            existing.scan_priority = _escalate_scan_priority(
                getattr(existing, "scan_priority", None), wanted_prio
            )
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
            scan_priority=wanted_prio,
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
    source_reason: str = SOURCE_FRAUD_LIST_OFFICER,
    scan_priority: str = SCAN_PRIORITY_HIGH,
    notes_prefix: str | None = None,
) -> dict[str, Any]:
    """
    Resolve company, prioritize recent officers, enroll onto watchlist.

    By default only *current* HR persons are enrolled. Former officers stay
    optional (manual Watch from Firmenanalyse / case checklist).

    Used on case open (``case_open``) and confirm fraud (``fraud_list_officer``).
    """
    # Demo fixture: persons_table (SHAB demo text is not parser-compatible).
    try:
        from app.hr_network.demo_fixture import (
            DemoFixtureError,
            build_demo_company_detail,
            is_demo_request,
        )

        if is_demo_request(name=name, uid=uid):
            detail = build_demo_company_detail()
            return await _intake_from_person_rows(
                company_name=detail.get("name") or name or "Unbekannt",
                company_uid=format_company_uid(detail) or uid,
                ehraid=detail.get("ehraid"),
                persons=list(detail.get("persons_table") or []),
                include_former=include_former,
                source_reason=source_reason,
                scan_priority=scan_priority,
                notes_prefix=notes_prefix,
                company_age=None,
                company_first_seen=None,
            )
    except DemoFixtureError:
        raise
    except Exception as e:
        logger.info("Demo intake short-circuit skipped: %s", e)

    detail = await resolve_company_detail(name, uid)
    sogc = detail.get("sogcPub")
    age = company_age_years(sogc)
    oldest = oldest_sogc_date(sogc)
    ehraid = detail.get("ehraid")
    company_name = detail.get("name") or name or "Unbekannt"
    company_uid = format_company_uid(detail)
    reason = (source_reason or SOURCE_FRAUD_LIST_OFFICER).strip() or SOURCE_FRAUD_LIST_OFFICER
    prio = scan_priority or default_scan_priority(reason)
    note_head = (notes_prefix or reason).strip()

    timeline = build_person_timeline(sogc)
    # Fallback when sogcPub empty/unparseable but persons_table present (e.g. demos)
    if not timeline and detail.get("persons_table"):
        return await _intake_from_person_rows(
            company_name=company_name,
            company_uid=company_uid,
            ehraid=ehraid,
            persons=list(detail.get("persons_table") or []),
            include_former=include_former,
            source_reason=reason,
            scan_priority=prio,
            notes_prefix=note_head,
            company_age=age,
            company_first_seen=oldest.isoformat() if oldest else None,
        )

    enrolled: list[dict[str, Any]] = []
    skipped_former: list[dict[str, Any]] = []
    reference = date.today()

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
            source_reason=reason,
            status=status,
            scan_priority=prio,
            notes=(
                f"{note_head} first_seen={person.get('first_seen')} "
                f"status={person.get('status')}"
            ),
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


async def _intake_from_person_rows(
    *,
    company_name: str,
    company_uid: str | None,
    ehraid: Any,
    persons: list[dict[str, Any]],
    include_former: bool,
    source_reason: str,
    scan_priority: str,
    notes_prefix: str | None,
    company_age: float | None,
    company_first_seen: str | None,
) -> dict[str, Any]:
    """Enroll from a persons_table-like list (demo fixture / network payload)."""
    reason = (source_reason or SOURCE_FRAUD_LIST_OFFICER).strip() or SOURCE_FRAUD_LIST_OFFICER
    prio = scan_priority or default_scan_priority(reason)
    note_head = (notes_prefix or reason).strip()
    reference = date.today()
    enrolled: list[dict[str, Any]] = []
    skipped_former: list[dict[str, Any]] = []
    ehraid_int = int(ehraid) if ehraid is not None else None

    for person in persons:
        if not isinstance(person, dict):
            continue
        display = (person.get("name") or person.get("display_name") or "").strip()
        slug = (
            person.get("person_id")
            or person.get("id")
            or person.get("person_slug")
            or ""
        )
        slug = str(slug).strip().removeprefix("person:")
        if not display:
            continue
        if not slug:
            from app.hr_network.shab_parser import _normalize_person_id

            slug = _normalize_person_id(display)
        if not slug:
            continue

        hr_status = (person.get("status") or person.get("person_hr_status") or "current")
        hr_status = str(hr_status).strip().lower() or "current"
        roles = list(person.get("roles") or [])

        if hr_status == "former" and not include_former:
            skipped_former.append({
                "person_slug": slug,
                "display_name": display,
                "person_hr_status": "former",
                "roles": roles,
                "exited_date": person.get("exited_date"),
            })
            continue

        first_seen = person.get("first_seen") or person.get("source_date")
        status = _priority_for_person(
            first_seen=first_seen,
            company_age=company_age,
            reference=reference,
        )
        if hr_status == "former":
            exited = _parse_iso_date(person.get("exited_date"))
            if exited and (reference - exited).days > 365 * 3:
                status = "low_priority"

        wp = await upsert_watched_person(
            person_slug=slug,
            display_name=display,
            residence=person.get("residence"),
            source_company_ehraid=ehraid_int,
            source_company_name=company_name,
            source_reason=reason,
            status=status,
            scan_priority=prio,
            notes=f"{note_head} first_seen={first_seen} status={hr_status}",
        )
        role = ", ".join(roles) or None
        await ensure_seed_link(
            person_id=wp.id,
            company_ehraid=ehraid_int,
            company_name=company_name,
            company_uid=company_uid,
            role=role,
        )
        enrolled.append({
            "person_id": wp.id,
            "person_slug": slug,
            "display_name": display,
            "watch_status": status,
            "person_hr_status": hr_status,
            "roles": roles,
            "first_seen": first_seen,
        })

    return {
        "company_name": company_name,
        "company_uid": company_uid,
        "ehraid": ehraid_int,
        "company_age_years": company_age,
        "company_first_seen": company_first_seen,
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
            source_reason=SOURCE_SHELL_TAKEOVER,
            status="active",
            scan_priority=SCAN_PRIORITY_HIGH,
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
