"""CompanyCase lifecycle — single firm dossier from review through reporting."""

from __future__ import annotations

import logging
import os
import re
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import delete, func, select

import config
from app.case_report import build_company_case_report
from app.database import (
    CaseBankCheckItem,
    CaseJournalEntry,
    CompanyCase,
    NetworkAlert,
    WatchedPerson,
    async_session,
)

logger = logging.getLogger(__name__)

FRAUD_TYPES = (
    "investment_scam",
    "fake_bank_employee",
    "romance_scam",
    "other",
)

ACTIVE_FRAUD_STATUSES = (
    "confirmed_fraud",
    "ready_for_report",
    "reported",
    "closed",
)

OPEN_STATUSES = (
    "under_review",
    "confirmed_fraud",
    "ready_for_report",
    "reported",
)


def _reports_dir() -> str:
    base = os.path.dirname(os.path.abspath(config.DATABASE_PATH))
    path = os.path.join(base, "case_reports")
    os.makedirs(path, exist_ok=True)
    return path


def _purpose_key(purpose: str | None) -> str:
    if not purpose:
        return ""
    text = re.sub(r"<[^>]+>", " ", purpose)
    text = re.sub(r"\s+", " ", text).strip().lower()
    # Strip common Swiss registry boilerplate so keys reflect the real activity
    for _ in range(4):
        nxt = text
        nxt = re.sub(r"^die\s+gesellschaft\s+bezweckt\s+(die\s+)?", "", nxt)
        nxt = re.sub(r"^zweck\s+(der\s+gesellschaft\s+)?(ist|sind)\s+(die\s+)?", "", nxt)
        nxt = re.sub(r"^erbringung\s+von\s+", "", nxt)
        nxt = re.sub(r"^leistungen?\s+im\s+bereich\s+(der|des|von)\s+", "", nxt)
        nxt = re.sub(r"^leistungen?\s+aller\s+art,?\s*(insbesondere\s+)?", "", nxt)
        nxt = re.sub(r"^im\s+bereich\s+(der|des|von)\s+", "", nxt)
        nxt = nxt.strip()
        if nxt == text:
            break
        text = nxt
    return text[:120]


def _case_dict(
    case: CompanyCase,
    *,
    journal: list[dict] | None = None,
    bank_checks: list[dict] | None = None,
) -> dict[str, Any]:
    checks = bank_checks or []
    pending = sum(1 for c in checks if c.get("status") == "pending")
    total = len(checks)
    payment_done = case.payment_blocked is not None
    documentation_complete = total > 0 and pending == 0 and payment_done
    if total == 0 and case.status in ("confirmed_fraud", "ready_for_report", "reported", "closed"):
        documentation_complete = False
    return {
        "id": case.id,
        "company_ehraid": case.company_ehraid,
        "company_name": case.company_name,
        "company_uid": case.company_uid,
        "company_purpose": case.company_purpose,
        "fraud_type": case.fraud_type,
        "status": case.status,
        "opened_by": case.opened_by,
        "opened_at": case.opened_at.isoformat() if case.opened_at else None,
        "confirmed_at": case.confirmed_at.isoformat() if case.confirmed_at else None,
        "payment_blocked": case.payment_blocked,
        "payment_blocked_note": case.payment_blocked_note,
        "hit_amount": case.hit_amount,
        "hit_currency": case.hit_currency,
        "hit_reference": case.hit_reference,
        "hit_note": case.hit_note,
        "report_path": case.report_path,
        "has_report": bool(case.report_path and os.path.isfile(case.report_path or "")),
        "reported_at": case.reported_at.isoformat() if case.reported_at else None,
        "reported_by": case.reported_by,
        "compliance_actioned_by": case.compliance_actioned_by,
        "compliance_actioned_at": (
            case.compliance_actioned_at.isoformat() if case.compliance_actioned_at else None
        ),
        "compliance_note": case.compliance_note,
        "source_alert_id": case.source_alert_id,
        "journal": journal or [],
        "bank_checks": checks,
        "bank_checks_total": total,
        "bank_checks_pending": pending,
        "bank_checks_done": total - pending,
        "documentation_complete": documentation_complete,
    }


