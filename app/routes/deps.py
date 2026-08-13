"""Shared FastAPI helpers: rate limit + auth dependencies."""

from __future__ import annotations

from typing import Callable

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select

from app.auth import ALL_ROLES, is_admin_role, normalize_role, user_public_dict
from app.database import User
from app import database as db
from app.rate_limit import is_login_2fa_rate_limited, is_login_rate_limited, is_rate_limited

# Admin-only: client may send this so Firmenanalyse searches are not team-logged.
INCOGNITO_HEADER = "x-lynx-incognito"


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def enforce_rate_limit(request: Request) -> None:
    if is_rate_limited(client_ip(request)):
        raise HTTPException(status_code=429, detail="Rate limit exceeded — try again in a minute")


def enforce_login_rate_limit(request: Request) -> None:
    if is_login_rate_limited(client_ip(request)):
        raise HTTPException(
            status_code=429,
            detail="Zu viele Login-Versuche — bitte eine Minute warten",
        )


def enforce_login_2fa_rate_limit(request: Request) -> None:
    if is_login_2fa_rate_limited(client_ip(request)):
        raise HTTPException(
            status_code=429,
            detail="Zu viele 2FA-Versuche — bitte eine Minute warten",
        )


async def load_user_from_session(request: Request) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    async with db.async_session() as session:
        user = await session.get(User, int(user_id))
        if not user or not user.active:
            return None
        # Mandatory 2FA: full session invalid after admin reset / incomplete enroll
        if not bool(getattr(user, "totp_enabled", False)):
            request.session.clear()
            return None
        return user


async def get_current_user(request: Request) -> User:
    user = getattr(request.state, "user", None)
    if user is not None:
        return user
    user = await load_user_from_session(request)
    if not user:
        raise HTTPException(status_code=401, detail="Nicht angemeldet")
    request.state.user = user
    return user


async def get_optional_user(request: Request) -> User | None:
    user = getattr(request.state, "user", None)
    if user is not None:
        return user
    return await load_user_from_session(request)


def require_role(*roles: str) -> Callable:
    allowed = {normalize_role(r) for r in roles} or set(ALL_ROLES)

    async def _dep(user: User = Depends(get_current_user)) -> User:
        role = normalize_role(user.role)
        if role == "admin" or role in allowed:
            return user
        raise HTTPException(status_code=403, detail="Keine Berechtigung für diese Aktion")

    return _dep


def current_username(user: User) -> str:
    return user.username


def current_user_payload(user: User) -> dict:
    return user_public_dict(user)


async def count_active_admins(session, *, exclude_user_id: int | None = None) -> int:
    q = select(User).where(User.role == "admin", User.active.is_(True))
    rows = list((await session.execute(q)).scalars().all())
    if exclude_user_id is not None:
        rows = [u for u in rows if u.id != exclude_user_id]
    return len(rows)


def is_admin_incognito(request: Request, user: User) -> bool:
    """True only when caller is admin AND sends X-Lynx-Incognito: 1 (header ignored otherwise)."""
    if not is_admin_role(getattr(user, "role", None)):
        return False
    raw = (request.headers.get(INCOGNITO_HEADER) or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}
