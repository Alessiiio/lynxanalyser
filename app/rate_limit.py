"""Simple in-memory rate limiting per client IP."""

from __future__ import annotations

import time
from collections import defaultdict
from typing import DefaultDict

import config

_requests: DefaultDict[str, list[float]] = defaultdict(list)
_login_requests: DefaultDict[str, list[float]] = defaultdict(list)
_login_2fa_requests: DefaultDict[str, list[float]] = defaultdict(list)


def _limited(bucket: DefaultDict[str, list[float]], client_ip: str, limit: int) -> bool:
    if limit <= 0:
        return False
    now = time.time()
    window_start = now - 60.0
    bucket[client_ip] = [t for t in bucket[client_ip] if t > window_start]
    if len(bucket[client_ip]) >= limit:
        return True
    bucket[client_ip].append(now)
    return False


def is_rate_limited(client_ip: str) -> bool:
    return _limited(_requests, client_ip, config.RATE_LIMIT_PER_MINUTE)


def is_login_rate_limited(client_ip: str) -> bool:
    return _limited(_login_requests, client_ip, config.LOGIN_RATE_LIMIT_PER_MINUTE)


def is_login_2fa_rate_limited(client_ip: str) -> bool:
    return _limited(_login_2fa_requests, client_ip, config.LOGIN_2FA_RATE_LIMIT_PER_MINUTE)