async def list_company_cases(
    *,
    status: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    async with async_session() as session:
        q = select(CompanyCase).order_by(CompanyCase.opened_at.desc()).limit(limit)
        if status:
            statuses = [s.strip() for s in status.split(",") if s.strip()]
            if len(statuses) == 1:
                q = q.where(CompanyCase.status == statuses[0])
            elif statuses:
                q = q.where(CompanyCase.status.in_(statuses))
        cases = list((await session.execute(q)).scalars().all())
        out = []
        for case in cases:
            checks = await _load_checks(session, case.id)
            out.append(_case_dict(case, bank_checks=checks))
        return out


async def get_company_case(case_id: int) -> dict[str, Any]:
    async with async_session() as session:
        case = await session.get(CompanyCase, case_id)
        if not case:
            raise LookupError("Fall nicht gefunden")
        journal = await _load_journal(session, case.id)
        checks = await _load_checks(session, case.id)
        return _case_dict(case, journal=journal, bank_checks=checks)


async def _load_journal(session, case_id: int) -> list[dict]:
    rows = list(
        (
            await session.execute(
                select(CaseJournalEntry)
                .where(CaseJournalEntry.case_id == case_id)
                .order_by(CaseJournalEntry.created_at.asc())
            )
        ).scalars().all()
    )
    return [
        {
            "id": r.id,
            "author": r.author,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "text": r.text,
        }
        for r in rows
    ]


async def _load_checks(session, case_id: int) -> list[dict]:
    rows = list(
        (
            await session.execute(
                select(CaseBankCheckItem)
                .where(CaseBankCheckItem.case_id == case_id)
                .order_by(CaseBankCheckItem.id.asc())
            )
        ).scalars().all()
    )
    return [
        {
            "id": r.id,
            "entity_type": r.entity_type,
            "entity_label": r.entity_label,
            "entity_ref": r.entity_ref,
            "status": r.status,
            "checked_by": r.checked_by,
            "checked_at": r.checked_at.isoformat() if r.checked_at else None,
            "note": r.note,
        }
        for r in rows
    ]


async def find_open_case_for_company(
    *,
    uid: str | None = None,
    ehraid: int | None = None,
    name: str | None = None,
) -> dict[str, Any] | None:
    """Return an open team case for this company if one exists."""
    async with async_session() as session:
        q = select(CompanyCase).where(CompanyCase.status.in_(OPEN_STATUSES))
        cases = list((await session.execute(q)).scalars().all())
        uid_digits = re.sub(r"\D", "", uid or "")
        name_n = (name or "").strip().lower()
        for case in cases:
            if uid_digits and re.sub(r"\D", "", case.company_uid or "") == uid_digits:
                return _case_dict(case, bank_checks=await _load_checks(session, case.id))
            if ehraid and case.company_ehraid == ehraid:
                return _case_dict(case, bank_checks=await _load_checks(session, case.id))
            if name_n and (case.company_name or "").strip().lower() == name_n:
                return _case_dict(case, bank_checks=await _load_checks(session, case.id))
        return None


async def list_confirmed_case_seeds() -> list[dict[str, Any]]:
    """Seed entries for fraud_network batch analyze (confirmed CompanyCases)."""
    async with async_session() as session:
        cases = list(
            (
                await session.execute(
                    select(CompanyCase)
                    .where(CompanyCase.status.in_(ACTIVE_FRAUD_STATUSES))
                    .order_by(CompanyCase.confirmed_at.desc())
                )
            ).scalars().all()
        )
        return [
            {
                "id": f"case-{c.id}",
                "name": c.company_name,
                "uid": c.company_uid,
                "note": f"case#{c.id}",
                "category": c.fraud_type or "confirmed",
                "case_id": c.id,
            }
            for c in cases
        ]


async def open_case(
    *,
    company_name: str,
    company_uid: str | None = None,
    company_ehraid: int | None = None,
    company_purpose: str | None = None,
    opened_by: str,
    source_alert_id: int | None = None,
) -> dict[str, Any]:
    name = (company_name or "").strip()
    if not name and not company_uid:
        raise ValueError("Firmenname oder UID erforderlich")
    existing = await find_open_case_for_company(
        uid=company_uid, ehraid=company_ehraid, name=name
    )
    if existing:
        return {**existing, "already_existed": True}

    # Enrich purpose from Zefix if missing
    purpose = company_purpose
    ehraid = company_ehraid
    uid = company_uid
    resolved_name = name
    if not purpose or not ehraid:
        try:
            from app.hr_network.zefix_resolve import format_company_uid, resolve_company_detail

            detail = await resolve_company_detail(name or None, company_uid)
            resolved_name = detail.get("name") or name
            ehraid = detail.get("ehraid") or ehraid
            uid = format_company_uid(detail) or uid
            purpose = purpose or (detail.get("purpose") or None)
            if purpose:
                purpose = re.sub(r"<[^>]+>", " ", str(purpose))
                purpose = re.sub(r"\s+", " ", purpose).strip()[:1024]
        except Exception as e:
            logger.info("Zefix enrich for new case skipped: %s", e)

    async with async_session() as session:
        case = CompanyCase(
            company_name=resolved_name or name or "Unbekannt",
            company_uid=uid,
            company_ehraid=int(ehraid) if ehraid else None,
            company_purpose=purpose,
            status="under_review",
            opened_by=opened_by,
            opened_at=datetime.now(timezone.utc),
            source_alert_id=source_alert_id,
        )
        session.add(case)
        await session.commit()
        await session.refresh(case)
        case_payload = {**_case_dict(case, bank_checks=[]), "already_existed": False}
        case_name, case_uid = case.company_name, case.company_uid

    # Auto-watch current organs so new mandates are caught before confirm
    from app.hr_network.watch_intake import (
        SCAN_PRIORITY_HIGH,
        SOURCE_CASE_OPEN,
        intake_from_fraud_company,
    )
    from app.hr_network.watched_companies import (
        SOURCE_CASE_OPEN as COMPANY_SOURCE_CASE_OPEN,
        upsert_watched_company,
    )

    # Firma unbedingt auf Firmen-Watchlist (Symmetrie zu Personen)
    try:
        company_watch = await upsert_watched_company(
            company_name=case_name,
            company_uid=case_uid,
            company_ehraid=case_payload.get("company_ehraid"),
            source_reason=COMPANY_SOURCE_CASE_OPEN,
            added_by=opened_by,
            notes="Auto: Fall eröffnet",
        )
    except Exception as e:
        logger.exception("Company watchlist after case open failed")
        company_watch = {"error": str(e)}

    try:
        intake = await intake_from_fraud_company(
            name=case_name,
            uid=case_uid,
            source_reason=SOURCE_CASE_OPEN,
            scan_priority=SCAN_PRIORITY_HIGH,
            notes_prefix="Auto: Fall eröffnet",
        )
    except Exception as e:
        logger.exception("Watch intake after case open failed")
        intake = {"error": str(e), "enrolled": [], "enrolled_count": 0}

    l5_meta = _kickoff_l5_background(name=case_name, uid=case_uid)

    case_payload["watch_intake"] = intake
    case_payload["company_watch"] = company_watch
    case_payload["l5"] = l5_meta
    return case_payload


def _kickoff_l5_background(*, name: str | None, uid: str | None) -> dict[str, Any]:
    """If L5 cache missing, start deep analyze in background (non-blocking)."""
    from app.hr_network.fraud_network_cache import (
        cache_status_for_company,
        load_cached_for_company,
        store_cached_for_company,
    )

    n = (name or "").strip() or None
    u = (uid or "").strip() or None
    if not n and not u:
        return {"l5_cached": False, "l5_started": False, "reason": "no_company"}

    status = cache_status_for_company(company_name=n, company_uid=u)
    l5 = (status.get("levels") or {}).get("5") or {}
    if l5.get("cached"):
        return {"l5_cached": True, "l5_started": False}

    async def _run() -> None:
        from app.hr_network.fraud_network import build_fraud_network

        try:
            # Re-check cache in case another job finished first
            hit, _key = load_cached_for_company(
                level=5, company_name=n, company_uid=u
            )
            if hit is not None:
                return
            result = await build_fraud_network(
                level=5,
                ad_hoc_company={"name": n or "", "uid": u or ""},
                max_person_searches=8,
            )
            if isinstance(result, dict):
                store_cached_for_company(
                    level=5,
                    company_name=n,
                    company_uid=u,
                    payload=result,
                )
                logger.info("Background L5 cached for %s / %s", n, u)
        except Exception:
            logger.exception("Background L5 after case open failed for %s / %s", n, u)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_run())
    except RuntimeError:
        logger.warning("No running loop — L5 background kick skipped")
        return {"l5_cached": False, "l5_started": False, "reason": "no_loop"}

    return {"l5_cached": False, "l5_started": True}


