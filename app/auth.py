"""Password hashing and session helpers for team login."""

from __future__ import annotations

import bcrypt

from app.database import User

# Precomputed once at import so missing-user logins still run a full bcrypt check
# (closes username-enumeration via response timing).
_DUMMY_PASSWORD_HASH = bcrypt.hashpw(
    b"timing-equalization-dummy-not-a-real-password",
    bcrypt.gensalt(),
).decode("utf-8")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def verify_login_password(password: str, password_hash: str | None) -> bool:
    """Always runs bcrypt, even when no user/hash exists (constant-time login path)."""
    return verify_password(password, password_hash or _DUMMY_PASSWORD_HASH)


ROLE_CASE_MANAGER = "case_manager"
ROLE_COMPLIANCE = "compliance"
ROLE_ADMIN = "admin"

# Legacy alias kept for migrations / old sessions
ROLE_ANALYST_LEGACY = "analyst"

ALL_ROLES = {ROLE_CASE_MANAGER, ROLE_COMPLIANCE, ROLE_ADMIN}

ROLE_LABELS = {
    ROLE_ADMIN: "Admin",
    ROLE_CASE_MANAGER: "Case Manager",
    ROLE_COMPLIANCE: "Compliance",
    ROLE_ANALYST_LEGACY: "Case Manager",
}


def normalize_role(role: str | None) -> str:
    """Map legacy role names to current ones."""
    r = (role or "").strip().lower()
    if r == ROLE_ANALYST_LEGACY:
        return ROLE_CASE_MANAGER
    return r


def role_label(role: str | None) -> str:
    r = normalize_role(role)
    return ROLE_LABELS.get(r, role or "User")


def user_public_dict(user: User) -> dict:
    role = normalize_role(user.role)
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role": role,
        "role_label": role_label(role),
        "active": user.active,
    }


def landing_path_for_role(role: str) -> str:
    if normalize_role(role) == ROLE_COMPLIANCE:
        return "/compliance"
    return "/"
