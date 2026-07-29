import httpx
import config
from app.models import CheckResult, CheckStatus
from app.checks.base import BaseCheck


class SafeBrowsingCheck(BaseCheck):
    name = "safebrowsing"
    display_name = "Google Safe Browsing"
    max_score = 15
    tier = 1

    async def run(self, domain: str, url: str = None, **kwargs) -> CheckResult:
        if not config.GOOGLE_SAFEBROWSING_API_KEY:
            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=CheckStatus.SKIPPED,
                score=0,
                max_score=self.max_score,
                summary="API key not configured",
                details={
                    "skipped": True,
                    "setup_url": "https://console.cloud.google.com",
                },
            )

        try:
            check_url = url or f"https://{domain}"
            api_url = (
                f"https://safebrowsing.googleapis.com/v4/threatMatches:find"
                f"?key={config.GOOGLE_SAFEBROWSING_API_KEY}"
            )
            payload = {
                "client": {
                    "clientId": "lynx",
                    "clientVersion": "1.0",
                },
                "threatInfo": {
                    "threatTypes": [
                        "MALWARE",
                        "SOCIAL_ENGINEERING",
                        "UNWANTED_SOFTWARE",
                        "POTENTIALLY_HARMFUL_APPLICATION",
                    ],
                    "platformTypes": ["ANY_PLATFORM"],
                    "threatEntryTypes": ["URL"],
                    "threatEntries": [
                        {"url": check_url},
                        {"url": f"https://{domain}"},
                        {"url": f"http://{domain}"},
                    ],
                },
            }

            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(api_url, json=payload)
                data = resp.json()

            matches = data.get("matches", [])

            if not matches:
                return CheckResult(
                    name=self.name,
                    display_name=self.display_name,
                    status=CheckStatus.PASSED,
                    score=15,
                    max_score=self.max_score,
                    summary="Not flagged by Google Safe Browsing",
                    details={"flagged": False},
                )

            threat_types = sorted(set(m.get("threatType", "") for m in matches))
            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=CheckStatus.FAILED,
                score=0,
                max_score=self.max_score,
                summary=f"FLAGGED: {', '.join(threat_types)}",
                details={
                    "flagged": True,
                    "threat_types": threat_types,
                    "matches": len(matches),
                },
            )
        except Exception as e:
            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=CheckStatus.ERROR,
                score=0,
                max_score=self.max_score,
                summary=f"Safe Browsing check failed: {str(e)[:120]}",
                details={},
            )