async def update_hit_context(
    case_id: int,
    *,
    hit_amount: float | None = None,
    hit_currency: str | None = None,
    hit_reference: str | None = None,
    hit_note: str | None = None,
) -> dict[str, Any]:
    """Optional payment-hit context (no customer PII). Always replaces the four fields."""
    async with async_session() as session:
        case = await session.get(CompanyCase, case_id)
        if not case:
            raise LookupError("Fall nicht gefunden")
        case.hit_amount = hit_amount
        cur = (hit_currency or "").strip().upper()[:3]
        case.hit_currency = cur or None
        case.hit_reference = (hit_reference or "").strip()[:256] or None
        case.hit_note = (hit_note or "").strip()[:1024] or None
        await session.commit()
    return await get_company_case(case_id)


async def clear_case(case_id: int, *, by: str, note: str = "") -> dict[str, Any]:
    async with async_session() as session:
        case = await session.get(CompanyCase, case_id)
        if not case:
            raise LookupError("Fall nicht gefunden")
        if case.status != "under_review":
            raise ValueError("Nur Fälle in Prüfung können als «kein Betrug» geschlossen werden")
        case.status = "cleared"
        if note.strip():
            session.add(
                CaseJournalEntry(
                    case_id=case.id,
                    author=by,
                    created_at=datetime.now(timezone.utc),
                    text=f"[Geschlossen — kein Betrug] {note.strip()[:3800]}",
                )
            )
        await session.commit()
        return await get_company_case(case_id)


