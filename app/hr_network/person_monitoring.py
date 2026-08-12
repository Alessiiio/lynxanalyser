"""Incremental SHAB monitoring for watched persons + network alerts."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, timezone
from typing import Any

from urllib.parse import quote

from sqlalchemy import delete, or_, select

from app.database import (
    CaseBankCheckItem,
    CompanyCase,
    NetworkAlert,
    PersonCompanyLink,
    PersonWatchScan,
    WatchedPerson,
    WatchedPersonStatusHistory,
    async_session,
)
from app.hr_network.moneyhouse_person import search_person_mandates
from app.hr_network.person_search import (
    _CANTON_REGISTRY,
    _person_label_matches,
    parse_person_query,
    search_person_in_sogc,
)
from app.hr_network.zefix_resolve import format_company_uid, resolve_company_detail

logger = logging.getLogger(__name__)

_FOUNDER_ROLE_HINTS = (
    "geschäftsführer",
    "inhaber",
    "gründer",
    "einzelunterschrift",
    "zeichnungsberechtigt",
)


def _month_add(year: int, month: int, delta: int) -> tuple[int, int]:
    idx = year * 12 + (month - 1) + delta
    return idx // 12, idx % 12 + 1


def _months_from(start_ym: str, *, include_overlap: int = 2) -> list[tuple[int, int]]:
    """Months to scan from last_scanned_month (rewinding overlap) through current month."""
    today = date.today()
    try:
        y, m = (int(x) for x in start_ym.split("-"))
    except ValueError:
        y, m = today.year, today.month
    y, m = _month_add(y, m, -include_overlap)
    months: list[tuple[int, int]] = []
    while (y, m) <= (today.year, today.month):
        months.append((y, m))
        y, m = _month_add(y, m, 1)
    return months


def estimate_match_confidence(
    *,
    watched: WatchedPerson,
    hit: dict[str, Any],
) -> str:
    """
    high = surname+firstname match and residence aligns (when both known)
    medium = name match only
    low = weak (should be rare with strict matcher)
    """
    score = 1
    residence = (watched.residence or "").strip().lower()
    snippet = (hit.get("snippet") or "").lower()
    person_name = hit.get("person_name") or ""
    try:
        q = parse_person_query(watched.display_name)
        if _person_label_matches(person_name, q):
            score += 1
    except ValueError:
        pass
    if residence and residence in snippet:
        score += 1
    if hit.get("role_hint"):
        score += 0.5
    if score >= 2.5:
        return "high"
    if score >= 1.5:
        return "medium"
    return "low"


def _alert_type_for_role(role_hint: str | None) -> tuple[str, str]:
    role = (role_hint or "").lower()
    if any(h in role for h in _FOUNDER_ROLE_HINTS) or not role_hint:
        return "new_company_founded", "high"
    return "new_role", "medium"


async def _get_or_create_scan_row(session, person_id: int) -> PersonWatchScan:
    result = await session.execute(
        select(PersonWatchScan).where(PersonWatchScan.person_id == person_id)
    )
    row = result.scalar_one_or_none()
    if row:
        return row
    # First scan: 3y lookback for interactive CH-wide scans (extend via repeat scans).
    today = date.today()
    start = today.replace(year=today.year - 3)
    row = PersonWatchScan(
        person_id=person_id,
        last_scanned_month=f"{start.year:04d}-{start.month:02d}",
        last_run_at=datetime.now(timezone.utc),
    )
    session.add(row)
    await session.flush()
    return row


async def _seed_exclude_uid(
    *,
    seed_uid: str | None,
    source_company_ehraid: int | None,
    source_company_name: str | None,
) -> str | None:
    """Resolve seed company UID so the SHAB scan can skip the known firm."""
    if seed_uid:
        return seed_uid
    from app.checks.zefix_check import _zefix_get

    if source_company_ehraid:
        try:
            detail = await asyncio.to_thread(
                _zefix_get, f"/company/ehraid/{int(source_company_ehraid)}"
            )
            if isinstance(detail, dict) and detail.get("uid"):
                return str(detail["uid"])
        except Exception:
            logger.debug(
                "SHAB exclude_uid: ehraid %s failed", source_company_ehraid, exc_info=True
            )
    if source_company_name:
        try:
            detail = await resolve_company_detail(source_company_name, None)
            if detail.get("uid"):
                return str(detail["uid"])
        except Exception:
            logger.debug("SHAB exclude_uid: name resolve failed", exc_info=True)
    return None


async def _resolve_watch_shab_scope(
    *,
    canton_override: str | None,
    source_company_ehraid: int | None,
    source_company_name: str | None,
    seed_uid: str | None,
) -> dict[str, Any]:
    """
    Default: ganze Schweiz — Mandate in mehreren Kantonen müssen sichtbar sein.
    Optionaler Kanton nur als manueller Schnellfilter (unvollständig).
    """
    exclude_uid = await _seed_exclude_uid(
        seed_uid=seed_uid,
        source_company_ehraid=source_company_ehraid,
        source_company_name=source_company_name,
    )
    override = (canton_override or "").strip().upper() or None
    if override and override in _CANTON_REGISTRY:
        return {
            "registry_office_id": _CANTON_REGISTRY[override],
            "canton": override,
            "exclude_uid": exclude_uid,
            "scope_source": "manual_canton",
        }

    return {
        "registry_office_id": None,
        "canton": None,
        "exclude_uid": exclude_uid,
        "scope_source": "nationwide",
    }


async def scan_watched_person_incremental(
    person_id: int,
    *,
    canton: str | None = None,
    include_shab: bool = False,
) -> dict[str, Any]:
    async with async_session() as session:
        person = await session.get(WatchedPerson, person_id)
        if not person:
            raise LookupError(f"WatchedPerson {person_id} nicht gefunden")
        if person.status not in ("active", "low_priority", "confirmed_fraud"):
            return {"skipped": True, "reason": f"status={person.status}"}

        scan_row = await _get_or_create_scan_row(session, person.id)
        last_month = scan_row.last_scanned_month
        months = _months_from(last_month, include_overlap=2)
        years_back = max(1, len(months) // 12 + 1)

        display_name = person.display_name
        residence = person.residence
        source_ehraid = person.source_company_ehraid
        source_name = person.source_company_name

        links = list(
            (
                await session.execute(
                    select(PersonCompanyLink).where(PersonCompanyLink.person_id == person_id)
                )
            ).scalars().all()
        )
        seed_only_like = (not links) or all(
            l.is_seed_company or l.relation_type == "seed" for l in links
        )
        if include_shab and seed_only_like and len(months) <= 4:
            today = date.today()
            start = today.replace(year=today.year - 3)
            scan_row.last_scanned_month = f"{start.year:04d}-{start.month:02d}"
            last_month = scan_row.last_scanned_month
            months = _months_from(last_month, include_overlap=2)
            years_back = max(1, len(months) // 12 + 1)

        seed_uid = None
        for link in links:
            if link.is_seed_company or link.relation_type == "seed":
                if link.company_uid:
                    seed_uid = link.company_uid
                    break
        if not seed_uid:
            for link in links:
                if link.company_uid:
                    seed_uid = link.company_uid
                    break

        await session.commit()

    # ── 1) Primary: Moneyhouse person search → Zefix firm resolve ─────────
    # Seed firm disambiguates common names (profile must list seed when possible).
    mh = await asyncio.to_thread(
        search_person_mandates,
        display_name,
        residence=residence,
        seed_company=source_name,
        seed_uid=seed_uid,
    )
    matches: list[dict[str, Any]] = []
    zefix_resolved = 0
    zefix_failed: list[str] = []

    for company in mh.get("companies") or []:
        cname = (company.get("name") or "").strip()
        if not cname:
            continue
        try:
            detail = await resolve_company_detail(cname, None)
        except Exception as e:
            logger.info("Zefix resolve failed for Moneyhouse firm %r: %s", cname, e)
            zefix_failed.append(cname)
            matches.append(
                {
                    "name": cname,
                    "uid": None,
                    "ehraid": None,
                    "role_hint": None,
                    "sogc_date": company.get("from"),
                    "snippet": "Moneyhouse-Mandat (Zefix-Auflösung ausstehend)",
                    "person_name": (mh.get("matched_person") or {}).get("name"),
                    "source": "moneyhouse",
                }
            )
            continue
        zefix_resolved += 1
        matches.append(
            {
                "name": detail.get("name") or cname,
                "uid": format_company_uid(detail) or detail.get("uid"),
                "ehraid": detail.get("ehraid"),
                "role_hint": None,
                "sogc_date": company.get("from") or detail.get("sogcDate"),
                "snippet": f"Moneyhouse-Mandat · Zefix {detail.get('status') or ''}".strip(),
                "person_name": (mh.get("matched_person") or {}).get("name"),
                "legal_seat": detail.get("legalSeat"),
                "source": "moneyhouse+zefix",
            }
        )

    # ── 2) Optional SHAB supplement (slow; off by default for watchlist UI) ─
    shab_result: dict[str, Any] | None = None
    if include_shab:
        scope = await _resolve_watch_shab_scope(
            canton_override=canton,
            source_company_ehraid=source_ehraid,
            source_company_name=source_name,
            seed_uid=seed_uid,
        )
        registry_office_id = scope.get("registry_office_id")
        canton_code = scope.get("canton")
        has_cantonal = bool(registry_office_id or canton_code)
        if has_cantonal:
            years_back = min(years_back, 12)
            max_seconds = 70.0
        else:
            years_back = min(years_back, 3)
            max_seconds = 60.0
        try:
            shab_result = await search_person_in_sogc(
                display_name,
                exclude_uid=scope.get("exclude_uid"),
                registry_office_id=registry_office_id,
                canton=canton_code,
                all_cantons=False,
                years_back=years_back,
                max_seconds=max_seconds,
                deep=False,
            )
            month_set = {f"{y:04d}-{m:02d}" for y, m in months}
            known_keys = {
                re.sub(r"\D", "", str(m.get("uid") or ""))
                or str(m.get("ehraid") or m.get("name") or "").lower()
                for m in matches
            }
            for hit in shab_result.get("matches") or []:
                sogc = (hit.get("sogc_date") or "")[:7]
                if month_set and sogc and sogc not in month_set:
                    continue
                key = (
                    re.sub(r"\D", "", str(hit.get("uid") or ""))
                    or str(hit.get("ehraid") or hit.get("name") or "").lower()
                )
                if key in known_keys:
                    continue
                known_keys.add(key)
                hit = dict(hit)
                hit["source"] = "shab"
                matches.append(hit)
        except Exception as e:
            logger.warning("Optional SHAB supplement failed for %s: %s", display_name, e)
            shab_result = {"error": str(e)}

    new_links = 0
    alerts_created = 0
    created_alerts: list[dict[str, Any]] = []

    async with async_session() as session:
        person = await session.get(WatchedPerson, person_id)
        scan_row = await _get_or_create_scan_row(session, person_id)
        existing_links = list(
            (
                await session.execute(
                    select(PersonCompanyLink).where(PersonCompanyLink.person_id == person_id)
                )
            ).scalars().all()
        )
        known_ehraids = {
            link.company_ehraid for link in existing_links if link.company_ehraid
        }
        known_names = {(link.company_name or "").lower() for link in existing_links}

        for hit in matches:
            ehraid = hit.get("ehraid")
            ehraid_i = int(ehraid) if ehraid else None
            name = hit.get("name") or "Unbekannt"
            if ehraid_i and ehraid_i in known_ehraids:
                continue
            if not ehraid_i and name.lower() in known_names:
                continue

            confidence = estimate_match_confidence(watched=person, hit=hit)
            if (hit.get("source") or "").startswith("moneyhouse"):
                confidence = "high" if ehraid_i else "medium"
            role = hit.get("role_hint")
            link = PersonCompanyLink(
                person_id=person_id,
                company_ehraid=ehraid_i,
                company_name=name,
                company_uid=hit.get("uid") if isinstance(hit.get("uid"), str) else (
                    str(hit.get("uid")) if hit.get("uid") else None
                ),
                role=role,
                relation_type="newly_found",
                is_seed_company=False,
                first_detected_at=datetime.now(timezone.utc),
                match_confidence=confidence,
            )
            session.add(link)
            new_links += 1
            if ehraid_i:
                known_ehraids.add(ehraid_i)
            known_names.add(name.lower())

            alert_type, severity = _alert_type_for_role(role)
            if confidence == "low":
                severity = "low"
            src = hit.get("source") or "scan"
            msg = (
                f"«{person.display_name}» neu verknüpft mit «{name}»"
                + (f" ({role})" if role else "")
                + f" — Konfidenz: {confidence} · Quelle: {src}"
            )
            alert = NetworkAlert(
                alert_type=alert_type,
                person_id=person_id,
                company_ehraid=ehraid_i,
                company_name=name,
                severity=severity,
                message=msg,
                created_at=datetime.now(timezone.utc),
                acknowledged=False,
            )
            session.add(alert)
            alerts_created += 1
            created_alerts.append({
                "alert_type": alert_type,
                "severity": severity,
                "company_name": name,
                "confidence": confidence,
                "source": src,
            })

        today = date.today()
        # Moneyhouse path is complete for mandate discovery; advance cursor.
        # If SHAB supplement was incomplete, keep cursor for retry.
        shab_incomplete = bool(
            include_shab and shab_result and shab_result.get("search_complete") is False
        )
        if not shab_incomplete:
            scan_row.last_scanned_month = f"{today.year:04d}-{today.month:02d}"
        scan_row.last_run_at = datetime.now(timezone.utc)
        await session.commit()

    matched = mh.get("matched_person") or {}
    return {
        "person_id": person_id,
        "display_name": display_name,
        "months_considered": len(months) if include_shab else 0,
        "raw_matches": len(matches),
        "new_links": new_links,
        "alerts": alerts_created,
        "created_alerts": created_alerts,
        "method": "moneyhouse_person+zefix",
        "moneyhouse": {
            "matched_person": matched.get("name"),
            "residence": matched.get("residence"),
            "companies_found": len(mh.get("companies") or []),
            "candidates": mh.get("candidates"),
            "note": mh.get("note"),
            "enabled": mh.get("enabled"),
        },
        "zefix_resolved": zefix_resolved,
        "zefix_failed": zefix_failed,
        "include_shab": include_shab,
        "search_elapsed": (shab_result or {}).get("elapsed_seconds"),
        "search_complete": not shab_incomplete,
        "registry_scope": (shab_result or {}).get("registry_scope") or "Moneyhouse→Zefix",
        "canton": canton,
        "note": mh.get("note") or (shab_result or {}).get("note"),
        "nationwide": False,
    }


async def run_person_monitoring(*, limit: int = 20) -> dict[str, Any]:
    """Daily job: scan active watched persons with light concurrency."""
    async with async_session() as session:
        result = await session.execute(
            select(WatchedPerson)
            .where(WatchedPerson.status.in_(("active", "confirmed_fraud")))
            .order_by(WatchedPerson.added_at.asc())
            .limit(limit)
        )
        people = list(result.scalars().all())
        ids = [p.id for p in people]

    sem = asyncio.Semaphore(2)
    outcomes: list[dict[str, Any]] = []

    async def _one(pid: int) -> None:
        async with sem:
            try:
                outcomes.append(await scan_watched_person_incremental(pid))
            except Exception as e:
                logger.exception("Watch scan failed for %s", pid)
                outcomes.append({"person_id": pid, "error": str(e)})

    await asyncio.gather(*[_one(pid) for pid in ids])
    return {
        "scanned": len(ids),
        "results": outcomes,
        "new_links": sum(o.get("new_links") or 0 for o in outcomes),
        "alerts": sum(o.get("alerts") or 0 for o in outcomes),
    }


async def list_network_alerts(
    *,
    acknowledged: bool | None = False,
    severity: str | None = None,
    since: str | None = None,
    person_id: int | None = None,
    limit: int = 100,
) -> list[dict]:
    async with async_session() as session:
        q = select(NetworkAlert).order_by(NetworkAlert.created_at.desc()).limit(limit)
        if acknowledged is not None:
            q = q.where(NetworkAlert.acknowledged.is_(acknowledged))
        if severity:
            q = q.where(NetworkAlert.severity == severity)
        if person_id is not None:
            q = q.where(NetworkAlert.person_id == person_id)
        rows = list((await session.execute(q)).scalars().all())

        person_ids = {a.person_id for a in rows if a.person_id}
        people_by_id: dict[int, WatchedPerson] = {}
        if person_ids:
            people = list(
                (
                    await session.execute(
                        select(WatchedPerson).where(WatchedPerson.id.in_(person_ids))
                    )
                ).scalars().all()
            )
            people_by_id = {p.id: p for p in people}

        out = []
        for a in rows:
            if since:
                try:
                    since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
                    if a.created_at and a.created_at < since_dt:
                        continue
                except ValueError:
                    pass
            person = people_by_id.get(a.person_id) if a.person_id else None
            out.append({
                "id": a.id,
                "alert_type": a.alert_type,
                "person_id": a.person_id,
                "person_name": person.display_name if person else None,
                "company_ehraid": a.company_ehraid,
                "company_name": a.company_name,
                "source_company_name": person.source_company_name if person else None,
                "severity": a.severity,
                "message": a.message,
                "created_at": a.created_at.isoformat() if a.created_at else None,
                "acknowledged": a.acknowledged,
                "acknowledged_by": a.acknowledged_by,
                "acknowledged_at": a.acknowledged_at.isoformat() if a.acknowledged_at else None,
                "deepen_url": (
                    f"/?company={quote(a.company_name)}" if a.company_name else None
                ),
            })
        return out


async def acknowledge_alert(alert_id: int, *, by: str = "analyst") -> dict:
    async with async_session() as session:
        alert = await session.get(NetworkAlert, alert_id)
        if not alert:
            raise LookupError("Alert nicht gefunden")
        alert.acknowledged = True
        alert.acknowledged_by = by
        alert.acknowledged_at = datetime.now(timezone.utc)
        await session.commit()
        return {"id": alert.id, "acknowledged": True}


_DEFAULT_LIST_STATUSES = ("active", "confirmed_fraud")
_SOURCE_REASON_PRIORITY = {
    "shell_takeover_pattern": 40,
    "fraud_list_officer": 25,
    "manual": 10,
}
_SEVERITY_SCORE = {"high": 30, "medium": 15, "low": 5}
_INTERMEDIARY_ROLE_HINTS = (
    "revisionsstelle",
    "revis",
    "treuhänd",
    "treuhand",
    "fiducia",
    "verwaltungsrat",
    "sekretär",
    "domizil",
)
_INTERMEDIARY_LINK_THRESHOLD = 8


def _normalize_status_filter(status: str | list[str] | None) -> list[str] | None:
    """None → default active statuses; empty list → all; otherwise explicit list."""
    if status is None:
        return list(_DEFAULT_LIST_STATUSES)
    if isinstance(status, str):
        parts = [s.strip() for s in status.split(",") if s.strip()]
        return parts or None
    parts = [str(s).strip() for s in status if str(s).strip()]
    return parts or None


def _link_dict(link: PersonCompanyLink) -> dict[str, Any]:
    return {
        "id": link.id,
        "name": link.company_name,
        "uid": link.company_uid,
        "ehraid": link.company_ehraid,
        "role": link.role,
        "relation_type": link.relation_type,
        "is_seed_company": link.is_seed_company,
        "match_confidence": link.match_confidence,
        "first_detected_at": link.first_detected_at.isoformat() if link.first_detected_at else None,
    }


def estimate_probable_intermediary(links: list[PersonCompanyLink]) -> bool:
    """Many Sammelmandat-style roles → likely intermediary noise, not primary fraud actor."""
    if len(links) < _INTERMEDIARY_LINK_THRESHOLD:
        return False
    role_hits = 0
    for link in links:
        role = (link.role or "").lower()
        if any(h in role for h in _INTERMEDIARY_ROLE_HINTS):
            role_hits += 1
    return role_hits >= max(3, len(links) // 2)


def compute_person_priority_score(
    *,
    source_reason: str | None,
    open_alert_severities: list[str],
    newly_found_count: int,
    probable_intermediary: bool,
) -> int:
    score = _SOURCE_REASON_PRIORITY.get(source_reason or "", 5)
    for sev in open_alert_severities:
        score += _SEVERITY_SCORE.get(sev, 0)
    score += newly_found_count * 5
    if probable_intermediary:
        score -= 50
    return score


async def list_watched_persons(
    *,
    status: str | list[str] | None = None,
    q: str | None = None,
    source_reason: str | None = None,
    has_open_alert: bool | None = None,
    sort: str = "priority",
    limit: int = 50,
    offset: int = 0,
    include_companies: bool = False,
) -> dict[str, Any]:
    statuses = _normalize_status_filter(status)
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))
    q_norm = (q or "").strip().lower()

    async with async_session() as session:
        people_q = select(WatchedPerson)
        if statuses is not None:
            people_q = people_q.where(WatchedPerson.status.in_(statuses))
        if source_reason:
            people_q = people_q.where(WatchedPerson.source_reason == source_reason)
        if q_norm:
            # Push name filter into SQL where possible (SQLite lower)
            people_q = people_q.where(
                or_(
                    WatchedPerson.display_name.ilike(f"%{q_norm}%"),
                    WatchedPerson.residence.ilike(f"%{q_norm}%"),
                )
            )
        people = list((await session.execute(people_q)).scalars().all())
        if not people:
            return {"items": [], "total": 0, "limit": limit, "offset": offset}

        person_ids = [p.id for p in people]

        alert_rows = list(
            (
                await session.execute(
                    select(NetworkAlert).where(
                        NetworkAlert.acknowledged.is_(False),
                        NetworkAlert.person_id.in_(person_ids),
                    )
                )
            ).scalars().all()
        )
        alerts_by_person: dict[int, list[NetworkAlert]] = {}
        for a in alert_rows:
            if a.person_id:
                alerts_by_person.setdefault(a.person_id, []).append(a)

        # One batch query for all company links (avoids N+1)
        all_links = list(
            (
                await session.execute(
                    select(PersonCompanyLink).where(PersonCompanyLink.person_id.in_(person_ids))
                )
            ).scalars().all()
        )
        links_by_person: dict[int, list[PersonCompanyLink]] = {}
        for link in all_links:
            links_by_person.setdefault(link.person_id, []).append(link)

        # Link to CompanyCase (process rule C: watch without case is OK but visible)
        from app.hr_network.company_cases import ACTIVE_FRAUD_STATUSES, OPEN_STATUSES

        linked_statuses = tuple(set(OPEN_STATUSES + ACTIVE_FRAUD_STATUSES))
        firm_cases = list(
            (
                await session.execute(
                    select(CompanyCase).where(CompanyCase.status.in_(linked_statuses))
                )
            ).scalars().all()
        )
        case_by_id = {c.id: c for c in firm_cases}
        case_by_ehraid = {c.company_ehraid: c for c in firm_cases if c.company_ehraid}
        case_by_name = {
            (c.company_name or "").strip().lower(): c
            for c in firm_cases
            if (c.company_name or "").strip()
        }
        person_id_to_case_id: dict[int, int] = {}
        if firm_cases:
            check_rows = list(
                (
                    await session.execute(
                        select(CaseBankCheckItem).where(
                            CaseBankCheckItem.case_id.in_([c.id for c in firm_cases]),
                            CaseBankCheckItem.entity_type == "person",
                        )
                    )
                ).scalars().all()
            )
            for row in check_rows:
                ref = (row.entity_ref or "").strip()
                if ref.isdigit():
                    person_id_to_case_id.setdefault(int(ref), row.case_id)

        enriched: list[dict[str, Any]] = []
        for p in people:
            person_alerts = alerts_by_person.get(p.id, [])
            if has_open_alert is True and not person_alerts:
                continue
            if has_open_alert is False and person_alerts:
                continue

            links = links_by_person.get(p.id, [])
            newly_found = sum(1 for l in links if l.relation_type == "newly_found")
            intermediary = estimate_probable_intermediary(links)
            severities = [a.severity for a in person_alerts]
            priority = compute_person_priority_score(
                source_reason=p.source_reason,
                open_alert_severities=severities,
                newly_found_count=newly_found,
                probable_intermediary=intermediary,
            )
            last_activity = p.added_at
            for a in person_alerts:
                if a.created_at and (last_activity is None or a.created_at > last_activity):
                    last_activity = a.created_at
            for l in links:
                if l.first_detected_at and (last_activity is None or l.first_detected_at > last_activity):
                    last_activity = l.first_detected_at

            linked_case = None
            if p.source_company_ehraid and p.source_company_ehraid in case_by_ehraid:
                linked_case = case_by_ehraid[p.source_company_ehraid]
            else:
                src_name = (p.source_company_name or "").strip().lower()
                if src_name and src_name in case_by_name:
                    linked_case = case_by_name[src_name]
            if linked_case is None and p.id in person_id_to_case_id:
                linked_case = case_by_id.get(person_id_to_case_id[p.id])

            item: dict[str, Any] = {
                "id": p.id,
                "person_slug": p.person_slug,
                "display_name": p.display_name,
                "residence": p.residence,
                "source_company_ehraid": p.source_company_ehraid,
                "source_company_name": p.source_company_name,
                "source_reason": p.source_reason,
                "status": p.status,
                "flag_undesired_customer": bool(getattr(p, "flag_undesired_customer", False)),
                "flag_aml": bool(getattr(p, "flag_aml", False)),
                "added_at": p.added_at.isoformat() if p.added_at else None,
                "notes": p.notes,
                "company_count": len(links),
                "newly_found_count": newly_found,
                "open_alert_count": len(person_alerts),
                "priority_score": priority,
                "probable_intermediary": intermediary,
                "last_activity_at": last_activity.isoformat() if last_activity else None,
                "has_company_case": linked_case is not None,
                "linked_case_id": linked_case.id if linked_case else None,
                "linked_case_status": linked_case.status if linked_case else None,
            }
            if include_companies:
                item["companies"] = [_link_dict(l) for l in links]
            enriched.append(item)

        if sort == "added_at":
            enriched.sort(key=lambda x: x.get("added_at") or "", reverse=True)
        else:
            enriched.sort(
                key=lambda x: (
                    0 if x.get("probable_intermediary") else 1,
                    x.get("priority_score") or 0,
                    x.get("last_activity_at") or "",
                ),
                reverse=True,
            )

        total = len(enriched)
        page = enriched[offset : offset + limit]
        return {"items": page, "total": total, "limit": limit, "offset": offset}


async def list_watched_person_cases(
    *,
    status: str | list[str] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Group watched persons by source company (investigative case view)."""
    listed = await list_watched_persons(
        status=status,
        sort="priority",
        limit=5000,
        offset=0,
        include_companies=False,
    )
    groups: dict[str, dict[str, Any]] = {}
    for person in listed["items"]:
        ehraid = person.get("source_company_ehraid")
        name = (person.get("source_company_name") or "").strip()
        if ehraid:
            key = f"ehraid:{ehraid}"
            label = name or f"EHRAID {ehraid}"
        elif name:
            key = f"name:{name.lower()}"
            label = name
        else:
            key = "ungrouped"
            label = "Ohne Ursprungsfirma"

        if key not in groups:
            groups[key] = {
                "case_key": key,
                "source_company_name": label if key != "ungrouped" else None,
                "source_company_ehraid": ehraid,
                "person_count": 0,
                "open_alerts": 0,
                "priority_score": 0,
                "persons": [],
            }
        g = groups[key]
        g["person_count"] += 1
        g["open_alerts"] += int(person.get("open_alert_count") or 0)
        g["priority_score"] = max(g["priority_score"], int(person.get("priority_score") or 0))
        g["persons"].append(person)

    cases = sorted(
        groups.values(),
        key=lambda c: (c["open_alerts"], c["priority_score"]),
        reverse=True,
    )
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))
    return {
        "cases": cases[offset : offset + limit],
        "total": len(cases),
        "limit": limit,
        "offset": offset,
    }


