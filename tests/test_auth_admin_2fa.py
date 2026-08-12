"""Auth admin guards + mandatory TOTP / backup-code flows."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pyotp
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Isolate DB / secrets before app modules bind the engine
_fd, _DB_PATH = tempfile.mkstemp(suffix="-lynx-auth-test.db")
os.close(_fd)
os.environ["DATABASE_PATH"] = _DB_PATH
os.environ["SESSION_SECRET"] = "test-session-secret-auth-admin-2fa-32b"
os.environ["TOTP_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
os.environ["ENVIRONMENT"] = "development"
os.environ["LOGIN_RATE_LIMIT_PER_MINUTE"] = "0"
os.environ["LOGIN_2FA_RATE_LIMIT_PER_MINUTE"] = "0"
os.environ["RATE_LIMIT_PER_MINUTE"] = "0"
for _k in (
    "SEED_ADMIN_PASSWORD",
    "SEED_CASE_MANAGER_PASSWORD",
    "SEED_COMPLIANCE_PASSWORD",
    "SEED_ALESSIO_PASSWORD",
    "SEED_ANALYST_PASSWORD",
):
    os.environ.pop(_k, None)

import config  # noqa: E402

config.DATABASE_PATH = _DB_PATH
config.SESSION_SECRET = os.environ["SESSION_SECRET"]
config.TOTP_ENCRYPTION_KEY = os.environ["TOTP_ENCRYPTION_KEY"]
config.LOGIN_RATE_LIMIT_PER_MINUTE = 0
config.LOGIN_2FA_RATE_LIMIT_PER_MINUTE = 0
config.RATE_LIMIT_PER_MINUTE = 0
config.SEED_ADMIN_PASSWORD = ""
config.SEED_CASE_MANAGER_PASSWORD = ""
config.SEED_COMPLIANCE_PASSWORD = ""
config.SEED_ALESSIO_PASSWORD = ""

from app import database as db  # noqa: E402
from app.auth import (  # noqa: E402
    enable_user_2fa,
    generate_backup_codes,
    generate_totp_secret,
    hash_password,
    verify_and_consume_backup_code,
    verify_totp_code,
)
from app.database import User  # noqa: E402
from app.totp_crypto import decrypt_totp_secret, encrypt_totp_secret  # noqa: E402

# Rebind engine to temp DB immediately
_path = os.path.abspath(_DB_PATH)
db.engine = create_async_engine(f"sqlite+aiosqlite:///{_path}", echo=False)
db.async_session = async_sessionmaker(db.engine, expire_on_commit=False)


@pytest.fixture(scope="session", autouse=True)
def _cleanup_db():
    yield
    try:
        Path(_DB_PATH).unlink(missing_ok=True)
    except OSError:
        pass


async def _add_user(
    username: str,
    password: str,
    role: str,
    *,
    active: bool = True,
    totp: bool = False,
    secret: str | None = None,
    backup_codes: list[str] | None = None,
) -> User:
    async with db.async_session() as session:
        user = User(
            username=username,
            password_hash=hash_password(password),
            display_name=username.title(),
            role=role,
            active=active,
            created_at=datetime.now(timezone.utc),
            totp_enabled=False,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        if totp:
            sec = secret or generate_totp_secret()
            codes = backup_codes or generate_backup_codes()
            enable_user_2fa(user, sec, codes)
            await session.commit()
            await session.refresh(user)
            user._test_secret = sec  # type: ignore[attr-defined]
            user._test_backup = codes  # type: ignore[attr-defined]
        return user


@pytest_asyncio.fixture
async def client():
    await db.init_db()
    async with db.async_session() as session:
        for u in (await session.execute(select(User))).scalars().all():
            await session.delete(u)
        await session.commit()

    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ── Unit: crypto / TOTP / backup ─────────────────────────────────────────


def test_totp_encrypt_roundtrip():
    enc = encrypt_totp_secret("JBSWY3DPEHPK3PXP")
    assert enc != "JBSWY3DPEHPK3PXP"
    assert decrypt_totp_secret(enc) == "JBSWY3DPEHPK3PXP"


def test_totp_verify_window():
    secret = generate_totp_secret()
    code = pyotp.TOTP(secret).now()
    assert verify_totp_code(secret, code) is True
    assert verify_totp_code(secret, "000000") is False


def test_backup_code_one_time_use():
    user = User(
        username="x",
        password_hash=hash_password("password12345"),
        display_name="X",
        role="admin",
        active=True,
        created_at=datetime.now(timezone.utc),
    )
    codes = generate_backup_codes(3)
    enable_user_2fa(user, generate_totp_secret(), codes)
    assert verify_and_consume_backup_code(user, codes[0]) is True
    assert verify_and_consume_backup_code(user, codes[0]) is False
    assert verify_and_consume_backup_code(user, codes[1]) is True


# ── API: soft-delete / last-admin / self-demote / inactive login ──────────


async def _login_full(client: AsyncClient, username: str, password: str, *, secret: str | None = None):
    r = await client.post("/api/login", json={"username": username, "password": password})
    data = r.json()
    if data.get("needs_2fa") and secret:
        code = pyotp.TOTP(secret).now()
        r2 = await client.post("/api/login/2fa", json={"code": code})
        assert r2.status_code == 200, r2.text
        return r2.json()
    return data


@pytest.mark.asyncio
async def test_inactive_user_cannot_login(client):
    await _add_user("inactive1", "password12345", "case_manager", active=False)
    r = await client.post("/api/login", json={"username": "inactive1", "password": "password12345"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_last_admin_cannot_deactivate(client):
    admin = await _add_user("soloadmin", "password12345", "admin", totp=True)
    secret = admin._test_secret  # type: ignore[attr-defined]
    await _login_full(client, "soloadmin", "password12345", secret=secret)
    r = await client.patch(f"/api/users/{admin.id}", json={"active": False})
    assert r.status_code == 400
    assert "Admin" in r.json()["detail"] or "admin" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_self_demote_requires_two_admins(client):
    a1 = await _add_user("admin_a", "password12345", "admin", totp=True)
    await _add_user("cm1", "password12345", "case_manager", totp=True)
    secret = a1._test_secret  # type: ignore[attr-defined]
    await _login_full(client, "admin_a", "password12345", secret=secret)
    r = await client.patch(f"/api/users/{a1.id}", json={"role": "case_manager"})
    assert r.status_code == 400

    await _add_user("admin_b", "password12345", "admin", totp=True)
    r2 = await client.patch(f"/api/users/{a1.id}", json={"role": "case_manager"})
    assert r2.status_code == 200, r2.text
    assert r2.json()["user"]["role"] == "case_manager"


@pytest.mark.asyncio
async def test_soft_delete_and_reactivate(client):
    admin = await _add_user("admin_main", "password12345", "admin", totp=True)
    target = await _add_user("victim", "password12345", "case_manager", totp=True)
    secret = admin._test_secret  # type: ignore[attr-defined]
    await _login_full(client, "admin_main", "password12345", secret=secret)

    r = await client.patch(f"/api/users/{target.id}", json={"active": False})
    assert r.status_code == 200
    assert r.json()["user"]["active"] is False

    await client.post("/api/logout")
    r_login = await client.post(
        "/api/login", json={"username": "victim", "password": "password12345"}
    )
    assert r_login.status_code == 401

    await _login_full(client, "admin_main", "password12345", secret=secret)
    r2 = await client.patch(f"/api/users/{target.id}", json={"active": True})
    assert r2.status_code == 200
    assert r2.json()["user"]["active"] is True


@pytest.mark.asyncio
async def test_mandatory_enroll_path(client):
    await _add_user("newbie", "password12345", "case_manager", totp=False)
    r = await client.post("/api/login", json={"username": "newbie", "password": "password12345"})
    assert r.status_code == 200
    data = r.json()
    assert data.get("needs_enroll") is True
    assert data.get("redirect") == "/enroll-2fa"

    me = await client.get("/api/me")
    assert me.status_code == 401

    start = await client.post("/api/2fa/enroll/start")
    assert start.status_code == 200, start.text
    secret = start.json()["secret"]
    code = pyotp.TOTP(secret).now()
    confirm = await client.post("/api/2fa/enroll/confirm", json={"code": code})
    assert confirm.status_code == 200, confirm.text
    body = confirm.json()
    assert body["user"]["totp_enabled"] is True
    assert len(body["backup_codes"]) == 10

    me2 = await client.get("/api/me")
    assert me2.status_code == 200


@pytest.mark.asyncio
async def test_totp_login_and_backup_one_time(client):
    codes = generate_backup_codes()
    secret = generate_totp_secret()
    user = await _add_user(
        "totper",
        "password12345",
        "compliance",
        totp=True,
        secret=secret,
        backup_codes=codes,
    )
    assert user.totp_enabled

    r = await client.post("/api/login", json={"username": "totper", "password": "password12345"})
    assert r.json().get("needs_2fa") is True
    assert (await client.get("/api/me")).status_code == 401

    bad = await client.post("/api/login/2fa", json={"code": "000000"})
    assert bad.status_code == 401

    good = await client.post("/api/login/2fa", json={"code": pyotp.TOTP(secret).now()})
    assert good.status_code == 200
    assert (await client.get("/api/me")).status_code == 200

    await client.post("/api/logout")
    await client.post("/api/login", json={"username": "totper", "password": "password12345"})
    b1 = await client.post("/api/login/2fa", json={"backup_code": codes[0]})
    assert b1.status_code == 200
    await client.post("/api/logout")
    await client.post("/api/login", json={"username": "totper", "password": "password12345"})
    b2 = await client.post("/api/login/2fa", json={"backup_code": codes[0]})
    assert b2.status_code == 401


@pytest.mark.asyncio
async def test_admin_reset_2fa_other_user_not_self(client):
    admin = await _add_user("adm_r", "password12345", "admin", totp=True)
    other = await _add_user("oth_r", "password12345", "case_manager", totp=True)
    secret = admin._test_secret  # type: ignore[attr-defined]
    await _login_full(client, "adm_r", "password12345", secret=secret)

    self_r = await client.post(f"/api/users/{admin.id}/reset-2fa")
    assert self_r.status_code == 400

    ok = await client.post(f"/api/users/{other.id}/reset-2fa")
    assert ok.status_code == 200
    assert ok.json()["user"]["totp_enabled"] is False

    await client.post("/api/logout")
    r = await client.post("/api/login", json={"username": "oth_r", "password": "password12345"})
    assert r.json().get("needs_enroll") is True
