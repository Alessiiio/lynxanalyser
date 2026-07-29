import asyncio
from datetime import datetime, timezone

from app.models import CheckResult, CheckStatus
from app.checks.base import BaseCheck

_PRIVACY_KEYWORDS = (
    "privacy", "whoisguard", "domains by proxy", "redacted", "gdpr",
    "withheld", "not disclosed", "data protected", "contact privacy",
)

_HIGH_RISK_REGISTRAR_KEYWORDS = (
    "namecheap", "godaddy", "hostinger", "namesilo", "porkbun",
    "gandi", "reg.ru", "nicenic",
)


class WhoisCheck(BaseCheck):
    name = "whois"
    display_name = "Domain Age"
    max_score = 8
    tier = 2

    async def run(self, domain: str, **kwargs) -> CheckResult:
        try:
            import whois
            loop = asyncio.get_event_loop()
            w = await asyncio.wait_for(
                loop.run_in_executor(None, whois.whois, domain),
                timeout=15,
            )

            creation_date = w.creation_date
            if isinstance(creation_date, list):
                creation_date = creation_date[0]

            registrar = str(w.registrar) if w.registrar else "Unknown"
            registrar_lower = registrar.lower()
            registrant = " ".join(
                str(x) for x in (
                    getattr(w, "name", None),
                    getattr(w, "org", None),
                    getattr(w, "registrant", None),
                ) if x
            ).lower()

            privacy_detected = any(
                kw in registrar_lower or kw in registrant
                for kw in _PRIVACY_KEYWORDS
            )
            high_risk_registrar = any(kw in registrar_lower for kw in _HIGH_RISK_REGISTRAR_KEYWORDS)

            if creation_date is None or privacy_detected:
                summary = "Registration date hidden (private registration)"
                if high_risk_registrar:
                    summary += f" — registrar flagged: {registrar}"
                return CheckResult(
                    name=self.name,
                    display_name=self.display_name,
                    status=CheckStatus.WARNING,
                    score=0,
                    max_score=self.max_score,
                    summary=summary,
                    details={
                        "registrar": registrar,
                        "private": True,
                        "privacy_service": privacy_detected,
                        "high_risk_registrar": high_risk_registrar,
                    },
                )

            if creation_date.tzinfo is None:
                creation_date = creation_date.replace(tzinfo=timezone.utc)

            now = datetime.now(timezone.utc)
            age_days = (now - creation_date).days
            age_years = age_days / 365.25

            if age_days >= 5 * 365:
                score = 8
            elif age_days >= 2 * 365:
                score = 6
            elif age_days >= 365:
                score = 4
            elif age_days >= 180:
                score = 2
            else:
                score = 0

            if high_risk_registrar and score > 0:
                score = max(0, score - 2)

            if score >= 4:
                status = CheckStatus.PASSED
            elif score >= 2:
                status = CheckStatus.WARNING
            else:
                status = CheckStatus.FAILED

            expiry = w.expiration_date
            if isinstance(expiry, list):
                expiry = expiry[0]

            summary = f"{age_years:.1f} years old"
            if high_risk_registrar:
                summary += f" — high-risk registrar: {registrar}"

            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=status,
                score=score,
                max_score=self.max_score,
                summary=summary,
                details={
                    "age_days": age_days,
                    "creation_date": creation_date.isoformat(),
                    "expiration_date": expiry.isoformat() if expiry else None,
                    "registrar": registrar,
                    "private": False,
                    "privacy_service": False,
                    "high_risk_registrar": high_risk_registrar,
                },
            )
        except asyncio.TimeoutError:
            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=CheckStatus.ERROR,
                score=0,
                max_score=self.max_score,
                summary="WHOIS lookup timed out",
                details={},
            )
        except Exception as e:
            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=CheckStatus.ERROR,
                score=0,
                max_score=self.max_score,
                summary=f"WHOIS lookup failed: {str(e)[:120]}",
                details={},
            )
