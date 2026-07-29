import config
from app.models import CheckResult, CheckStatus
from app.checks.base import BaseCheck
from app.iscan_warnings import find_iscan_warning, _ISCAN_PORTAL


class ISCANCheck(BaseCheck):
    name = "iscan"
    display_name = "I-SCAN (IOSCO)"
    max_score = 10
    tier = 1

    async def run(self, domain: str, company_name: str = "", **kwargs) -> CheckResult:
        if not config.IOSCO_ISCAN_API_KEY:
            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=CheckStatus.SKIPPED,
                score=0,
                max_score=self.max_score,
                summary="I-SCAN API key missing — set IOSCO_ISCAN_API_KEY in .env",
                details={
                    "skipped": True,
                    "source": "iosco_i_scan",
                    "portal_url": _ISCAN_PORTAL,
                    "note": "Request a free API key at api@iosco.org (see https://api.iosco.org/v1/i-scan/)",
                },
            )

        try:
            match = await find_iscan_warning(domain, company_name or None)
        except Exception as e:
            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=CheckStatus.ERROR,
                score=0,
                max_score=self.max_score,
                summary=f"I-SCAN lookup failed: {str(e)[:120]}",
                details={"source": "iosco_i_scan", "portal_url": _ISCAN_PORTAL},
            )

        if match:
            regulator = match.get("regulator") or "IOSCO member regulator"
            jurisdiction = match.get("jurisdiction")
            regulator_part = regulator
            if jurisdiction:
                regulator_part = f"{regulator} ({jurisdiction})"
            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=CheckStatus.FAILED,
                score=0,
                max_score=self.max_score,
                summary=f"Auf I-SCAN-Warnliste: {match['name']} — {regulator_part}",
                details={
                    "listed": True,
                    "source": "iosco_i_scan",
                    "match_type": match["match_type"],
                    "warning_name": match["name"],
                    "warning_url": match.get("url"),
                    "regulator": match.get("regulator"),
                    "jurisdiction": match.get("jurisdiction"),
                    "warning_id": match.get("warning_id"),
                    "iscan_portal": match.get("iscan_portal", _ISCAN_PORTAL),
                },
            )

        return CheckResult(
            name=self.name,
            display_name=self.display_name,
            status=CheckStatus.PASSED,
            score=self.max_score,
            max_score=self.max_score,
            summary="Nicht in I-SCAN-Warnliste (international)",
            details={
                "listed": False,
                "source": "iosco_i_scan",
                "iscan_portal": _ISCAN_PORTAL,
            },
        )