async def get_watched_person_dossier(person_id: int) -> dict[str, Any]:
    """Full person record: profile, links, alerts, status history."""
    async with async_session() as session:
        person = await session.get(WatchedPerson, person_id)
        if not person:
            raise LookupError("Person nicht gefunden")
        links = list(
            (
                await session.execute(
                    select(PersonCompanyLink).where(PersonCompanyLink.person_id == person_id)
                )
            ).scalars().all()
        )
        history = list(
            (
                await session.execute(
                    select(WatchedPersonStatusHistory)
                    .where(WatchedPersonStatusHistory.person_id == person_id)
                    .order_by(WatchedPersonStatusHistory.changed_at.desc())
                )
            ).scalars().all()
        )
        base = {
            "id": person.id,
            "person_slug": person.person_slug,
            "display_name": person.display_name,
            "residence": person.residence,
            "source_company_ehraid": person.source_company_ehraid,
            "source_company_name": person.source_company_name,
            "source_reason": person.source_reason,
            "status": person.status,
            "flag_undesired_customer": bool(getattr(person, "flag_undesired_customer", False)),
            "flag_aml": bool(getattr(person, "flag_aml", False)),
            "added_at": person.added_at.isoformat() if person.added_at else None,
            "notes": person.notes,
            "case_notes": person.case_notes,
            "companies": [_link_dict(l) for l in links],
            "seed_only": bool(links) and all(
                l.is_seed_company or l.relation_type == "seed" for l in links
            ),
            "probable_intermediary": estimate_probable_intermediary(links),
            "source_company_url": (
                f"/?company={quote(person.source_company_name)}"
                if person.source_company_name
                else None
            ),
            "status_history": [
                {
                    "id": r.id,
                    "old_status": r.old_status,
                    "new_status": r.new_status,
                    "reason": r.reason,
                    "changed_by": r.changed_by,
                    "changed_at": r.changed_at.isoformat() if r.changed_at else None,
                }
                for r in history
            ],
        }
        linked = await _linked_company_case_for_person(session, person)
        base["has_company_case"] = linked is not None
        base["linked_case_id"] = linked.id if linked else None
        base["linked_case_status"] = linked.status if linked else None

    alerts = await list_network_alerts(person_id=person_id, acknowledged=None, limit=200)
    base["alerts"] = alerts
    base["open_alert_count"] = sum(1 for a in alerts if not a.get("acknowledged"))
    return base


