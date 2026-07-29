"""IOSCO I-SCAN international securities warnings lookup.

Official portal: https://www.iosco.org/i-scan/
API docs: https://api.iosco.org/v1/i-scan/?p=getting-started
Request API key: api@iosco.org
"""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlparse

import httpx

import config

_API_URL = "https://api.iosco.org/v1/i-scan/warnings"
_ISCAN_PORTAL = "https://www.iosco.org/i-scan/"
_TIMEOUT = 25.0


def _host_from(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if "://" not in value and not value.startswith("//"):
        candidate = value.split("/")[0].split("?")[0]
    else:
        if value.startswith("//"):
            value = "https:" + value
        elif not value.startswith(("http://", "https://")):
            value = "https://" + value
        candidate = urlparse(value).netloc
    return candidate.lower().removeprefix("www.")


def _warning_hosts(warning: dict[str, Any]) -> set[str]:
    hosts: set[str] = set()
    for key in ("domain_name", "fqdn", "url"):
        host = _host_from(str(warning.get(key) or ""))
        if host:
            hosts.add(host)
    for item in warning.get("other_urls") or []:
        host = _host_from(str(item))
        if host:
            hosts.add(host)
    return hosts


def _hosts_match(target: str, candidate: str) -> bool:
    if not target or not candidate:
        return False
    if target == candidate:
        return True
    return target.endswith(f".{candidate}") or candidate.endswith(f".{target}")


def _domain_match(warning: dict[str, Any], domain: str) -> bool:
    target = _host_from(domain)
    if not target:
        return False
    return any(_hosts_match(target, host) for host in _warning_hosts(warning))


def _company_match(warning: dict[str, Any], company_lower: str) -> bool:
    if len(company_lower) < 4:
        return False
    names: list[str] = []
    commercial = warning.get("commercial_name")
    if isinstance(commercial, str) and commercial.strip():
        names.append(commercial.strip().lower())
    for field in ("corporate_names", "other_commercial_names"):
        for item in warning.get(field) or []:
            if isinstance(item, str) and item.strip():
                names.append(item.strip().lower())
    for name in names:
        if len(name) < 4:
            continue
        if company_lower in name or name in company_lower:
            return True
    return False


def _normalize_warnings(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        return [payload]
    return []


def _warning_display_name(warning: dict[str, Any], fallback: str) -> str:
    commercial = warning.get("commercial_name")
    if isinstance(commercial, str) and commercial.strip():
        return commercial.strip()
    domain_name = warning.get("domain_name")
    if isinstance(domain_name, str) and domain_name.strip():
        return domain_name.strip()
    return fallback


async def _query_iscan(keywords: str) -> list[dict[str, Any]]:
    if not config.IOSCO_ISCAN_API_KEY:
        raise RuntimeError("IOSCO_ISCAN_API_KEY not configured")

    query = keywords.strip()[:200]
    if not query:
        return []

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(
            _API_URL,
            params={"keywords": query},
            headers={
                "Authorization": f"Bearer {config.IOSCO_ISCAN_API_KEY}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )

    if resp.status_code == 401:
        raise RuntimeError("IOSCO I-SCAN API key invalid or expired — contact api@iosco.org")
    if resp.status_code == 429:
        raise RuntimeError("IOSCO I-SCAN rate limit exceeded — retry later")
    resp.raise_for_status()

    return _normalize_warnings(resp.json())


def _match_to_result(
    warning: dict[str, Any],
    match_type: str,
    fallback_name: str,
) -> dict[str, Any]:
    nca_url = warning.get("nca_url")
    entity_url = warning.get("url")
    return {
        "match_type": match_type,
        "name": _warning_display_name(warning, fallback_name),
        "url": nca_url if isinstance(nca_url, str) and nca_url else entity_url,
        "regulator": warning.get("nca_name"),
        "jurisdiction": warning.get("nca_jurisdiction"),
        "warning_id": warning.get("id"),
        "iscan_portal": _ISCAN_PORTAL,
        "domain_name": warning.get("domain_name"),
    }


async def find_iscan_warning(domain: str, company_name: str | None = None) -> Optional[dict]:
    """Search I-SCAN for domain or company matches."""
    domain = domain.lower().removeprefix("www.")
    company_lower = (company_name or "").strip().lower()

    domain_results = await _query_iscan(domain)
    for warning in domain_results:
        if _domain_match(warning, domain):
            return _match_to_result(warning, "domain", domain)

    if company_lower:
        company_results = await _query_iscan(company_lower)
        for warning in company_results:
            if _company_match(warning, company_lower):
                return _match_to_result(warning, "company", company_lower)

    return None
