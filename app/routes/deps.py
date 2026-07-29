"""Shared FastAPI helpers: rate limit + auth dependencies."""

from __future__ import annotations

from typing import Callable

from fastapi import Depends, HTTPException, Request

from app.auth import ALL_ROLES, normalize_role, user_public_dict
from app.database import User, async_session
from app.rate_limit import is_login_rate_limited, is_rate_limited
from sqlalchemy import select


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


async def load_user_from_session(request: Request) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    async with async_session() as session:
        user = await session.get(User, int(user_id))
        if not user or not user.active:
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
