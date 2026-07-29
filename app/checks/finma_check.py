from app.models import CheckResult, CheckStatus
from app.checks.base import BaseCheck
from app.finma_warnings import find_finma_warning

_FINMA_WARNLIST = "https://www.finma.ch/de/finma-public/warnungen/warnliste/"


class FINMACheck(BaseCheck):
    name = "finma"
    display_name = "FINMA-Warnliste"
    max_score = 10
    tier = 1

    async def run(self, domain: str, company_name: str = "", **kwargs) -> CheckResult:
        try:
            match = await find_finma_warning(domain, company_name or None)
        except Exception as e:
            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=CheckStatus.ERROR,
                score=0,
                max_score=self.max_score,
                summary=f"FINMA-Warnliste: Abfrage fehlgeschlagen — {str(e)[:100]}",
                details={"source": "finma_ch", "warnlist_url": _FINMA_WARNLIST},
            )

        if match:
            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=CheckStatus.FAILED,
                score=0,
                max_score=self.max_score,
                summary=f"Auf FINMA-Warnliste: {match['name']}",
                details={
                    "listed": True,
                    "source": "finma_ch",
                    "match_type": match["match_type"],
                    "warning_name": match["name"],
                    "warning_url": match["url"],
                    "warnlist_url": _FINMA_WARNLIST,
                },
            )

        return CheckResult(
            name=self.name,
            display_name=self.display_name,
            status=CheckStatus.PASSED,
            score=self.max_score,
            max_score=self.max_score,
            summary="Nicht auf der FINMA-Warnliste",
            details={
                "listed": False,
                "source": "finma_ch",
                "warnlist_url": _FINMA_WARNLIST,
            },
        )