async def _linked_company_case_for_person(session, person: WatchedPerson) -> CompanyCase | None:
    """Resolve open/active CompanyCase for a watched person (if any)."""
    from app.hr_network.company_cases import ACTIVE_FRAUD_STATUSES, OPEN_STATUSES

    linked_statuses = tuple(set(OPEN_STATUSES + ACTIVE_FRAUD_STATUSES))
    cases = list(
        (
            await session.execute(
                select(CompanyCase).where(CompanyCase.status.in_(linked_statuses))
            )
        ).scalars().all()
    )
    if person.source_company_ehraid:
        for c in cases:
            if c.company_ehraid == person.source_company_ehraid:
                return c
    src = (person.source_company_name or "").strip().lower()
    if src:
        for c in cases:
            if (c.company_name or "").strip().lower() == src:
                return c
    if cases:
        hit = (
            await session.execute(
                select(CaseBankCheckItem).where(
                    CaseBankCheckItem.case_id.in_([c.id for c in cases]),
                    CaseBankCheckItem.entity_type == "person",
                    CaseBankCheckItem.entity_ref == str(person.id),
                ).limit(1)
            )
        ).scalar_one_or_none()
        if hit:
            for c in cases:
                if c.id == hit.case_id:
                    return c
    return None


async def list_watchlist_inbox(*, limit: int = 100) -> dict[str, Any]:
    """Triage inbox: open alerts → jump into person dossier."""
    alerts = await list_network_alerts(acknowledged=False, limit=200)
    items: list[dict[str, Any]] = []
    for a in alerts:
        sev = a.get("severity") or "low"
        items.append({
            "kind": "alert",
            "priority": _SEVERITY_SCORE.get(sev, 5),
            "created_at": a.get("created_at"),
            "person_id": a.get("person_id"),
            "payload": a,
        })
    items.sort(key=lambda x: (x["priority"], x.get("created_at") or ""), reverse=True)
    limit = max(1, min(int(limit or 100), 300))
    return {
        "items": items[:limit],
        "total": len(items),
        "alert_count": len(alerts),
        "hint_count": 0,
    }