async def confirm_fraud(
    case_id: int,
    *,
    fraud_type: str,
    by: str,
) -> dict[str, Any]:
    fraud_type = (fraud_type or "").strip()
    if fraud_type not in FRAUD_TYPES:
        raise ValueError(f"fraud_type muss einer von {FRAUD_TYPES} sein")

    async with async_session() as session:
        case = await session.get(CompanyCase, case_id)
        if not case:
            raise LookupError("Fall nicht gefunden")
        if case.status != "under_review":
            raise ValueError("Nur Fälle in Prüfung können bestätigt werden")
        case.status = "confirmed_fraud"
        case.fraud_type = fraud_type
        case.confirmed_at = datetime.now(timezone.utc)
        await session.commit()
        name, uid = case.company_name, case.company_uid

    # Watchlist intake outside session
    from app.hr_network.watch_intake import (
        SCAN_PRIORITY_HIGH,
        SOURCE_FRAUD_LIST_OFFICER,
        intake_from_fraud_company,
    )

    try:
        intake = await intake_from_fraud_company(
            name=name,
            uid=uid,
            source_reason=SOURCE_FRAUD_LIST_OFFICER,
            scan_priority=SCAN_PRIORITY_HIGH,
            notes_prefix="Auto: Betrug bestätigt",
        )
    except Exception as e:
        logger.exception("Watch intake after confirm failed")
        intake = {"error": str(e), "enrolled": [], "enrolled_count": 0}

    await _seed_bank_checklist(case_id, intake=intake)
    result = await get_company_case(case_id)
    result["watch_intake"] = intake
    return result


