import asyncio
import re

import httpx

from app.models import CheckResult, CheckStatus
from app.checks.base import BaseCheck

# Country alone is a weak signal — always read alongside other checks, never in isolation.
_FULL_SCORE_COUNTRIES = {"CH", "DE", "AT"}

_EU_WESTERN_EUROPE = {
    "CH", "DE", "AT", "FR", "IT", "ES", "PT", "NL", "BE", "LU", "IE",
    "DK", "SE", "FI", "NO", "IS", "LI", "PL", "CZ", "SK", "HU", "RO",
    "BG", "HR", "SI", "EE", "LV", "LT", "MT", "CY", "GR", "GB", "UK",
}

# Frequently cited in fraud/bulletproof-hosting context — weak signal only, not a blanket verdict.
_HIGH_RISK_COUNTRIES = {
    "RU", "BY", "PA", "BZ", "SC", "MH", "VU", "KP", "IR",
}

_CLOUD_PROVIDER_PATTERNS = [
    r"amazon",
    r"\baws\b",
    r"google\s*llc",
    r"\bgoogle\b",
    r"google\s*cloud",
    r"\bgcp\b",
    r"microsoft\s*azure",
    r"\bazure\b",
    r"hetzner",
    r"\bovh\b",
    r"cloudflare",
    r"digitalocean",
    r"linode",
    r"vultr",
]

_BULLETPROOF_ISP_PATTERNS = [
    r"bulletproof",
    r"offshore",
    r"abuse.?resistant",
    r"privacy.?host",
    r"anonymous.?host",
    r"flokinet",
    r"ccweb",
    r"quadranet",
    r"hostkey",
    r"shinjiru",
    r"alexhost",
    r"private\s*layer",
    r"floki",
]


def _is_known_cloud_provider(isp: str, org: str) -> bool:
    combined = f"{isp} {org}".lower()
    return any(re.search(p, combined) for p in _CLOUD_PROVIDER_PATTERNS)


def _is_bulletproof_isp(isp: str, org: str) -> bool:
    combined = f"{isp} {org}".lower()
    return any(re.search(p, combined) for p in _BULLETPROOF_ISP_PATTERNS)


def _score_hosting(country_code: str, isp: str, org: str) -> tuple[int, CheckStatus, str]:
    is_cloud = _is_known_cloud_provider(isp, org)
    is_bulletproof = _is_bulletproof_isp(isp, org)
    cc = country_code.upper()

    if is_cloud or cc in _FULL_SCORE_COUNTRIES:
        label = "known cloud provider" if is_cloud else f"hosted in {cc}"
        return 4, CheckStatus.PASSED, f"Reputable hosting ({label})"

    if is_bulletproof or cc in _HIGH_RISK_COUNTRIES:
        reason = "bulletproof/offshore provider" if is_bulletproof else f"high-risk jurisdiction ({cc})"
        return 1, CheckStatus.WARNING, f"Concerning hosting signal — {reason}"

    if cc in _EU_WESTERN_EUROPE:
        return 2, CheckStatus.PASSED, f"EU/Western Europe hosting ({cc})"

    return 2, CheckStatus.PASSED, f"Hosting in {cc or 'unknown country'}"


class HostingCheck(BaseCheck):
    name = "hosting"
    display_name = "Hosting Location"
    max_score = 4
    tier = 3

    async def run(self, domain: str, **kwargs) -> CheckResult:
        loop = asyncio.get_event_loop()

        def _resolve_a_record() -> str:
            import dns.resolver
            import dns.exception

            answers = dns.resolver.resolve(domain, "A", lifetime=8)
            for rdata in answers:
                return str(rdata)
            raise ValueError("No A record found")

        try:
            ip = await asyncio.wait_for(
                loop.run_in_executor(None, _resolve_a_record),
                timeout=10,
            )
        except asyncio.TimeoutError:
            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=CheckStatus.ERROR,
                score=0,
                max_score=self.max_score,
                summary="DNS lookup timed out",
                details={},
            )
        except Exception as e:
            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=CheckStatus.ERROR,
                score=0,
                max_score=self.max_score,
                summary=f"DNS resolution failed: {str(e)[:120]}",
                details={},
            )

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"http://ip-api.com/json/{ip}",
                    params={"fields": "status,message,country,countryCode,city,isp,org,as"},
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=CheckStatus.ERROR,
                score=0,
                max_score=self.max_score,
                summary=f"Geolocation lookup failed: {str(e)[:120]}",
                details={"ip": ip},
            )

        if data.get("status") != "success":
            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=CheckStatus.ERROR,
                score=0,
                max_score=self.max_score,
                summary=f"Geolocation API error: {data.get('message', 'unknown')[:120]}",
                details={"ip": ip},
            )

        country = data.get("country", "")
        country_code = data.get("countryCode", "")
        city = data.get("city", "")
        isp = data.get("isp", "") or ""
        org = data.get("org", "") or data.get("as", "") or ""
        is_cloud = _is_known_cloud_provider(isp, org)

        score, status, summary = _score_hosting(country_code, isp, org)
        location = ", ".join(p for p in [city, country] if p) or country_code or "unknown"

        return CheckResult(
            name=self.name,
            display_name=self.display_name,
            status=status,
            score=score,
            max_score=self.max_score,
            summary=f"{summary} — {location} ({isp or org or 'unknown ISP'})",
            details={
                "ip": ip,
                "country": country,
                "country_code": country_code,
                "city": city,
                "isp": isp or org,
                "is_known_cloud_provider": is_cloud,
            },
        )
