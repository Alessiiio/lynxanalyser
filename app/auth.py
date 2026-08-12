"""Password hashing, roles, TOTP/backup helpers, and session-facing user dict."""

from __future__ import annotations

import json
import logging
import secrets
import string
from datetime import datetime, timezone

import bcrypt
import pyotp

from app.database import User
from app.totp_crypto import decrypt_totp_secret, encrypt_totp_secret

logger = logging.getLogger(__name__)

# Precomputed once at import so missing-user logins still run a full bcrypt check
# (closes username-enumeration via response timing).
_DUMMY_PASSWORD_HASH = bcrypt.hashpw(
    b"timing-equalization-dummy-not-a-real-password",
    bcrypt.gensalt(),
).decode("utf-8")

BACKUP_CODE_COUNT = 10
BACKUP_CODE_ALPHABET = string.ascii_uppercase + string.digits
# Avoid ambiguous chars
BACKUP_CODE_ALPHABET = "".join(c for c in BACKUP_CODE_ALPHABET if c not in "O01IL")
TOTP_ISSUER = "Lynx"
PENDING_2FA_TTL_SECONDS = 10 * 60
MAX_2FA_FAILURES = 5


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
        "totp_enabled": bool(getattr(user, "totp_enabled", False)),
    }


def landing_path_for_role(role: str) -> str:
    if normalize_role(role) == ROLE_COMPLIANCE:
        return "/compliance"
    return "/"


def generate_totp_secret() -> str:
    return pyotp.random_base32()


def totp_provisioning_uri(secret: str, username: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=TOTP_ISSUER)


def verify_totp_code(secret: str, code: str) -> bool:
    cleaned = "".join(c for c in (code or "") if c.isdigit())
    if len(cleaned) != 6:
        return False
    totp = pyotp.TOTP(secret)
    return bool(totp.verify(cleaned, valid_window=1))


def generate_backup_codes(count: int = BACKUP_CODE_COUNT) -> list[str]:
    codes: list[str] = []
    for _ in range(count):
        raw = "".join(secrets.choice(BACKUP_CODE_ALPHABET) for _ in range(8))
        codes.append(f"{raw[:4]}-{raw[4:]}")
    return codes


def hash_backup_codes(codes: list[str]) -> str:
    hashes = [hash_password(_normalize_backup_code(c)) for c in codes]
    return json.dumps(hashes)


def load_backup_hashes(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(x) for x in data]
    except (json.JSONDecodeError, TypeError):
        pass
    return []


def _normalize_backup_code(code: str) -> str:
    return "".join(c for c in (code or "").upper() if c.isalnum())


def verify_and_consume_backup_code(user: User, code: str) -> bool:
    """Return True and remove matching hash from user.backup_codes_hash if valid."""
    hashes = load_backup_hashes(user.backup_codes_hash)
    if not hashes:
        # Timing equalization
        verify_password(_normalize_backup_code(code) or "x", _DUMMY_PASSWORD_HASH)
        return False
    needle = _normalize_backup_code(code)
    if not needle:
        verify_password("x", _DUMMY_PASSWORD_HASH)
        return False
    for i, h in enumerate(hashes):
        if verify_password(needle, h):
            del hashes[i]
            user.backup_codes_hash = json.dumps(hashes)
            return True
    # No match — still burned one bcrypt against dummy for roughly similar cost
    verify_password(needle, _DUMMY_PASSWORD_HASH)
    return False


def get_user_totp_secret(user: User) -> str | None:
    enc = getattr(user, "totp_secret_encrypted", None)
    if not enc:
        return None
    return decrypt_totp_secret(enc)


def set_user_totp_secret(user: User, plain_secret: str | None) -> None:
    if plain_secret is None:
        user.totp_secret_encrypted = None
    else:
        user.totp_secret_encrypted = encrypt_totp_secret(plain_secret)


def clear_user_2fa(user: User) -> None:
    user.totp_secret_encrypted = None
    user.totp_enabled = False
    user.totp_confirmed_at = None
    user.backup_codes_hash = None
    user.backup_codes_generated_at = None


def enable_user_2fa(user: User, plain_secret: str, backup_codes: list[str]) -> None:
    set_user_totp_secret(user, plain_secret)
    user.totp_enabled = True
    user.totp_confirmed_at = datetime.now(timezone.utc)
    user.backup_codes_hash = hash_backup_codes(backup_codes)
    user.backup_codes_generated_at = datetime.now(timezone.utc)


def is_admin_role(role: str | None) -> bool:
    return normalize_role(role) == ROLE_ADMIN


def grant_full_session(request, user: User) -> None:
    request.session.pop("pending_2fa_user_id", None)
    request.session.pop("pending_2fa_at", None)
    request.session.pop("pending_2fa_failures", None)
    request.session.pop("pending_enroll_user_id", None)
    request.session.pop("pending_enroll_at", None)
    request.session.pop("pending_totp_secret", None)
    request.session["user_id"] = user.id
    request.session["username"] = user.username
    request.session["role"] = normalize_role(user.role)


def start_pending_2fa(request, user: User) -> None:
    request.session.clear()
    request.session["pending_2fa_user_id"] = user.id
    request.session["pending_2fa_at"] = datetime.now(timezone.utc).isoformat()
    request.session["pending_2fa_failures"] = 0


def start_pending_enroll(request, user: User) -> None:
    request.session.clear()
    request.session["pending_enroll_user_id"] = user.id
    request.session["pending_enroll_at"] = datetime.now(timezone.utc).isoformat()


def pending_still_valid(iso_ts: str | None, ttl: int = PENDING_2FA_TTL_SECONDS) -> bool:
    if not iso_ts:
        return False
    try:
        started = datetime.fromisoformat(iso_ts)
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - started).total_seconds()
        return 0 <= age <= ttl
    except (TypeError, ValueError):
        return False
