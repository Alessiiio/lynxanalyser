"""Login / logout / current user / admin user management."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.auth import (
    ALL_ROLES,
    hash_password,
    landing_path_for_role,
    normalize_role,
    user_public_dict,
    verify_login_password,
    verify_password,
)
from app.database import User, async_session
from app.routes.deps import get_current_user, require_role, enforce_login_rate_limit

router = APIRouter()


class LoginBody(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class CreateUserBody(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)
    password: str = Field(..., min_length=12, max_length=128)
    display_name: str = Field(..., min_length=1, max_length=128)
    role: str = Field(..., min_length=1, max_length=32)


class ChangePasswordBody(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=12, max_length=128)


class AdminResetPasswordBody(BaseModel):
    password: str = Field(..., min_length=12, max_length=128)


@router.get("/login")
async def login_page(request: Request):
    from app.routes.deps import get_optional_user
    user = await get_optional_user(request)
    if user:
        return RedirectResponse(landing_path_for_role(user.role), status_code=302)
    return FileResponse("static/login.html")


@router.post("/api/login")
async def api_login(request: Request, body: LoginBody):
    enforce_login_rate_limit(request)
    username = body.username.strip().lower()
    async with async_session() as session:
        user = (
            await session.execute(select(User).where(User.username == username))
        ).scalar_one_or_none()
        # Always run bcrypt (dummy hash if user missing/inactive) to avoid timing leaks
        password_ok = verify_login_password(
            body.password,
            user.password_hash if user and user.active else None,
        )
        if not user or not user.active or not password_ok:
            raise HTTPException(status_code=401, detail="Ungültige Anmeldedaten")
        request.session["user_id"] = user.id
        request.session["username"] = user.username
        request.session["role"] = user.role
        return {
            "user": user_public_dict(user),
            "redirect": landing_path_for_role(user.role),
        }


@router.post("/login")
async def login_form(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    """HTML form POST fallback."""
    try:
        result = await api_login(request, LoginBody(username=username, password=password))
        return RedirectResponse(result["redirect"], status_code=302)
    except HTTPException:
        return RedirectResponse("/login?error=1", status_code=302)


@router.post("/api/logout")
async def api_logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.get("/logout")
async def logout_page(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


@router.get("/api/me")
async def api_me(user: User = Depends(get_current_user)):
    from app.settings_store import get_public_settings

    return {
        "user": user_public_dict(user),
        "settings": await get_public_settings(),
    }


@router.get("/api/users")
async def api_list_users(_admin: User = Depends(require_role("admin"))):
    async with async_session() as session:
        rows = list((await session.execute(select(User).order_by(User.username))).scalars().all())
        return {"users": [user_public_dict(u) for u in rows]}


@router.post("/api/users")
async def api_create_user(body: CreateUserBody, _admin: User = Depends(require_role("admin"))):
    role = normalize_role(body.role)
    if role not in ALL_ROLES:
        raise HTTPException(status_code=400, detail=f"Ungültige Rolle: {role}")
    username = body.username.strip().lower()
    async with async_session() as session:
        existing = (
            await session.execute(select(User).where(User.username == username))
        ).scalar_one_or_none()
        if existing:
            raise HTTPException(status_code=409, detail="Benutzername bereits vergeben")
        user = User(
            username=username,
            password_hash=hash_password(body.password),
            display_name=body.display_name.strip(),
            role=role,
            active=True,
            created_at=datetime.now(timezone.utc),
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return {"user": user_public_dict(user)}


@router.post("/api/me/password")
async def api_change_own_password(body: ChangePasswordBody, user: User = Depends(get_current_user)):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Aktuelles Passwort ist falsch")
    if body.current_password == body.new_password:
        raise HTTPException(status_code=400, detail="Neues Passwort muss anders sein")
    async with async_session() as session:
        db_user = await session.get(User, user.id)
        if not db_user:
            raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")
        db_user.password_hash = hash_password(body.new_password)
        await session.commit()
    return {"ok": True}


@router.post("/api/users/{user_id}/reset-password")
async def api_admin_reset_password(
    user_id: int,
    body: AdminResetPasswordBody,
    _admin: User = Depends(require_role("admin")),
):
    async with async_session() as session:
        target = await session.get(User, user_id)
        if not target:
            raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")
        target.password_hash = hash_password(body.password)
        await session.commit()
        return {"user": user_public_dict(target)}
