"""Persistent admin audit trail (logins, user admin, exports)."""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import Request
from sqlalchemy import select

from app.database import AuditEvent, async_session

logger = logging.getLogger(__name__)


def client_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    forwarded = request.headers.get("x-forwarded-for") or ""
    if forwarded:
        return forwarded.split(",")[0].strip()[:64] or None
    if request.client and request.client.host:
        return str(request.client.host)[:64]
    return None


async def record_audit(
    *,
    action: str,
    success: bool = True,
    actor_username: str | None = None,
    actor_display: str | None = None,
    target: str | None = None,
    detail: str | None = None,
    ip: str | None = None,
    request: Request | None = None,
) -> None:
    """Best-effort write — never break the primary request on audit failure."""
    try:
        async with async_session() as session:
            session.add(
                AuditEvent(
                    created_at=datetime.now(timezone.utc),
                    action=(action or "unknown")[:64],
                    actor_username=(actor_username or None) and actor_username[:64],
                    actor_display=(actor_display or None) and actor_display[:128],
                    target=(target or None) and target[:256],
                    detail=(detail or None) and detail[:1024],
                    ip=ip or client_ip(request),
                    success=bool(success),
                )
            )
            await session.commit()
    except Exception:
        logger.exception("audit_log write failed action=%s", action)


def _iso(dt: datetime | None) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def event_dict(row: AuditEvent) -> dict[str, Any]:
    return {
        "id": row.id,
        "created_at": _iso(row.created_at),
        "action": row.action or "",
        "actor_username": row.actor_username or "",
        "actor_display": row.actor_display or "",
        "target": row.target or "",
        "detail": row.detail or "",
        "ip": row.ip or "",
        "success": bool(row.success),
    }


async def list_audit_events(
    *,
    action: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    from sqlalchemy import func

    limit = max(1, min(int(limit or 100), 500))
    offset = max(0, int(offset or 0))
    async with async_session() as session:
        count_q = select(func.count()).select_from(AuditEvent)
        list_q = select(AuditEvent).order_by(AuditEvent.created_at.desc())
        if action and action.strip():
            act = action.strip()[:64]
            count_q = count_q.where(AuditEvent.action == act)
            list_q = list_q.where(AuditEvent.action == act)
        total = int((await session.execute(count_q)).scalar_one() or 0)
        rows = list(
            (await session.execute(list_q.offset(offset).limit(limit))).scalars().all()
        )
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [event_dict(r) for r in rows],
    }


def export_audit_csv(items: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\n")
    writer.writerow(
        [
            "Zeit",
            "Aktion",
            "Erfolg",
            "Benutzer",
            "Anzeigename",
            "Ziel",
            "Detail",
            "IP",
        ]
    )
    for it in items:
        writer.writerow(
            [
                it.get("created_at") or "",
                it.get("action") or "",
                "ja" if it.get("success") else "nein",
                it.get("actor_username") or "",
                it.get("actor_display") or "",
                it.get("target") or "",
                it.get("detail") or "",
                it.get("ip") or "",
            ]
        )
    return buf.getvalue()