async def _seed_bank_checklist(case_id: int, *, intake: dict[str, Any]) -> None:
    """
    Core checklist only (process rule B):
    - seed company
    - current HR officers (not former)
    - no other companies from person links (those explode the list)

    Former persons / extra firms can be added later via add_bank_check_item.
    """
    async with async_session() as session:
        case = await session.get(CompanyCase, case_id)
        if not case:
            return
        existing = list(
            (
                await session.execute(
                    select(CaseBankCheckItem).where(CaseBankCheckItem.case_id == case_id)
                )
            ).scalars().all()
        )
        if existing:
            return

        seen: set[tuple[str, str]] = set()

        def add(entity_type: str, label: str, ref: str | None) -> None:
            key = (entity_type, (ref or label).lower())
            if key in seen or not label:
                return
            seen.add(key)
            session.add(
                CaseBankCheckItem(
                    case_id=case_id,
                    entity_type=entity_type,
                    entity_label=label[:512],
                    entity_ref=(ref or "")[:128] or None,
                    status="pending",
                )
            )

        add("company", case.company_name, case.company_uid or str(case.company_ehraid or ""))

        for enr in intake.get("enrolled") or []:
            # Former only appear when intake ran with include_former=True
            if not _is_core_officer_roles(enr.get("roles") or []):
                continue
            pid = enr.get("person_id")
            label = enr.get("display_name") or ""
            add("person", label, str(pid) if pid else None)

        await session.commit()


_CORE_ROLE_RE = re.compile(
    r"geschäftsführer|gesellschafter|verwaltungsrat|präsident|inhaber|"
    r"zeichnungsberechtigt|direktor|einzelunternehmer|"
    r"mitglied\s+des\s+verwaltungsrats|vr-?mitglied",
    re.IGNORECASE,
)


def _is_core_officer_roles(roles: list[str]) -> bool:
    """True for current officers we must bank-check; empty roles → include (safer)."""
    if not roles:
        return True
    joined = " ".join(roles)
    return bool(_CORE_ROLE_RE.search(joined))


async def mark_case_suspicious(
    case_id: int,
    *,
    by: str,
    note: str = "",
) -> dict[str, Any]:
    """
    «Als Verdächtig markieren»: Tag In Abklärung + Watchlist (Firma/Organe), Akte schliessen.
    """
    from app.hr_network.company_tags import (
        TAG_UNDER_INVESTIGATION,
        set_company_tag,
    )
    from app.hr_network.under_investigation_watchlist import (
        enroll_under_investigation_watchlist,
    )

    async with async_session() as session:
        case = await session.get(CompanyCase, case_id)
        if not case:
            raise LookupError("Fall nicht gefunden")
        if case.status != "under_review":
            raise ValueError("Nur Fälle in Prüfung können als verdächtig markiert werden")
        name = case.company_name
        uid = case.company_uid
        ehraid = case.company_ehraid

    try:
        tag_row = await set_company_tag(
            company_name=name,
            company_uid=uid,
            tag=TAG_UNDER_INVESTIGATION,
            set_by=by,
        )
    except Exception as e:
        logger.exception("Tag In Abklärung after mark-suspicious failed")
        tag_row = {"error": str(e)}

    try:
        watchlist_side = await enroll_under_investigation_watchlist(
            company_name=name,
            company_uid=uid,
            company_ehraid=ehraid,
            added_by=by,
        )
    except Exception as e:
        logger.exception("Watchlist enroll after mark-suspicious failed")
        watchlist_side = {"error": str(e)}

    journal = "[In Abklärung] Als Verdächtig markiert — Firma und Organe auf Watchlist; Akte geschlossen."
    if (note or "").strip():
        journal = f"{journal} {(note or '').strip()[:1800]}"

    async with async_session() as session:
        case = await session.get(CompanyCase, case_id)
        if not case:
            raise LookupError("Fall nicht gefunden")
        case.status = "cleared"
        session.add(
            CaseJournalEntry(
                case_id=case.id,
                author=by,
                created_at=datetime.now(timezone.utc),
                text=journal[:4000],
            )
        )
        await session.commit()

    result = await get_company_case(case_id)
    result["marked_suspicious"] = True
    result["company_tag"] = tag_row
    result["watchlist"] = watchlist_side
    return result


