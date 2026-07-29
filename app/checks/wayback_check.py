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


class WaybackCheck(BaseCheck):
    name = "wayback"
    display_name = "Web Archive"
    max_score = 3
    tier = 3

    async def run(self, domain: str, **kwargs) -> CheckResult:
        try:
            def _fetch():
                url = (
                    f"https://web.archive.org/cdx/search/cdx"
                    f"?url={domain}&output=json&fl=timestamp&limit=1&matchType=domain"
                )
                req = urllib.request.Request(url, headers=_HEADERS)
                with urllib.request.urlopen(req, timeout=None) as r:
                    return json.loads(r.read().decode())

            data = await asyncio.wait_for(asyncio.to_thread(_fetch), timeout=35)

            if not data or len(data) < 2:
                return CheckResult(
                    name=self.name,
                    display_name=self.display_name,
                    status=CheckStatus.WARNING,
                    score=0,
                    max_score=self.max_score,
                    summary="Not found in Wayback Machine",
                    details={"archived": False},
                )

            timestamp = data[1][0]
            first_seen = datetime.strptime(timestamp, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            age_days = (now - first_seen).days
            age_years = age_days / 365.25

            if age_days >= 3 * 365:
                score = 3
            elif age_days >= 365:
                score = 2
            elif age_days >= 180:
                score = 1
            elif age_days >= 30:
                score = 1
            else:
                score = 0

            status = CheckStatus.PASSED if score >= 2 else (
                CheckStatus.WARNING if score >= 1 else CheckStatus.FAILED
            )

            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=status,
                score=score,
                max_score=self.max_score,
                summary=f"First archived {age_years:.1f} years ago",
                details={
                    "first_seen": first_seen.isoformat(),
                    "age_days": age_days,
                    "archived": True,
                    "wayback_url": f"https://web.archive.org/web/*/{domain}",
                },
            )
        except Exception as e:
            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=CheckStatus.ERROR,
                score=0,
                max_score=self.max_score,
                summary=f"Archive check failed: {str(e)[:120]}",
                details={},
            )
