"""Manual goldlist of known-legitimate domains for calibration pipeline."""

from __future__ import annotations

import os
import threading
from typing import Optional

import config

_lock = threading.Lock()
_cached_domains: set[str] | None = None


def _goldlist_path() -> str:
    return os.path.abspath(config.GOLDLIST_PATH)


def _normalize_domain(domain: str) -> str:
    return domain.lower().strip().removeprefix("www.").strip("/")


def load_goldlist() -> set[str]:
    global _cached_domains
    path = _goldlist_path()
    domains: set[str] = set()
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                domains.add(_normalize_domain(line))
    with _lock:
        _cached_domains = domains
    return domains


def get_goldlist() -> list[str]:
    return sorted(load_goldlist())


def is_goldlisted(domain: str) -> bool:
    global _cached_domains
    if _cached_domains is None:
        load_goldlist()
    assert _cached_domains is not None
    return _normalize_domain(domain) in _cached_domains


def add_domain(domain: str) -> bool:
    """Add domain if not present. Returns True if newly added."""
    domain = _normalize_domain(domain)
    if not domain:
        return False
    domains = load_goldlist()
    if domain in domains:
        return False
    path = _goldlist_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{domain}\n")
    domains.add(domain)
    with _lock:
        global _cached_domains
        _cached_domains = domains
    return True


def remove_domain(domain: str) -> bool:
    domain = _normalize_domain(domain)
    domains = load_goldlist()
    if domain not in domains:
        return False
    domains.remove(domain)
    path = _goldlist_path()
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Trusted domains (one per line)\n")
        for d in sorted(domains):
            f.write(f"{d}\n")
    with _lock:
        global _cached_domains
        _cached_domains = domains
    return True


def goldlist_info(domain: str) -> Optional[dict]:
    if is_goldlisted(domain):
        return {"listed": True, "note": "Domain steht auf der internen Goldlist"}
    return {"listed": False}
