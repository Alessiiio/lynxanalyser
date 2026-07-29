"""Persisted admin application settings (SQLite KV)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.database import AppSetting, async_session

DEFAULTS: dict[str, Any] = {
    "anonymize_mode": False,
}


def _unwrap(raw: Any, key: str) -> Any:
    if isinstance(raw, dict) and "v" in raw:
        return raw["v"]
    if raw is None:
        return DEFAULTS.get(key)
    return raw


async def get_setting(key: str, default: Any = None) -> Any:
    async with async_session() as session:
        row = await session.get(AppSetting, key)
        if not row:
            return DEFAULTS.get(key) if default is None else default
        return _unwrap(row.value, key)


async def set_setting(key: str, value: Any, *, updated_by: str = "") -> Any:
    async with async_session() as session:
        row = await session.get(AppSetting, key)
        payload = {"v": value}
        if row:
            row.value = payload
            row.updated_at = datetime.now(timezone.utc)
            row.updated_by = updated_by or None
        else:
            session.add(
                AppSetting(
                    key=key,
                    value=payload,
                    updated_at=datetime.now(timezone.utc),
                    updated_by=updated_by or None,
                )
            )
        await session.commit()
    return value


async def get_public_settings() -> dict[str, Any]:
    """Settings safe to expose to all logged-in users (drives UI modes)."""
    anon = await get_setting("anonymize_mode", False)
    return {
        "anonymize_mode": bool(anon),
    }


async def get_admin_settings() -> dict[str, Any]:
    public = await get_public_settings()
    meta: dict[str, Any] = {}
    async with async_session() as session:
        rows = list((await session.execute(select(AppSetting))).scalars().all())
        for row in rows:
            meta[row.key] = {
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                "updated_by": row.updated_by,
            }
    return {**public, "_meta": meta}