async def add_watched_person_manual(
    *,
    display_name: str,
    residence: str | None = None,
    notes: str | None = None,
) -> dict:
    from app.hr_network.shab_parser import _normalize_person_id

    slug = _normalize_person_id(display_name)
    async with async_session() as session:
        existing = (
            await session.execute(select(WatchedPerson).where(WatchedPerson.person_slug == slug))
        ).scalar_one_or_none()
        if existing:
            return {"id": existing.id, "already_existed": True, "display_name": existing.display_name}
        person = WatchedPerson(
            person_slug=slug,
            display_name=display_name.strip(),
            residence=residence,
            source_reason="manual",
            status="active",
            notes=notes,
            added_at=datetime.now(timezone.utc),
        )
        session.add(person)
        await session.commit()
        await session.refresh(person)
        return {"id": person.id, "already_existed": False, "display_name": person.display_name}


async def update_watched_person_case_notes(person_id: int, case_notes: str) -> dict[str, Any]:
    async with async_session() as session:
        person = await session.get(WatchedPerson, person_id)
        if not person:
            raise LookupError("Person nicht gefunden")
        person.case_notes = (case_notes or "")[:4000] or None
        await session.commit()
        return {"id": person.id, "case_notes": person.case_notes}


async def update_watched_person_flags(
    person_id: int,
    *,
    flag_undesired_customer: bool | None = None,
    flag_aml: bool | None = None,
) -> dict[str, Any]:
    async with async_session() as session:
        person = await session.get(WatchedPerson, person_id)
        if not person:
            raise LookupError("Person nicht gefunden")
        if flag_undesired_customer is not None:
            person.flag_undesired_customer = bool(flag_undesired_customer)
        if flag_aml is not None:
            person.flag_aml = bool(flag_aml)
        await session.commit()
        return {
            "id": person.id,
            "flag_undesired_customer": bool(person.flag_undesired_customer),
            "flag_aml": bool(person.flag_aml),
        }