async def enroll_former_officers_for_case(case_id: int, *, by: str) -> dict[str, Any]:
    """After confirm: optionally enroll former organs on watchlist + checklist."""
    async with async_session() as session:
        case = await session.get(CompanyCase, case_id)
        if not case:
            raise LookupError("Fall nicht gefunden")
        if case.status not in ("confirmed_fraud", "ready_for_report"):
            raise ValueError("Ehemalige nur nach Betrugsbestätigung aufnehmbar")
        name, uid = case.company_name, case.company_uid

    from app.hr_network.watch_intake import (
        SCAN_PRIORITY_HIGH,
        SOURCE_FRAUD_LIST_OFFICER,
        intake_from_fraud_company,
    )

    try:
        intake = await intake_from_fraud_company(
            name=name,
            uid=uid,
            include_former=True,
            source_reason=SOURCE_FRAUD_LIST_OFFICER,
            scan_priority=SCAN_PRIORITY_HIGH,
            notes_prefix="Auto: Ehemalige nach Bestätigung",
        )
    except Exception as e:
        logger.exception("Former-officer intake failed")
        raise ValueError(f"Aufnahme Ehemaliger fehlgeschlagen: {e}") from e

    former_enrolled = [
        enr
        for enr in (intake.get("enrolled") or [])
        if (enr.get("person_hr_status") or "") == "former"
    ]

    async with async_session() as session:
        case = await session.get(CompanyCase, case_id)
        if not case:
            raise LookupError("Fall nicht gefunden")
        existing = list(
            (
                await session.execute(
                    select(CaseBankCheckItem).where(CaseBankCheckItem.case_id == case_id)
                )
            ).scalars().all()
        )
        seen = {
            (r.entity_type, (r.entity_ref or r.entity_label or "").lower())
            for r in existing
        }
        added = 0
        for enr in former_enrolled:
            if not _is_core_officer_roles(enr.get("roles") or []):
                continue
            label = (enr.get("display_name") or "").strip()
            if not label:
                continue
            pid = enr.get("person_id")
            key = ("person", str(pid).lower() if pid else label.lower())
            if key in seen:
                continue
            seen.add(key)
            session.add(
                CaseBankCheckItem(
                    case_id=case_id,
                    entity_type="person",
                    entity_label=label[:512],
                    entity_ref=(str(pid) if pid else "")[:128] or None,
                    status="pending",
                )
            )
            added += 1
        if added and case.status == "ready_for_report":
            # New pending checks → back to documentation
            case.status = "confirmed_fraud"
        await session.commit()

    result = await get_company_case(case_id)
    result["former_intake"] = {
        "enrolled": former_enrolled,
        "enrolled_count": len(former_enrolled),
        "checklist_added": added,
    }
    result["enrolled_by"] = by
    return result


async def add_bank_check_item(
    case_id: int,
    *,
    entity_type: str,
    entity_label: str,
    entity_ref: str | None = None,
) -> dict[str, Any]:
    """Manually add a checklist row (e.g. former person / extra firm)."""
    entity_type = (entity_type or "").strip().lower()
    if entity_type not in ("company", "person"):
        raise ValueError("entity_type muss company oder person sein")
    label = (entity_label or "").strip()
    if len(label) < 2:
        raise ValueError("Bezeichnung ist Pflicht")

    async with async_session() as session:
        case = await session.get(CompanyCase, case_id)
        if not case:
            raise LookupError("Fall nicht gefunden")
        if case.status not in ("confirmed_fraud", "ready_for_report"):
            raise ValueError("Checkliste nur bei bestätigten Fällen erweiterbar")

        session.add(
            CaseBankCheckItem(
                case_id=case_id,
                entity_type=entity_type,
                entity_label=label[:512],
                entity_ref=(entity_ref or "")[:128] or None,
                status="pending",
            )
        )
        if case.status == "ready_for_report":
            case.status = "confirmed_fraud"
        await session.commit()
    return await get_company_case(case_id)


