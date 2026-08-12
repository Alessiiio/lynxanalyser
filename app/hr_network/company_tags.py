"""Team-visible firm tags (MVP: «In Abklärung» / under_investigation)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select

from app.database import CompanyTag, async_session

TAG_UNDER_INVESTIGATION = "under_investigation"
TAG_LABELS_DE = {
    TAG_UNDER_INVESTIGATION: "In Abklärung",
}
ALLOWED_TAGS = frozenset(TAG_LABELS_DE.keys())


def _uid_digits(uid: str | None) -> str:
    return re.sub(r"\D", "", uid or "")


def _name_key(name: str | None) -> str:
    return (name or "").strip().lower()


def _iso_utc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        aware = dt.replace(tzinfo=timezone.utc)
    else:
        aware = dt.astimezone(timezone.utc)
    return aware.isoformat().replace("+00:00", "Z")


def _row_dict(row: CompanyTag) -> dict[str, Any]:
    return {
        "id": row.id,
        "company_name": row.company_name or "",
        "company_uid": row.company_uid or "",
        "uid": row.company_uid or "",
        "name": row.company_name or "",
        "tag": row.tag,
        "label": TAG_LABELS_DE.get(row.tag, row.tag),
        "set_by": row.set_by or "Team",
        "set_by_username": row.set_by_username or "",
        "set_at": _iso_utc(row.set_at),
    }


def _match_row(
    rows: list[CompanyTag],
    *,
    uid: str | None,
    name: str | None,
) -> CompanyTag | None:
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


async def list_company_tags(*, tag: str | None = None) -> list[dict[str, Any]]:
    """All tags (optionally filtered), newest first."""
    tag_f = (tag or "").strip() or None
    if tag_f and tag_f not in ALLOWED_TAGS:
        return []
    async with async_session() as session:
        q = select(CompanyTag).order_by(CompanyTag.set_at.desc())
        if tag_f:
            q = q.where(CompanyTag.tag == tag_f)
        rows = list((await session.execute(q)).scalars().all())
    return [_row_dict(r) for r in rows]


async def get_company_tag(
    *,
    uid: str | None = None,
    name: str | None = None,
    tag: str = TAG_UNDER_INVESTIGATION,
) -> dict[str, Any] | None:
    """Lookup one tag for a firm (uid preferred)."""
    tag_k = (tag or TAG_UNDER_INVESTIGATION).strip()
    if tag_k not in ALLOWED_TAGS:
        return None
    if not _uid_digits(uid) and not _name_key(name):
        return None
    async with async_session() as session:
        rows = list(
            (
                await session.execute(
                    select(CompanyTag)
                    .where(CompanyTag.tag == tag_k)
                    .order_by(CompanyTag.set_at.desc())
                )
            )
            .scalars()
            .all()
        )
    hit = _match_row(rows, uid=uid, name=name)
    return _row_dict(hit) if hit else None


async def set_company_tag(
    *,
    company_name: str | None,
    company_uid: str | None,
    set_by: str,
    set_by_username: str | None = None,
    tag: str = TAG_UNDER_INVESTIGATION,
) -> dict[str, Any]:
    """Upsert tag for a firm (unique per uid+tag, else name+tag)."""
    tag_k = (tag or TAG_UNDER_INVESTIGATION).strip()
    if tag_k not in ALLOWED_TAGS:
        raise ValueError(f"Unbekannter Tag: {tag_k}")
    name = (company_name or "").strip()
    uid = (company_uid or "").strip() or None
    if not name and not uid:
        raise ValueError("Name oder UID erforderlich")
    by = (set_by or "").strip() or "Team"
    uname = (set_by_username or "").strip() or None
    now = datetime.now(timezone.utc)

    async with async_session() as session:
        rows = list(
            (
                await session.execute(
                    select(CompanyTag).where(CompanyTag.tag == tag_k)
                )
            )
            .scalars()
            .all()
        )
        existing = _match_row(rows, uid=uid, name=name)
        if existing:
            existing.company_name = name or existing.company_name
            existing.company_uid = uid or existing.company_uid
            existing.set_by = by
            existing.set_by_username = uname
            existing.set_at = now
            await session.commit()
            await session.refresh(existing)
            return _row_dict(existing)

        row = CompanyTag(
            company_name=name,
            company_uid=uid,
            tag=tag_k,
            set_by=by,
            set_by_username=uname,
            set_at=now,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return _row_dict(row)


async def clear_company_tag(
    *,
    uid: str | None = None,
    name: str | None = None,
    tag: str = TAG_UNDER_INVESTIGATION,
) -> bool:
    """Remove tag for a firm. Returns True if a row was deleted.

    Does **not** remove Watchlist entries (Firma/Personen). Tag-Lebenszyklus
    ist absichtlich getrennt von der Watchlist (sicherer Default).
    """
    tag_k = (tag or TAG_UNDER_INVESTIGATION).strip()
    if tag_k not in ALLOWED_TAGS:
        return False
    if not _uid_digits(uid) and not _name_key(name):
        return False
    async with async_session() as session:
        rows = list(
            (
                await session.execute(
                    select(CompanyTag).where(CompanyTag.tag == tag_k)
                )
            )
            .scalars()
            .all()
        )
        hit = _match_row(rows, uid=uid, name=name)
        if not hit:
            return False
        await session.execute(delete(CompanyTag).where(CompanyTag.id == hit.id))
        await session.commit()
        return True
