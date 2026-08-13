"""Firmen-Watchlist: upsert with UID-primary / name-fallback dedup (Regel A)."""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.database import WatchedCompany, async_session

SOURCE_UNDER_INVESTIGATION = "under_investigation"
SOURCE_CASE_OPEN = "case_open"
SOURCE_BULK_SCAN = "bulk_scan"
SOURCE_MANUAL = "manual"


def _uid_digits(uid: str | None) -> str:
    return re.sub(r"\D", "", uid or "")


def _name_key(name: str | None) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def _iso_utc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        aware = dt.replace(tzinfo=timezone.utc)
    else:
        aware = dt.astimezone(timezone.utc)
    return aware.isoformat().replace("+00:00", "Z")


def company_row_dict(row: WatchedCompany) -> dict[str, Any]:
    return {
        "id": row.id,
        "company_name": row.company_name or "",
        "company_uid": row.company_uid or "",
        "company_ehraid": row.company_ehraid,
        "address": row.address or "",
        "legal_seat": row.legal_seat or "",
        "source_reason": row.source_reason or "",
        "status": row.status or "active",
        "added_at": _iso_utc(row.added_at),
        "added_by": row.added_by or "",
        "notes": row.notes or "",
    }


def find_watched_company_match(
    rows: list[WatchedCompany],
    *,
    uid: str | None,
    name: str | None,
) -> WatchedCompany | None:
    """Dedup A: match by UID digits first, else exact normalized name."""
    digits = _uid_digits(uid)
    name_n = _name_key(name)
    if digits:
        for row in rows:
            if _uid_digits(row.company_uid) == digits:
                return row
    if name_n:
        for row in rows:
            if _name_key(row.company_name) == name_n:
                return row
    return None


async def upsert_watched_company(
    *,
    company_name: str | None,
    company_uid: str | None = None,
    company_ehraid: int | None = None,
    address: str | None = None,
    legal_seat: str | None = None,
    source_reason: str = SOURCE_MANUAL,
    added_by: str | None = None,
    notes: str | None = None,
    status: str = "active",
) -> dict[str, Any]:
    """
    Upsert firm on watchlist.

    Dedup (A): primary key = UID digits; fallback = normalized company name.
    Without UID, name-only rows merge; attaching a UID later updates the same row.
    """
    name = (company_name or "").strip()
    uid = (company_uid or "").strip() or None
    if not name and not uid:
        raise ValueError("Firmenname oder UID erforderlich")
    reason = (source_reason or SOURCE_MANUAL).strip() or SOURCE_MANUAL
    by = (added_by or "").strip() or None
    now = datetime.now(timezone.utc)

    async with async_session() as session:
        rows = list((await session.execute(select(WatchedCompany))).scalars().all())
        existing = find_watched_company_match(rows, uid=uid, name=name)
        if existing:
            if name:
                existing.company_name = name
            if uid:
                existing.company_uid = uid
            if company_ehraid is not None:
                existing.company_ehraid = int(company_ehraid)
            if address:
                existing.address = address.strip()[:1024]
            if legal_seat:
                existing.legal_seat = legal_seat.strip()[:255]
            if notes and not existing.notes:
                existing.notes = notes[:2000]
            # Prefer stronger / more specific source labels lightly
            _STRONG = {SOURCE_UNDER_INVESTIGATION, SOURCE_CASE_OPEN}
            if reason in _STRONG or not existing.source_reason:
                if existing.source_reason != SOURCE_UNDER_INVESTIGATION or reason == SOURCE_UNDER_INVESTIGATION:
                    existing.source_reason = reason
            if status == "active" and existing.status == "cleared":
                existing.status = "active"
            if by:
                existing.added_by = by
            await session.commit()
            await session.refresh(existing)
            return {**company_row_dict(existing), "already_existed": True}

        row = WatchedCompany(
            company_name=name or uid or "",
            company_uid=uid,
            company_ehraid=int(company_ehraid) if company_ehraid is not None else None,
            address=(address or "").strip()[:1024] or None,
            legal_seat=(legal_seat or "").strip()[:255] or None,
            source_reason=reason,
            status=(status or "active").strip() or "active",
            added_at=now,
            added_by=by,
            notes=(notes or "")[:2000] or None,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return {**company_row_dict(row), "already_existed": False}


async def list_watched_companies(
    *,
    status: str | None = "active",
    q: str | None = None,
    source_reason: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    async with async_session() as session:
        query = select(WatchedCompany).order_by(WatchedCompany.added_at.desc())
        status_f = (status or "").strip()
        if status_f:
            statuses = [s.strip() for s in status_f.split(",") if s.strip()]
            if statuses:
                query = query.where(WatchedCompany.status.in_(statuses))
        if source_reason:
            query = query.where(WatchedCompany.source_reason == source_reason.strip())
        q_norm = (q or "").strip()
        if q_norm:
            like = f"%{q_norm}%"
            query = query.where(
                (WatchedCompany.company_name.ilike(like))
                | (WatchedCompany.company_uid.ilike(like))
                | (WatchedCompany.address.ilike(like))
            )
        rows = list((await session.execute(query)).scalars().all())
    total = len(rows)
    limit = max(1, min(int(limit or 100), 500))
    offset = max(0, int(offset or 0))
    page = rows[offset : offset + limit]
    return {
        "items": [company_row_dict(r) for r in page],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


async def update_watched_company_status(
    company_id: int,
    *,
    status: str,
) -> dict[str, Any]:
    status_k = (status or "").strip()
    if status_k not in ("active", "cleared"):
        raise ValueError("status muss active oder cleared sein")
    async with async_session() as session:
        row = await session.get(WatchedCompany, company_id)
        if not row:
            raise LookupError("Firma nicht auf der Watchlist")
        row.status = status_k
        await session.commit()
        await session.refresh(row)
        return company_row_dict(row)


async def delete_watched_companies(company_ids: list[int]) -> dict[str, Any]:
    ids = [int(i) for i in company_ids if i]
    if not ids:
        return {"deleted_count": 0, "deleted_ids": []}
    deleted: list[int] = []
    async with async_session() as session:
        for cid in ids:
            row = await session.get(WatchedCompany, cid)
            if row:
                await session.delete(row)
                deleted.append(cid)
        await session.commit()
    return {"deleted_count": len(deleted), "deleted_ids": deleted}


def export_companies_csv(items: list[dict[str, Any]]) -> str:
    """DS-Export: Firmenname, Adresse."""
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\n")
    writer.writerow(["Firmenname", "Adresse"])
    for it in items:
        writer.writerow([it.get("company_name") or "", it.get("address") or ""])
    return buf.getvalue()


def export_persons_csv(items: list[dict[str, Any]]) -> str:
    """DS-Export Personen: Name, Adresse/Wohnort."""
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\n")
    writer.writerow(["Name", "Adresse"])
    for it in items:
        writer.writerow(
            [
                it.get("display_name") or it.get("name") or "",
                it.get("residence") or it.get("address") or "",
            ]
        )
    return buf.getvalue()
