"""HSTS check — preload list, response header quality, and resilient HTTPS probing."""

from __future__ import annotations

import re

import httpx

from app.checks.base import BaseCheck
from app.checks.utils import USER_AGENT
from app.models import CheckResult, CheckStatus

_MAX_AGE_STRONG = 31_536_000  # 1 year
_MAX_AGE_MINIMUM = 86_400  # 1 day

_MAX_AGE_RE = re.compile(r"max-age\s*=\s*(\d+)", re.IGNORECASE)


def _host_candidates(domain: str) -> list[str]:
    domain = domain.lower().strip(".")
    hosts = [domain]
    if not domain.startswith("www."):
        hosts.append(f"www.{domain}")
    else:
        hosts.append(domain[4:])
    # Preserve order, dedupe
    seen: set[str] = set()
    out: list[str] = []
    for h in hosts:
        if h and h not in seen:
            seen.add(h)
            out.append(h)
    return out


def _parse_hsts_header(value: str) -> dict:
    if not value:
        return {
            "max_age": 0,
            "include_subdomains": False,
            "preload": False,
            "raw": None,
        }
    compact = value.lower().replace(" ", "")
    match = _MAX_AGE_RE.search(value)
    max_age = int(match.group(1)) if match else 0
    return {
        "max_age": max_age,
        "include_subdomains": "includesubdomains" in compact,
        "preload": "preload" in compact,
        "raw": value,
    }


def _score_from_signals(preloaded: bool, parsed: dict) -> tuple[int, CheckStatus, str]:
    max_age = parsed.get("max_age", 0)
    include_sub = parsed.get("include_subdomains", False)
    header_preload = parsed.get("preload", False)

    if preloaded:
        return 3, CheckStatus.PASSED, "On HSTS preload list"

    if max_age >= _MAX_AGE_STRONG:
        if include_sub and header_preload:
            return 3, CheckStatus.PASSED, "Strong HSTS header (≥1 year, subdomains, preload)"
        if include_sub:
            return 3, CheckStatus.PASSED, "Strong HSTS header (≥1 year, includeSubDomains)"
        return 3, CheckStatus.PASSED, "Strong HSTS header (max-age ≥ 1 year)"

    if max_age >= _MAX_AGE_MINIMUM:
        return 2, CheckStatus.PASSED, "HSTS header present (max-age ≥ 1 day)"

    if max_age > 0:
        return 1, CheckStatus.WARNING, f"Weak HSTS header (max-age={max_age}s)"

    return 0, CheckStatus.FAILED, "No HSTS configured"


async def _check_preload_list(hosts: list[str]) -> tuple[bool, str | None]:
    for host in hosts:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"https://hstspreload.org/api/v2/status?domain={host}"
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("status") == "preloaded":
                        return True, host
        except Exception:
            continue
    return False, None


async def _probe_hsts_header(hosts: list[str]) -> dict:
    """
    Try HTTPS for each host variant. Collect HSTS from final response and redirect chain.
  Returns best result found.
    """
    headers = {"User-Agent": USER_AGENT}
    best: dict | None = None
    errors: list[str] = []

    async with httpx.AsyncClient(
        timeout=12,
        follow_redirects=True,
        headers=headers,
    ) as client:
        for host in hosts:
            for path in ("/", ""):
                url = f"https://{host}{path}"
                try:
                    resp = await client.get(url)
                except httpx.HTTPError as e:
                    errors.append(f"{host}: {type(e).__name__}")
                    continue

                candidates: list[tuple[str, str]] = []
                for hop in (*resp.history, resp):
                    hsts = hop.headers.get("strict-transport-security", "")
                    if hsts:
                        candidates.append((hsts, str(hop.url)))

                if candidates:
                    # Prefer longest max-age if multiple hops set HSTS
                    parsed_best = None
                    raw_best = None
                    url_best = None
                    for raw, hop_url in candidates:
                        parsed = _parse_hsts_header(raw)
                        if parsed_best is None or parsed["max_age"] > parsed_best["max_age"]:
                            parsed_best = parsed
                            raw_best = raw
                            url_best = hop_url
                    result = {
                        "host": host,
                        "final_url": str(resp.url),
                        "header_url": url_best,
                        "hsts_header": raw_best,
                        "parsed": parsed_best,
                        "connected": True,
                    }
                    if best is None or parsed_best["max_age"] > best["parsed"]["max_age"]:
                        best = result
                elif best is None:
                    best = {
                        "host": host,
                        "final_url": str(resp.url),
                        "header_url": None,
                        "hsts_header": None,
                        "parsed": _parse_hsts_header(""),
                        "connected": True,
                    }

    if best:
        return best

    return {
        "host": hosts[0] if hosts else "",
        "final_url": None,
        "header_url": None,
        "hsts_header": None,
        "parsed": _parse_hsts_header(""),
        "connected": False,
        "errors": errors[:3],
    }


class HSTSCheck(BaseCheck):
    name = "hsts"
    display_name = "HSTS Security"
    max_score = 3
    tier = 3

    async def run(self, domain: str, url: str = "", **kwargs) -> CheckResult:
        hosts = _host_candidates(domain)
        preloaded, preload_host = await _check_preload_list(hosts)
        probe = await _probe_hsts_header(hosts)

        parsed = probe.get("parsed") or _parse_hsts_header("")
        has_hsts_header = bool(parsed.get("max_age", 0) > 0 or probe.get("hsts_header"))

        if not probe.get("connected") and not preloaded:
            err = ", ".join(probe.get("errors") or []) or "HTTPS unreachable"
            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=CheckStatus.ERROR,
                score=0,
                max_score=self.max_score,
                summary=f"Could not probe HTTPS for HSTS ({err})",
                details={
                    "hosts_tried": hosts,
                    "preloaded": False,
                    "has_hsts_header": False,
                    "errors": probe.get("errors", []),
                },
            )

        score, status, summary = _score_from_signals(preloaded, parsed)

        return CheckResult(
            name=self.name,
            display_name=self.display_name,
            status=status,
            score=score,
            max_score=self.max_score,
            summary=summary,
            details={
                "preloaded": preloaded,
                "preload_host": preload_host,
                "has_hsts_header": has_hsts_header,
                "hsts_header": probe.get("hsts_header"),
                "max_age": parsed.get("max_age"),
                "include_subdomains": parsed.get("include_subdomains"),
                "header_preload": parsed.get("preload"),
                "probed_host": probe.get("host"),
                "final_url": probe.get("final_url"),
                "header_url": probe.get("header_url"),
                "hosts_tried": hosts,
            },
        )
