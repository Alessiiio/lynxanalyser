"""Internal blocklist of analyst-confirmed fraudulent domains."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any, Optional

import config

_lock = threading.Lock()
_cached_entries: dict[str, dict[str, Any]] | None = None


def _blocklist_path() -> str:
    return os.path.abspath(config.BLOCKLIST_PATH)


def _normalize_domain(domain: str) -> str:
    return domain.lower().strip().removeprefix("www.").strip("/")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_blocklist() -> dict[str, dict[str, Any]]:
    global _cached_entries
    path = _blocklist_path()
    entries: dict[str, dict[str, Any]] = {}
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                for domain, meta in raw.items():
                    if isinstance(meta, dict):
                        entries[_normalize_domain(domain)] = meta
        except (json.JSONDecodeError, OSError):
            entries = {}
    with _lock:
        _cached_entries = entries
    return entries


def get_blocklist() -> list[dict[str, Any]]:
    entries = load_blocklist()
    return [
        {"domain": domain, **meta}
        for domain, meta in sorted(entries.items())
    ]


def is_blocklisted(domain: str) -> bool:
    global _cached_entries
    if _cached_entries is None:
        load_blocklist()
    assert _cached_entries is not None
    return _normalize_domain(domain) in _cached_entries


def blocklist_entry(domain: str) -> Optional[dict[str, Any]]:
    domain = _normalize_domain(domain)
    entries = load_blocklist()
    meta = entries.get(domain)
    if not meta:
        return None
    return {"domain": domain, **meta}


def blocklist_info(domain: str) -> Optional[dict]:
    entry = blocklist_entry(domain)
    if entry:
        return {"listed": True, **entry}
    return {"listed": False}


def _save_entries(entries: dict[str, dict[str, Any]]) -> None:
    path = _blocklist_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2, sort_keys=True)
    with _lock:
        global _cached_entries
        _cached_entries = entries


def confirm_fraud(
    domain: str,
    *,
    url: str = "",
    fraud_category: str = "general_suspicious",
    note: str = "",
    analyst_id: str = "unknown",
    llm_answers: list[dict] | None = None,
) -> dict[str, Any]:
    """Add or update a domain on the internal blocklist."""
    domain = _normalize_domain(domain)
    if not domain:
        raise ValueError("domain required")

    entries = load_blocklist()
    record: dict[str, Any] = {
        "fraud_category": fraud_category,
        "note": note.strip()[:2000],
        "url": url.strip()[:2048],
        "analyst_id": analyst_id or "unknown",
        "confirmed_at": _now_iso(),
    }
    if llm_answers:
        record["llm_answers"] = llm_answers[:12]

    entries[domain] = record
    _save_entries(entries)
    return {"domain": domain, **record}


def remove_fraud(domain: str) -> bool:
    domain = _normalize_domain(domain)
    entries = load_blocklist()
    if domain not in entries:
        return False
    del entries[domain]
    _save_entries(entries)
    return True
