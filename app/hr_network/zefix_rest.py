"""ZefixREST web API client (shab/search, firm/search) — faster than PublicREST day scans."""

from __future__ import annotations

import json
import urllib.request
from urllib.request import Request

import config
from app.checks.zefix_check import _auth_header

_ZEFIX_REST_BASE = "https://www.zefix.admin.ch/ZefixREST/api/v1"
_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (compatible; lynx/1.0)",
}


def zefix_rest_post(path: str, payload: dict) -> dict:
    headers = {**_HEADERS, "Authorization": _auth_header()}
    req = Request(
        f"{_ZEFIX_REST_BASE}{path}",
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())
