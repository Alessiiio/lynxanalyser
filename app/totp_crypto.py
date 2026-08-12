"""Fernet helpers for TOTP secrets at rest (TOTP_ENCRYPTION_KEY)."""

from __future__ import annotations

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

import config

logger = logging.getLogger(__name__)

_fernet: Fernet | None = None


def _key_material() -> bytes:
    raw = (config.TOTP_ENCRYPTION_KEY or "").strip()
    if raw:
        try:
            # Accept Fernet-format key (url-safe base64, 32 bytes decoded)
            key = raw.encode("ascii") if isinstance(raw, str) else raw
            Fernet(key)  # validate
            return key
        except Exception:
            # Allow hex or arbitrary string → HKDF into Fernet key
            digest = hashlib.sha256(raw.encode("utf-8")).digest()
            return base64.urlsafe_b64encode(digest)
    # Dev-only derivation (config already warned / blocked prod)
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"lynx-totp-dev-salt-v1",
        info=b"lynx-totp-encryption",
    ).derive(config.SESSION_SECRET.encode("utf-8"))
    return base64.urlsafe_b64encode(derived)


def get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_key_material())
    return _fernet


def encrypt_totp_secret(plain_secret: str) -> str:
    token = get_fernet().encrypt(plain_secret.encode("utf-8"))
    return token.decode("ascii")


def decrypt_totp_secret(ciphertext: str) -> str | None:
    if not ciphertext:
        return None
    try:
        return get_fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        logger.warning("TOTP secret decrypt failed")
        return None
