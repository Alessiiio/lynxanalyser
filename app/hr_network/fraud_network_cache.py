"""Disk cache for fraud-network deep analyzes (shared across users)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_TTL_SEC = 7 * 24 * 3600
_CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "fraud_network_cache"


def _uid_digits(uid: str | None) -> str:
    return re.sub(r"\D", "", uid or "")


def cache_key(
    *,
    level: int,
    company_name: str | None,
    company_uid: str | None,
    max_person_searches: int = 0,
) -> str:
    """Key by firm + level only (max_person_searches ignored for stable hits)."""
    uid = _uid_digits(company_uid)
    name = (company_name or "").strip().lower()
    # Prefer UID when present so name spelling variants still hit
    identity = uid or name
    # v6: Zefix/SHAB person mandate primary; Moneyhouse fill-in only (2026-08)
    raw = f"v6|{level}|{identity}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


def cache_keys(
    *,
    level: int,
    company_name: str | None,
    company_uid: str | None,
) -> list[str]:
    """UID and name keys — load tries both; store writes both when available."""
    keys: list[str] = []
    seen: set[str] = set()
    uid = _uid_digits(company_uid)
    name = (company_name or "").strip().lower()
    for identity in (uid, name):
        if not identity:
            continue
        raw = f"v5|{level}|{identity}"
        key = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]
        if key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


def _path(key: str) -> Path:
    return _CACHE_DIR / f"{key}.json"


def load_cached(key: str) -> dict[str, Any] | None:
    path = _path(key)
    try:
        if not path.is_file():
            return None
        if time.time() - path.stat().st_mtime > _TTL_SEC:
            logger.info("fraud-network cache expired: %s", key)
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "nodes" not in data:
            return None
        logger.info("fraud-network cache hit: %s", key)
        return data
    except (OSError, json.JSONDecodeError, TypeError) as e:
        logger.warning("fraud-network cache load failed: %s", e)
        return None


def load_cached_for_company(
    *,
    level: int,
    company_name: str | None,
    company_uid: str | None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Return (payload, matching_key) trying UID then name."""
    for key in cache_keys(level=level, company_name=company_name, company_uid=company_uid):
        hit = load_cached(key)
        if hit is not None:
            return hit, key
    return None, None


def store_cached(key: str, payload: dict[str, Any]) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        to_store = {k: v for k, v in payload.items() if k not in ("cached", "cached_at")}
        path = _path(key)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(to_store, ensure_ascii=False, default=str, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(tmp, path)
        logger.info("fraud-network cache store: %s (%s bytes)", key, path.stat().st_size)
    except (OSError, TypeError, ValueError) as e:
        logger.warning("fraud-network cache store failed: %s", e)


def store_cached_for_company(
    *,
    level: int,
    company_name: str | None,
    company_uid: str | None,
    payload: dict[str, Any],
) -> None:
    keys = cache_keys(level=level, company_name=company_name, company_uid=company_uid)
    if not keys:
        return
    for key in keys:
        store_cached(key, payload)


def cached_at_iso(key: str) -> str | None:
    path = _path(key)
    try:
        if not path.is_file():
            return None
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(path.stat().st_mtime))
    except OSError:
        return None


def cache_status_for_company(
    *,
    company_name: str | None,
    company_uid: str | None,
) -> dict[str, Any]:
    """Presence of L4/L5 disk cache for this firm (for UI after normal search)."""
    out: dict[str, Any] = {"levels": {}}
    for level in (4, 5):
        hit, key = load_cached_for_company(
            level=level, company_name=company_name, company_uid=company_uid
        )
        if hit is not None and key:
            out["levels"][str(level)] = {
                "cached": True,
                "cached_at": cached_at_iso(key),
                "nodes": len(hit.get("nodes") or []),
                "persons": len(hit.get("persons_table") or hit.get("persons") or []),
            }
        else:
            out["levels"][str(level)] = {"cached": False}
    return out
