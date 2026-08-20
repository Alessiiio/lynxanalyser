"""ZefixREST web API client (shab/search, firm/search) — faster than PublicREST day scans."""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.request import Request

import config
from app.checks.zefix_check import _auth_header

_ZEFIX_REST_BASE = "https://www.zefix.admin.ch/ZefixREST/api/v1"
_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (compatible; lynx/1.0)",
}

_HEALTH: dict[str, Any] = {
    "last_success_at": None,
    "last_error_at": None,
    "last_error": None,
    "consecutive_errors": 0,
}


def zefix_health() -> dict[str, Any]:
    return dict(_HEALTH)


def _record_success() -> None:
    _HEALTH["last_success_at"] = datetime.now(timezone.utc).isoformat()
    _HEALTH["consecutive_errors"] = 0


def _record_error(exc: Exception) -> None:
    _HEALTH["last_error_at"] = datetime.now(timezone.utc).isoformat()
    _HEALTH["last_error"] = str(exc)[:200]
    _HEALTH["consecutive_errors"] = int(_HEALTH.get("consecutive_errors") or 0) + 1


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def network_status_from_health(
    health: dict[str, Any] | None = None,
) -> Literal["ok", "degraded", "down"]:
    """Derive coarse network status from in-process Zefix call health."""
    h = health if health is not None else _HEALTH
    errors = int(h.get("consecutive_errors") or 0)
    last_ok = h.get("last_success_at")
    last_err = h.get("last_error_at")

    # Never called since process start → neutral ok
    if not last_ok and not last_err:
        return "ok"
    if errors >= 3 or (not last_ok and last_err):
        return "down"
    if 1 <= errors <= 2:
        return "degraded"

    ok_at = _parse_iso(last_ok)
    err_at = _parse_iso(last_err)
    if ok_at is not None:
        age_min = (datetime.now(timezone.utc) - ok_at).total_seconds() / 60.0
        # Stale success with no later error → nothing tried recently
        if age_min > 30 and (err_at is None or err_at <= ok_at):
            return "degraded"
    return "ok"


def zefix_rest_post(path: str, payload: dict) -> dict:
    headers = {**_HEADERS, "Authorization": _auth_header()}
    req = Request(
        f"{_ZEFIX_REST_BASE}{path}",
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read().decode())
        _record_success()
        return data
    except Exception as e:
        _record_error(e)
        raise
