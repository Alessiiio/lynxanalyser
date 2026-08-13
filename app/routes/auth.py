"""Login / logout / current user / admin user management / 2FA."""

from __future__ import annotations

import base64
import io
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.auth import (
    ALL_ROLES,
    MAX_2FA_FAILURES,
    clear_user_2fa,
    enable_user_2fa,
    generate_backup_codes,
    generate_totp_secret,
    get_user_totp_secret,
    grant_full_session,
    hash_password,
    is_admin_role,
    landing_path_for_role,
    normalize_role,
    pending_still_valid,
    start_pending_2fa,
    start_pending_enroll,
    totp_provisioning_uri,
    user_public_dict,
    verify_and_consume_backup_code,
    verify_login_password,
    verify_password,
    verify_totp_code,
)
from app.database import User
from app import database as db
from app.routes.deps import (
    count_active_admins,
    enforce_login_2fa_rate_limit,
    enforce_login_rate_limit,
    get_current_user,
    require_role,
)

logger = logging.getLogger(__name__)

router = APIRouter()


class LoginBody(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class Login2FABody(BaseModel):
    code: Optional[str] = Field(None, max_length=32)
    backup_code: Optional[str] = Field(None, max_length=64)


class CreateUserBody(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)
    password: str = Field(..., min_length=12, max_length=128)
    display_name: str = Field(..., min_length=1, max_length=128)
    role: str = Field(..., min_length=1, max_length=32)


class PatchUserBody(BaseModel):
    role: Optional[str] = Field(None, min_length=1, max_length=32)
    active: Optional[bool] = None
    display_name: Optional[str] = Field(None, min_length=1, max_length=128)


class ChangePasswordBody(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=12, max_length=128)


class AdminResetPasswordBody(BaseModel):
    password: str = Field(..., min_length=12, max_length=128)


class EnrollConfirmBody(BaseModel):
    code: str = Field(..., min_length=6, max_length=16)


def _qr_data_url(otpauth_uri: str) -> str:
    import qrcode

    img = qrcode.make(otpauth_uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


async def _pending_user(request: Request, kind: str) -> User:
    """Load user from pending_2fa or pending_enroll session (not full login)."""
    if kind == "2fa":
        uid = request.session.get("pending_2fa_user_id")
        ts = request.session.get("pending_2fa_at")
    else:
        uid = request.session.get("pending_enroll_user_id")
        ts = request.session.get("pending_enroll_at")
    if not uid or not pending_still_valid(ts):
        request.session.clear()
        raise HTTPException(status_code=401, detail="Sitzung abgelaufen — bitte erneut anmelden")
    async with db.async_session() as session:
        user = await session.get(User, int(uid))
        if not user or not user.active:
            request.session.clear()
            raise HTTPException(status_code=401, detail="Sitzung ungültig")
        return user


@router.get("/login")
async def login_page(request: Request):
    from app.routes.deps import get_optional_user

    user = await get_optional_user(request)
    if user:
        return RedirectResponse(landing_path_for_role(user.role), status_code=302)
    return FileResponse("static/login.html")


@router.get("/enroll-2fa")
async def enroll_2fa_page(request: Request):
    """Enrollment UI — requires pending_enroll session (after password, before full access)."""
    uid = request.session.get("pending_enroll_user_id")
    ts = request.session.get("pending_enroll_at")
    if not uid or not pending_still_valid(ts):
        return RedirectResponse("/login", status_code=302)
    return FileResponse("static/enroll-2fa.html")


@router.get("/account")
async def account_page(user: User = Depends(get_current_user)):
    return FileResponse("static/account.html")


@router.post("/api/login")
async def api_login(request: Request, body: LoginBody):
    from app.audit_log import record_audit

    enforce_login_rate_limit(request)
    username = body.username.strip().lower()
    async with db.async_session() as session:
        user = (
            await session.execute(select(User).where(User.username == username))
        ).scalar_one_or_none()
        # Always run bcrypt (dummy hash if user missing/inactive) to avoid timing leaks
        password_ok = verify_login_password(
            body.password,
            user.password_hash if user and user.active else None,
        )
        if not user or not user.active or not password_ok:
            await record_audit(
                action="login_fail",
                success=False,
                actor_username=username or None,
                detail="invalid_credentials",
                request=request,
            )
            raise HTTPException(status_code=401, detail="Ungültige Anmeldedaten")

        if user.totp_enabled:
            start_pending_2fa(request, user)
            return {
                "needs_2fa": True,
                "methods": ["totp", "backup"],
                "user": {"username": user.username, "display_name": user.display_name},
            }

        # Mandatory 2FA from day 1: force enroll before full session
        start_pending_enroll(request, user)
        return {
            "needs_enroll": True,
            "redirect": "/enroll-2fa",
            "user": {"username": user.username, "display_name": user.display_name},
        }


@router.post("/api/login/2fa")
async def api_login_2fa(request: Request, body: Login2FABody):
    enforce_login_2fa_rate_limit(request)
    code = (body.code or "").strip()
    backup = (body.backup_code or "").strip()
    if not code and not backup:
        raise HTTPException(status_code=400, detail="Code erforderlich")

    uid = request.session.get("pending_2fa_user_id")
    ts = request.session.get("pending_2fa_at")
    if not uid or not pending_still_valid(ts):
        request.session.clear()
        raise HTTPException(status_code=401, detail="Sitzung abgelaufen — bitte erneut anmelden")

    async with db.async_session() as session:
        user = await session.get(User, int(uid))
        if not user or not user.active or not user.totp_enabled:
            request.session.clear()
            raise HTTPException(status_code=401, detail="Sitzung ungültig")

        ok = False
        if backup:
            ok = verify_and_consume_backup_code(user, backup)
            if ok:
                await session.commit()
        elif code:
            secret = get_user_totp_secret(user)
            if secret:
                ok = verify_totp_code(secret, code)

        if not ok:
            from app.audit_log import record_audit

            failures = int(request.session.get("pending_2fa_failures") or 0) + 1
            request.session["pending_2fa_failures"] = failures
            await record_audit(
                action="2fa_fail",
                success=False,
                actor_username=user.username,
                actor_display=user.display_name,
                detail=f"failures={failures}",
                request=request,
            )
            if failures >= MAX_2FA_FAILURES:
                request.session.clear()
                raise HTTPException(
                    status_code=401,
                    detail="Zu viele Fehlversuche — bitte erneut anmelden",
                )
            raise HTTPException(status_code=401, detail="Ungültiger Code")

        from app.audit_log import record_audit

        grant_full_session(request, user)
        await record_audit(
            action="login_ok",
            success=True,
            actor_username=user.username,
            actor_display=user.display_name,
            detail="2fa",
            request=request,
        )
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
    """HTML form POST fallback (password step only; 2FA via JSON UI)."""
    try:
        result = await api_login(request, LoginBody(username=username, password=password))
        if result.get("needs_enroll"):
            return RedirectResponse("/enroll-2fa", status_code=302)
        if result.get("needs_2fa"):
            return RedirectResponse("/login?step=2fa", status_code=302)
        return RedirectResponse(result.get("redirect") or "/", status_code=302)
    except HTTPException:
        return RedirectResponse("/login?error=1", status_code=302)


@router.post("/api/logout")
async def api_logout(request: Request):
    from app.audit_log import record_audit

    uname = request.session.get("username")
    await record_audit(
        action="logout",
        success=True,
        actor_username=uname,
        request=request,
    )
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


@router.get("/api/auth/pending")
async def api_auth_pending(request: Request):
    """Status for login/enroll pages (no full session required)."""
    if request.session.get("pending_2fa_user_id") and pending_still_valid(
        request.session.get("pending_2fa_at")
    ):
        return {"state": "needs_2fa", "methods": ["totp", "backup"]}
    if request.session.get("pending_enroll_user_id") and pending_still_valid(
        request.session.get("pending_enroll_at")
    ):
        return {"state": "needs_enroll"}
    return {"state": "none"}


# ── 2FA enrollment (pending_enroll session OR logged-in without 2FA — latter cleared) ──


@router.post("/api/2fa/enroll/start")
async def api_2fa_enroll_start(request: Request):
    user = await _pending_user(request, "enroll")
    if user.totp_enabled:
        raise HTTPException(status_code=400, detail="2FA ist bereits aktiv")
    secret = generate_totp_secret()
    # Keep pending secret only in session until confirm (avoids half-state in DB)
    request.session["pending_totp_secret"] = secret
    uri = totp_provisioning_uri(secret, user.username)
    return {
        "otpauth_uri": uri,
        "secret": secret,
        "qr_data_url": _qr_data_url(uri),
        "username": user.username,
    }


@router.post("/api/2fa/enroll/confirm")
async def api_2fa_enroll_confirm(request: Request, body: EnrollConfirmBody):
    enforce_login_2fa_rate_limit(request)
    uid = request.session.get("pending_enroll_user_id")
    ts = request.session.get("pending_enroll_at")
    secret = request.session.get("pending_totp_secret")
    if not uid or not pending_still_valid(ts) or not secret:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Sitzung abgelaufen — bitte erneut anmelden")
    if not verify_totp_code(secret, body.code):
        raise HTTPException(status_code=400, detail="Ungültiger Authenticator-Code")

    backup_codes = generate_backup_codes()
    async with db.async_session() as session:
        user = await session.get(User, int(uid))
        if not user or not user.active:
            request.session.clear()
            raise HTTPException(status_code=401, detail="Sitzung ungültig")
        enable_user_2fa(user, secret, backup_codes)
        await session.commit()
        await session.refresh(user)
        grant_full_session(request, user)
        logger.info("2FA enrolled for user=%s", user.username)
        return {
            "user": user_public_dict(user),
            "backup_codes": backup_codes,
            "redirect": landing_path_for_role(user.role),
        }


@router.post("/api/2fa/enroll/cancel")
async def api_2fa_enroll_cancel(request: Request):
    request.session.clear()
    return {"ok": True}


@router.get("/api/users")
async def api_list_users(_admin: User = Depends(require_role("admin"))):
    async with db.async_session() as session:
        rows = list((await session.execute(select(User).order_by(User.username))).scalars().all())
        return {"users": [user_public_dict(u) for u in rows]}


@router.post("/api/users")
async def api_create_user(body: CreateUserBody, _admin: User = Depends(require_role("admin"))):
    role = normalize_role(body.role)
    if role not in ALL_ROLES:
        raise HTTPException(status_code=400, detail=f"Ungültige Rolle: {role}")
    username = body.username.strip().lower()
    async with db.async_session() as session:
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
            totp_enabled=False,
            created_at=datetime.now(timezone.utc),
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        logger.info("User created username=%s role=%s by admin", user.username, role)
        return {"user": user_public_dict(user)}


@router.patch("/api/users/{user_id}")
async def api_patch_user(
    user_id: int,
    body: PatchUserBody,
    request: Request,
    admin: User = Depends(require_role("admin")),
):
    if body.role is None and body.active is None and body.display_name is None:
        raise HTTPException(status_code=400, detail="Keine Änderungen")

    async with db.async_session() as session:
        target = await session.get(User, user_id)
        if not target:
            raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")

        new_role = normalize_role(body.role) if body.role is not None else None
        if new_role is not None and new_role not in ALL_ROLES:
            raise HTTPException(status_code=400, detail=f"Ungültige Rolle: {new_role}")

        was_admin = is_admin_role(target.role)
        will_be_admin = is_admin_role(new_role) if new_role is not None else was_admin
        will_be_active = target.active if body.active is None else bool(body.active)

        # Soft-delete / demote last-admin guards
        losing_admin = was_admin and (not will_be_admin or not will_be_active)
        if losing_admin:
            others = await count_active_admins(session, exclude_user_id=target.id)
            if others < 1:
                raise HTTPException(
                    status_code=400,
                    detail="Letzter aktiver Admin darf nicht deaktiviert oder degradiert werden",
                )

        # Self soft-delete only blocked when last admin (locked decision)
        if body.active is False and target.id == admin.id and was_admin:
            others = await count_active_admins(session, exclude_user_id=target.id)
            if others < 1:
                raise HTTPException(
                    status_code=400,
                    detail="Du kannst dich nicht selbst deaktivieren (letzter Admin)",
                )

        # Self-demote: require ≥2 active admins before demote (≥1 remains)
        if (
            new_role is not None
            and target.id == admin.id
            and was_admin
            and not will_be_admin
        ):
            total_admins = await count_active_admins(session)
            if total_admins < 2:
                raise HTTPException(
                    status_code=400,
                    detail="Self-Demote nur möglich, wenn mindestens zwei aktive Admins existieren",
                )

        old_role = target.role
        old_active = target.active

        if new_role is not None:
            target.role = new_role
        if body.active is not None:
            target.active = bool(body.active)
        if body.display_name is not None:
            target.display_name = body.display_name.strip()

        await session.commit()
        await session.refresh(target)

        logger.info(
            "User patch by=%s target=%s role %s→%s active %s→%s",
            admin.username,
            target.username,
            old_role,
            target.role,
            old_active,
            target.active,
        )

        # Keep own session role in sync
        if target.id == admin.id and request.session.get("user_id") == admin.id:
            request.session["role"] = normalize_role(target.role)

        return {"user": user_public_dict(target)}


@router.delete("/api/users/{user_id}")
async def api_hard_delete_user(
    user_id: int,
    admin: User = Depends(require_role("admin")),
):
    """Hard-delete inactive users only (cleanup). Soft-delete via PATCH active=false first."""
    if user_id == admin.id:
        raise HTTPException(
            status_code=400,
            detail="Du kannst dich nicht selbst endgültig löschen",
        )
    async with db.async_session() as session:
        target = await session.get(User, user_id)
        if not target:
            raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")
        if target.active:
            raise HTTPException(
                status_code=400,
                detail="Aktive Benutzer zuerst deaktivieren (Soft-Delete), dann endgültig löschen",
            )
        # Defense-in-depth: never hard-delete an active admin (active check above);
        # if role=admin but inactive, cleanup is allowed.
        if is_admin_role(target.role) and target.active:
            raise HTTPException(
                status_code=400,
                detail="Aktiver Admin kann nicht endgültig gelöscht werden",
            )
        uname = target.username
        await session.delete(target)
        await session.commit()
        logger.info("User hard-deleted by=%s target=%s", admin.username, uname)
        return {"ok": True, "deleted": uname}


@router.post("/api/me/password")
async def api_change_own_password(body: ChangePasswordBody, user: User = Depends(get_current_user)):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Aktuelles Passwort ist falsch")
    if body.current_password == body.new_password:
        raise HTTPException(status_code=400, detail="Neues Passwort muss anders sein")
    async with db.async_session() as session:
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
    admin: User = Depends(require_role("admin")),
):
    async with db.async_session() as session:
        target = await session.get(User, user_id)
        if not target:
            raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")
        if not target.active:
            raise HTTPException(
                status_code=400,
                detail="Passwort-Reset erst nach Reaktivierung möglich",
            )
        target.password_hash = hash_password(body.password)
        await session.commit()
        logger.info("Password reset by=%s target=%s", admin.username, target.username)
        return {"user": user_public_dict(target)}


@router.post("/api/users/{user_id}/reset-2fa")
async def api_admin_reset_2fa(
    user_id: int,
    admin: User = Depends(require_role("admin")),
):
    """Clear another user's 2FA — not self (recovery by second admin)."""
    if user_id == admin.id:
        raise HTTPException(
            status_code=400,
            detail="Eigenes 2FA kann nicht zurückgesetzt werden — bitte anderen Admin fragen",
        )
    async with db.async_session() as session:
        target = await session.get(User, user_id)
        if not target:
            raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")
        clear_user_2fa(target)
        await session.commit()
        await session.refresh(target)
        logger.info("2FA reset by=%s target=%s", admin.username, target.username)
        return {"user": user_public_dict(target)}
