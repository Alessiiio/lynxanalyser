import asyncio
import json
import urllib.request
from datetime import datetime, timezone
from app.models import CheckResult, CheckStatus
from app.checks.base import BaseCheck

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; lynx/1.0)",
    "Accept": "application/json",
}


class CRTCheck(BaseCheck):
    name = "crt"
    display_name = "Certificate History"
    max_score = 1
    tier = 3

    async def run(self, domain: str, **kwargs) -> CheckResult:
        try:
            def _fetch():
                url = f"https://crt.sh/?q={domain}&output=json"
                req = urllib.request.Request(url, headers=_HEADERS)
                with urllib.request.urlopen(req, timeout=12) as r:
                    return json.loads(r.read().decode())

            data = await asyncio.wait_for(asyncio.to_thread(_fetch), timeout=14)

            if not data:
                return CheckResult(
                    name=self.name,
                    display_name=self.display_name,
                    status=CheckStatus.WARNING,
                    score=1,
                    max_score=self.max_score,
                    summary="No certificate history found",
                    details={"cert_count": 0},
                )

            dates = []
            issuers: set[str] = set()

            for cert in data:
                try:
                    raw = cert.get("not_before", "")
                    if raw:
                        d = datetime.fromisoformat(raw)
                        if d.tzinfo is None:
                            d = d.replace(tzinfo=timezone.utc)
                        dates.append(d)
                    issuer = cert.get("issuer_name", "")
                    for part in issuer.split(","):
                        part = part.strip()
                        if part.startswith("CN="):
                            issuers.add(part[3:])
                except Exception:
                    pass

            total = len(data)

            if not dates:
                return CheckResult(
                    name=self.name,
                    display_name=self.display_name,
                    status=CheckStatus.WARNING,
                    score=1,
                    max_score=self.max_score,
                    summary=f"{total} certificates found",
                    details={"cert_count": total},
                )

            first_cert = min(dates)
            now = datetime.now(timezone.utc)
            age_days = (now - first_cert).days
            age_years = age_days / 365.25

            if age_days >= 2 * 365:
                score = 1
            elif age_days >= 365:
                score = 1
            elif age_days >= 180:
                score = 1
            else:
                score = 1

            status = CheckStatus.PASSED if score >= 1 else CheckStatus.WARNING

            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=status,
                score=score,
                max_score=self.max_score,
                summary=f"First cert {age_years:.1f} years ago, {total} total",
                details={
                    "first_cert_date": first_cert.isoformat(),
                    "cert_count": total,
                    "issuers": sorted(issuers)[:5],
                    "age_days": age_days,
                },
            )
        except Exception as e:
            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=CheckStatus.ERROR,
                score=0,
                max_score=self.max_score,
                summary=f"Certificate history check failed: {str(e)[:120]}",
                details={},
            )
