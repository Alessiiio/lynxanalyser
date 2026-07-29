"""In-memory scan result cache with TTL."""

from __future__ import annotations

import time
from typing import Any, Optional

import config

_cache: dict[str, tuple[float, Any]] = {}


def _cache_key(domain: str, company: str | None, transaction_key: str = "") -> str:
    company_part = (company or "").strip().lower()
    return f"{domain.lower()}|{company_part}{transaction_key}"


def transaction_cache_suffix(transaction: Any | None) -> str:
    if not transaction:
        return ""
    amount = getattr(transaction, "amount", None)
    if amount is None:
        return ""
    currency = getattr(transaction, "currency", "CHF") or "CHF"
    purpose = (getattr(transaction, "purpose", None) or "")[:24]
    return f"|tx:{amount}:{currency}:{purpose}"


def get_cached_report(
    domain: str,
    company: str | None = None,
    transaction: Any | None = None,
) -> Optional[Any]:
    if config.CACHE_TTL_SECONDS <= 0:
        return None
    key = _cache_key(domain, company, transaction_cache_suffix(transaction))
    entry = _cache.get(key)
    if not entry:
        return None
    expires_at, report = entry
    if time.time() > expires_at:
        _cache.pop(key, None)
        return None
    return report


def set_cached_report(
    domain: str,
    company: str | None,
    report: Any,
    transaction: Any | None = None,
) -> None:
    if config.CACHE_TTL_SECONDS <= 0:
        return
    key = _cache_key(domain, company, transaction_cache_suffix(transaction))
    _cache[key] = (time.time() + config.CACHE_TTL_SECONDS, report)


def invalidate_domain_cache(domain: str) -> None:
    """Drop all cached reports for a domain (any company variant)."""
    prefix = f"{domain.lower()}|"
    for key in list(_cache):
        if key.startswith(prefix):
            _cache.pop(key, None)
