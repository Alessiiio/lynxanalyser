"""Shared Firmenanalyse search history (team-visible on idle start page)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import delete, select

from app.database import CompanySearchHistory, async_session

logger = logging.getLogger(__name__)

# Cap stored rows so SQLite stays small; list de-dupes further for the UI.
_MAX_STORE = 500
_DEFAULT_LIMIT = 15


def _company_key(name: str | None, uid: str | None) -> str:
    u = (uid or "").strip().upper()
    if u:
        return f"uid:{u}"
    n = (name or "").strip().lower()
    return f"name:{n}" if n else ""


def _iso_utc(dt: datetime | None) -> str | None:
    """Serialize as ISO-8601 UTC with Z so clients can convert to Europe/Zurich."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        aware = dt.replace(tzinfo=timezone.utc)
    else:
        aware = dt.astimezone(timezone.utc)
    return aware.isoformat().replace("+00:00", "Z")


def _row_dict(row: CompanySearchHistory) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.company_name or "",
        "uid": row.company_uid or "",
        "by": row.searched_by or "Team",
        "by_username": row.searched_by_username or "",
        "at": _iso_utc(row.searched_at),
    }


async def log_company_search(
    *,
    company_name: str | None,
    company_uid: str | None,
    searched_by: str,
    searched_by_username: str | None = None,
) -> Optional[dict[str, Any]]:
    """Insert one search event (deduped per user+company within the same second-ish)."""
    name = (company_name or "").strip()
    uid = (company_uid or "").strip() or None
    if not name and not uid:
        return None
    by = (searched_by or "").strip() or "Team"
    uname = (searched_by_username or "").strip() or None
    now = datetime.now(timezone.utc)
    key = _company_key(name, uid)

    try:
        async with async_session() as session:
            # Refresh timestamp if same user just re-ran the same firm (keeps list clean).
            recent = list(
                (
                    await session.execute(
                        select(CompanySearchHistory)
                        .where(CompanySearchHistory.searched_by_username == uname)
                        .order_by(CompanySearchHistory.searched_at.desc())
                        .limit(40)
                    )
                )
                .scalars()
                .all()
            )
            for row in recent:
                if _company_key(row.company_name, row.company_uid) == key:
                    row.company_name = name or row.company_name
                    row.company_uid = uid or row.company_uid
                    row.searched_by = by
                    row.searched_at = now
                    await session.commit()
                    await session.refresh(row)
                    return _row_dict(row)

            row = CompanySearchHistory(
                company_name=name,
                company_uid=uid,
                searched_by=by,
                searched_by_username=uname,
                searched_at=now,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)

            # Prune oldest beyond cap
            ids = list(
                (
                    await session.execute(
                        select(CompanySearchHistory.id).order_by(
                            CompanySearchHistory.searched_at.desc()
                        )
                    )
                )
                .scalars()
                .all()
            )
            if len(ids) > _MAX_STORE:
                drop = ids[_MAX_STORE:]
                await session.execute(
                    delete(CompanySearchHistory).where(CompanySearchHistory.id.in_(drop))
                )
                await session.commit()
            return _row_dict(row)
    except Exception:
        logger.exception("Failed to log company search")
        return None


async def list_company_searches(*, limit: int = _DEFAULT_LIMIT) -> list[dict[str, Any]]:
    """Newest-first list de-duplicated by company (one row per firm, last actor)."""
    lim = max(1, min(int(limit or _DEFAULT_LIMIT), 50))
    async with async_session() as session:
        rows = list(
            (
                await session.execute(
                    select(CompanySearchHistory)
                    .order_by(CompanySearchHistory.searched_at.desc())
                    .limit(200)
                )
            )
            .scalars()
            .all()
        )
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        key = _company_key(row.company_name, row.company_uid)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(_row_dict(row))
        if len(out) >= lim:
            break
    return out


async def clear_own_company_searches(username: str) -> int:
    """Delete history rows attributed to one user. Returns deleted count."""
    uname = (username or "").strip()
    if not uname:
        return 0
    async with async_session() as session:
        result = await session.execute(
            delete(CompanySearchHistory).where(
                CompanySearchHistory.searched_by_username == uname
            )
        )
        await session.commit()
        return int(result.rowcount or 0)
