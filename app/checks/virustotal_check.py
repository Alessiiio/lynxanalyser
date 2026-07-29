import httpx
import config
from app.models import CheckResult, CheckStatus
from app.checks.base import BaseCheck

_MAX_SCORE = 10
_SCORE_UNKNOWN = 5  # domain not in VT DB


def _compute_vt_score(
    malicious: int,
    suspicious: int,
    harmless: int,
    undetected: int,
) -> tuple[int, CheckStatus, str]:
    """Proportional score — a single flag among many engines is a soft signal, not −50%."""
    total = malicious + suspicious + harmless + undetected
    if total == 0:
        return _SCORE_UNKNOWN, CheckStatus.WARNING, "No engine data available"

    ratio = malicious / total
    high_confidence_threat = malicious > 5 and (ratio >= 0.08 or malicious >= 10)
    if high_confidence_threat:
        return 0, CheckStatus.FAILED, f"{malicious}/{total} engines flagged — HIGH RISK"

    if malicious == 0 and suspicious == 0:
        return _MAX_SCORE, CheckStatus.PASSED, f"Clean — 0/{total} engines flagged"

    clean_count = total - malicious - suspicious
    ratio = clean_count / total
    score = round(_MAX_SCORE * ratio)

    # Few flags on a large scan: floor so 1/91 does not tank the check
    if malicious <= 2 and total >= 30:
        score = max(score, _MAX_SCORE - malicious)
    elif malicious == 1 and total >= 10:
        score = max(score, 8)

    # Suspicious engines weigh less than confirmed malicious
    if suspicious > 0 and malicious == 0:
        score = max(7, score - min(2, suspicious))

    score = max(0, min(_MAX_SCORE, score))

    if malicious >= 3:
        status = CheckStatus.WARNING
    elif malicious >= 1 or suspicious >= 3:
        status = CheckStatus.WARNING
    else:
        status = CheckStatus.PASSED

    summary = f"{malicious}/{total} engines flagged"
    if suspicious:
        summary += f", {suspicious} suspicious"

    return score, status, summary


class VirusTotalCheck(BaseCheck):
    name = "virustotal"
    display_name = "VirusTotal"
    max_score = _MAX_SCORE
    tier = 1

    async def run(self, domain: str, **kwargs) -> CheckResult:
        if not config.VIRUSTOTAL_API_KEY:
            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=CheckStatus.SKIPPED,
                score=0,
                max_score=self.max_score,
                summary="API key not configured",
                details={
                    "skipped": True,
                    "setup_url": "https://www.virustotal.com/gui/sign-in",
                },
            )

        try:
            url = f"https://www.virustotal.com/api/v3/domains/{domain}"
            headers = {"x-apikey": config.VIRUSTOTAL_API_KEY}

            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url, headers=headers)

            if resp.status_code == 404:
                return CheckResult(
                    name=self.name,
                    display_name=self.display_name,
                    status=CheckStatus.WARNING,
                    score=_SCORE_UNKNOWN,
                    max_score=self.max_score,
                    summary="Domain not yet in VirusTotal database",
                    details={"not_found": True},
                )

            if resp.status_code == 429:
                return CheckResult(
                    name=self.name,
                    display_name=self.display_name,
                    status=CheckStatus.ERROR,
                    score=0,
                    max_score=self.max_score,
                    summary="Rate limit reached (4 req/min on free tier)",
                    details={},
                )

            data = resp.json()
            stats = (
                data.get("data", {})
                .get("attributes", {})
                .get("last_analysis_stats", {})
            )

            malicious = stats.get("malicious", 0)
            suspicious = stats.get("suspicious", 0)
            harmless = stats.get("harmless", 0)
            undetected = stats.get("undetected", 0)
            total = malicious + suspicious + harmless + undetected

            score, status, summary = _compute_vt_score(
                malicious, suspicious, harmless, undetected
            )

            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=status,
                score=score,
                max_score=self.max_score,
                summary=summary,
                details={
                    "malicious": malicious,
                    "suspicious": suspicious,
                    "harmless": harmless,
                    "undetected": undetected,
                    "total_engines": total,
                    "clean_ratio_percent": round(
                        (total - malicious - suspicious) / total * 100, 1
                    ) if total else 0,
                },
            )
        except Exception as e:
            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=CheckStatus.ERROR,
                score=0,
                max_score=self.max_score,
                summary=f"VirusTotal check failed: {str(e)[:120]}",
                details={},
            )