async def add_journal_entry(case_id: int, *, author: str, text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if len(text) < 3:
        raise ValueError("Journal-Text ist Pflicht")
    async with async_session() as session:
        case = await session.get(CompanyCase, case_id)
        if not case:
            raise LookupError("Fall nicht gefunden")
        session.add(
            CaseJournalEntry(
                case_id=case_id,
                author=author,
                created_at=datetime.now(timezone.utc),
                text=text[:4000],
            )
        )
        await session.commit()
    return await get_company_case(case_id)


async def update_payment_flags(
    case_id: int,
    *,
    payment_blocked: bool | None,
    payment_blocked_note: str | None,
) -> dict[str, Any]:
    async with async_session() as session:
        case = await session.get(CompanyCase, case_id)
        if not case:
            raise LookupError("Fall nicht gefunden")
        case.payment_blocked = payment_blocked
        case.payment_blocked_note = (payment_blocked_note or "")[:512] or None
        await session.commit()

        # Promote when Sicherung + checklist done
        if case.status == "confirmed_fraud" and payment_blocked is not None:
            checks = await _load_checks(session, case_id)
            pending = sum(1 for c in checks if c["status"] == "pending")
            if checks and pending == 0:
                case.status = "ready_for_report"
                await session.commit()

    return await get_company_case(case_id)


async def update_bank_check(
    case_id: int,
    item_id: int,
    *,
    status: str,
    note: str,
    checked_by: str,
) -> dict[str, Any]:
    status = (status or "").strip()
    if status not in ("no_relationship", "relationship_found"):
        raise ValueError("status muss no_relationship oder relationship_found sein")
    note = (note or "").strip()
    async with async_session() as session:
        item = await session.get(CaseBankCheckItem, item_id)
        if not item or item.case_id != case_id:
            raise LookupError("Checklisten-Eintrag nicht gefunden")
        item.status = status
        item.note = note[:512] if note else None
        item.checked_by = checked_by
        item.checked_at = datetime.now(timezone.utc)
        await session.commit()

        # Promote to ready_for_report when checklist + Sicherung complete
        case = await session.get(CompanyCase, case_id)
        checks = await _load_checks(session, case_id)
        pending = sum(1 for c in checks if c["status"] == "pending")
        payment_done = case is not None and case.payment_blocked is not None
        if (
            case
            and case.status == "confirmed_fraud"
            and pending == 0
            and checks
            and payment_done
        ):
            case.status = "ready_for_report"
            await session.commit()

    return await get_company_case(case_id)


async def generate_case_report(case_id: int, *, by: str) -> dict[str, Any]:
    detail = await get_company_case(case_id)
    if not detail["documentation_complete"]:
        raise ValueError(
            f"Checkliste unvollständig ({detail['bank_checks_pending']} offen) — Report nicht möglich"
        )
    if detail["status"] not in ("confirmed_fraud", "ready_for_report", "reported"):
        raise ValueError("Report nur für bestätigte Fälle")

    # Freeze network snapshot (best-effort, time-boxed so report never hangs)
    snapshot = None
    try:
        from app.hr_network.fraud_network import build_fraud_network

        snapshot = await asyncio.wait_for(
            build_fraud_network(
                level=2,
                ad_hoc_company={
                    "name": detail["company_name"],
                    "uid": detail.get("company_uid"),
                },
                max_person_searches=0,
            ),
            timeout=25.0,
        )
    except asyncio.TimeoutError:
        logger.warning("Network snapshot for case %s timed out", case_id)
        snapshot = {"error": "Netzwerk-Snapshot Timeout — Report ohne Graph"}
    except Exception as e:
        logger.warning("Network snapshot for case report failed: %s", e)
        snapshot = {"error": str(e)}

    pdf = build_company_case_report(detail, snapshot=snapshot, prepared_by=by)
    path = os.path.join(_reports_dir(), f"case_{case_id}.pdf")
    with open(path, "wb") as f:
        f.write(pdf)

    async with async_session() as session:
        case = await session.get(CompanyCase, case_id)
        if not case:
            raise LookupError("Fall nicht gefunden")
        case.report_path = path
        case.reported_at = datetime.now(timezone.utc)
        case.reported_by = by
        case.status = "reported"
        case.snapshot_json = {
            "frozen_at": datetime.now(timezone.utc).isoformat(),
            "stats": (snapshot or {}).get("stats"),
            "seed_companies": (snapshot or {}).get("seed_companies"),
        }
        await session.commit()

    return await get_company_case(case_id)


async def get_case_report_path(case_id: int) -> tuple[str, CompanyCase]:
    async with async_session() as session:
        case = await session.get(CompanyCase, case_id)
        if not case:
            raise LookupError("Fall nicht gefunden")
        if not case.report_path or not os.path.isfile(case.report_path):
            raise FileNotFoundError("Kein Report vorhanden")
        return case.report_path, case


async def action_reported_case(
    case_id: int,
    *,
    note: str,
    actioned_by: str,
) -> dict[str, Any]:
    note = (note or "").strip()
    if len(note) < 3:
        raise ValueError("compliance_note ist Pflicht")
    async with async_session() as session:
        case = await session.get(CompanyCase, case_id)
        if not case:
            raise LookupError("Fall nicht gefunden")
        if case.status != "reported":
            raise ValueError("Fall ist nicht im Status reported")
        case.status = "closed"
        case.compliance_actioned_by = actioned_by
        case.compliance_actioned_at = datetime.now(timezone.utc)
        case.compliance_note = note[:1024]
        await session.commit()
    return await get_company_case(case_id)


async def open_case_from_alert(alert_id: int, *, opened_by: str) -> dict[str, Any]:
    async with async_session() as session:
        alert = await session.get(NetworkAlert, alert_id)
        if not alert:
            raise LookupError("Alert nicht gefunden")
        company_name = alert.company_name
        ehraid = alert.company_ehraid
        person = await session.get(WatchedPerson, alert.person_id) if alert.person_id else None
        if not company_name and person:
            company_name = person.source_company_name
            ehraid = ehraid or person.source_company_ehraid
        if not company_name:
            raise ValueError("Alert enthält keine Firma")

    return await open_case(
        company_name=company_name,
        company_ehraid=int(ehraid) if ehraid else None,
        opened_by=opened_by,
        source_alert_id=alert_id,
    )


async def delete_company_case(case_id: int) -> dict[str, Any]:
    """Permanently remove a CompanyCase and related journal / checklist rows."""
    async with async_session() as session:
        case = await session.get(CompanyCase, case_id)
        if not case:
            raise LookupError("Fall nicht gefunden")
        name = case.company_name
        await session.execute(delete(CaseJournalEntry).where(CaseJournalEntry.case_id == case_id))
        await session.execute(delete(CaseBankCheckItem).where(CaseBankCheckItem.case_id == case_id))
        await session.delete(case)
        await session.commit()
        return {"deleted": True, "id": case_id, "company_name": name}


async def branch_signal(*, months: int = 6, limit: int = 8) -> dict[str, Any]:
    """Top purposes among confirmed fraud cases in the last N months."""
    months = max(1, min(int(months or 6), 36))
    top_n = max(1, min(int(limit or 8), 30))
    since = datetime.now(timezone.utc) - timedelta(days=30 * months)
    async with async_session() as session:
        cases = list(
            (
                await session.execute(
                    select(CompanyCase).where(
                        CompanyCase.status.in_(ACTIVE_FRAUD_STATUSES),
                        CompanyCase.confirmed_at.is_not(None),
                        CompanyCase.confirmed_at >= since,
                    )
                )
            ).scalars().all()
        )
    counts: dict[str, int] = {}
    labels: dict[str, str] = {}
    for c in cases:
        key = _purpose_key(c.company_purpose) or "(ohne Zweckangabe)"
        counts[key] = counts.get(key, 0) + 1
        if key not in labels:
            labels[key] = (c.company_purpose or key)[:160]
    total = sum(counts.values()) or 1
    ranked = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:top_n]
    return {
        "months": months,
        "total_confirmed": sum(counts.values()),
        "branches": [
            {
                "key": k,
                "label": labels.get(k, k),
                "count": n,
                "share": round(100.0 * n / total, 1),
            }
            for k, n in ranked
            if k != "(ohne Zweckangabe)" or n > 0
        ],
    }