async def update_watched_person_status(
    person_id: int,
    status: str,
    *,
    reason: str,
    changed_by: str,
) -> dict:
    allowed = {"active", "low_priority", "cleared", "confirmed_fraud"}
    if status not in allowed:
        raise ValueError(f"Ungültiger Status: {status}")
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise ValueError("Begründung ist Pflicht (mind. 3 Zeichen)")
    async with async_session() as session:
        person = await session.get(WatchedPerson, person_id)
        if not person:
            raise LookupError("Person nicht gefunden")
        old = person.status
        if old == status:
            return {"id": person.id, "status": person.status, "unchanged": True}
        person.status = status
        session.add(
            WatchedPersonStatusHistory(
                person_id=person.id,
                old_status=old,
                new_status=status,
                reason=reason[:1024],
                changed_by=changed_by,
                changed_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()
        return {"id": person.id, "status": person.status, "old_status": old}


async def merge_watched_persons(
    *,
    canonical_id: int,
    duplicate_id: int,
    changed_by: str,
    reason: str = "Merge: dieselbe Person",
) -> dict:
    if canonical_id == duplicate_id:
        raise ValueError("IDs müssen unterschiedlich sein")
    async with async_session() as session:
        canonical = await session.get(WatchedPerson, canonical_id)
        duplicate = await session.get(WatchedPerson, duplicate_id)
        if not canonical or not duplicate:
            raise LookupError("Person nicht gefunden")
        links = list(
            (
                await session.execute(
                    select(PersonCompanyLink).where(PersonCompanyLink.person_id == duplicate.id)
                )
            ).scalars().all()
        )
        moved = 0
        for link in links:
            # Avoid duplicate company links on canonical
            exists = (
                await session.execute(
                    select(PersonCompanyLink).where(
                        PersonCompanyLink.person_id == canonical.id,
                        PersonCompanyLink.company_ehraid == link.company_ehraid,
                        PersonCompanyLink.company_name == link.company_name,
                    )
                )
            ).scalar_one_or_none()
            if exists:
                await session.delete(link)
            else:
                link.person_id = canonical.id
                moved += 1
        old = duplicate.status
        duplicate.status = "cleared"
        duplicate.notes = (
            (duplicate.notes or "")
            + f"\n[merged→#{canonical.id} by {changed_by}]"
        ).strip()
        session.add(
            WatchedPersonStatusHistory(
                person_id=duplicate.id,
                old_status=old,
                new_status="cleared",
                reason=(reason or "Merge")[:1024],
                changed_by=changed_by,
                changed_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()
        return {
            "canonical_id": canonical.id,
            "duplicate_id": duplicate.id,
            "links_moved": moved,
        }


async def delete_watched_persons(person_ids: list[int]) -> dict[str, Any]:
    """Permanently remove watched persons and related rows."""
    ids = sorted({int(i) for i in person_ids if i is not None})
    if not ids:
        raise ValueError("Keine Personen-IDs angegeben")
    if len(ids) > 100:
        raise ValueError("Maximal 100 Personen auf einmal löschen")

    async with async_session() as session:
        existing = list(
            (
                await session.execute(select(WatchedPerson).where(WatchedPerson.id.in_(ids)))
            ).scalars().all()
        )
        found_ids = [p.id for p in existing]
        if not found_ids:
            raise LookupError("Keine der Personen gefunden")

        alert_ids = list(
            (
                await session.execute(
                    select(NetworkAlert.id).where(NetworkAlert.person_id.in_(found_ids))
                )
            ).scalars().all()
        )
        if alert_ids:
            from app.database import CompanyCase
            from sqlalchemy import update

            await session.execute(
                update(CompanyCase)
                .where(CompanyCase.source_alert_id.in_(alert_ids))
                .values(source_alert_id=None)
            )

        await session.execute(
            delete(NetworkAlert).where(NetworkAlert.person_id.in_(found_ids))
        )
        await session.execute(
            delete(WatchedPersonStatusHistory).where(
                WatchedPersonStatusHistory.person_id.in_(found_ids)
            )
        )
        await session.execute(
            delete(PersonCompanyLink).where(PersonCompanyLink.person_id.in_(found_ids))
        )
        await session.execute(
            delete(PersonWatchScan).where(PersonWatchScan.person_id.in_(found_ids))
        )
        await session.execute(delete(WatchedPerson).where(WatchedPerson.id.in_(found_ids)))
        await session.commit()
        return {
            "deleted": found_ids,
            "deleted_count": len(found_ids),
            "missing": [i for i in ids if i not in found_ids],
        }


async def list_status_history(person_id: int) -> list[dict]:
    async with async_session() as session:
        rows = list(
            (
                await session.execute(
                    select(WatchedPersonStatusHistory)
                    .where(WatchedPersonStatusHistory.person_id == person_id)
                    .order_by(WatchedPersonStatusHistory.changed_at.desc())
                )
            ).scalars().all()
        )
        return [
            {
                "id": r.id,
                "old_status": r.old_status,
                "new_status": r.new_status,
                "reason": r.reason,
                "changed_by": r.changed_by,
                "changed_at": r.changed_at.isoformat() if r.changed_at else None,
            }
            for r in rows
        ]


async def match_company_against_watchlist(
    *,
    ehraid: int | None,
    uid: str | None,
    officer_slugs: list[str],
) -> dict[str, Any]:
    """Used by Zefix check / scoring: does this company touch the watchlist?"""
    hits: list[dict[str, Any]] = []
    async with async_session() as session:
        if ehraid:
            links = list(
                (
                    await session.execute(
                        select(PersonCompanyLink).where(PersonCompanyLink.company_ehraid == int(ehraid))
                    )
                ).scalars().all()
            )
            for link in links:
                person = await session.get(WatchedPerson, link.person_id)
                if not person or person.status == "cleared":
                    continue
                hits.append({
                    "match_via": "company_link",
                    "person_id": person.id,
                    "person_slug": person.person_slug,
                    "display_name": person.display_name,
                    "source_company_name": person.source_company_name,
                    "source_reason": person.source_reason,
                    "status": person.status,
                })

        if officer_slugs:
            people = list(
                (
                    await session.execute(
                        select(WatchedPerson).where(
                            WatchedPerson.person_slug.in_(officer_slugs),
                            WatchedPerson.status != "cleared",
                        )
                    )
                ).scalars().all()
            )
            seen = {h["person_slug"] for h in hits}
            for person in people:
                if person.person_slug in seen:
                    continue
                hits.append({
                    "match_via": "officer_slug",
                    "person_id": person.id,
                    "person_slug": person.person_slug,
                    "display_name": person.display_name,
                    "source_company_name": person.source_company_name,
                    "source_reason": person.source_reason,
                    "status": person.status,
                })

    return {
        "matched": bool(hits),
        "hits": hits,
        "hit_count": len(hits),
    }
