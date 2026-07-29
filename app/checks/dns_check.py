import asyncio
from app.models import CheckResult, CheckStatus
from app.checks.base import BaseCheck


class DNSCheck(BaseCheck):
    name = "dns"
    display_name = "DNS Records"
    max_score = 4
    tier = 3

    async def run(self, domain: str, **kwargs) -> CheckResult:
        loop = asyncio.get_event_loop()

        def _check():
            import dns.resolver
            import dns.exception

            results = {
                "has_mx": False,
                "has_spf": False,
                "has_dmarc": False,
                "mx_records": [],
                "spf_value": None,
                "dmarc_value": None,
            }

            try:
                mx = dns.resolver.resolve(domain, "MX", lifetime=8)
                results["has_mx"] = len(list(mx)) > 0
                results["mx_records"] = [str(r.exchange).rstrip(".") for r in mx][:3]
            except Exception:
                pass

            try:
                txt = dns.resolver.resolve(domain, "TXT", lifetime=8)
                for record in txt:
                    val = "".join(s.decode() if isinstance(s, bytes) else s for s in record.strings)
                    if "v=spf1" in val:
                        results["has_spf"] = True
                        results["spf_value"] = val
                        break
            except Exception:
                pass

            try:
                dmarc = dns.resolver.resolve(f"_dmarc.{domain}", "TXT", lifetime=8)
                for record in dmarc:
                    val = "".join(s.decode() if isinstance(s, bytes) else s for s in record.strings)
                    if "v=DMARC1" in val:
                        results["has_dmarc"] = True
                        results["dmarc_value"] = val
                        break
            except Exception:
                pass

            return results

        try:
            results = await asyncio.wait_for(
                loop.run_in_executor(None, _check),
                timeout=20,
            )

            score = 0
            if results["has_mx"]:
                score += 1
            if results["has_spf"]:
                score += 1
            if results["has_dmarc"]:
                score += 2

            present = [n for n, k in [("MX", "has_mx"), ("SPF", "has_spf"), ("DMARC", "has_dmarc")] if results[k]]
            missing = [n for n, k in [("MX", "has_mx"), ("SPF", "has_spf"), ("DMARC", "has_dmarc")] if not results[k]]

            if score >= 3:
                status = CheckStatus.PASSED
            elif score >= 1:
                status = CheckStatus.WARNING
            else:
                status = CheckStatus.FAILED

            parts = []
            if present:
                parts.append("+".join(present) + " present")
            if missing:
                parts.append("missing " + ", ".join(missing))
            summary = "; ".join(parts) or "No DNS email records"

            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=status,
                score=score,
                max_score=self.max_score,
                summary=summary,
                details={
                    **results,
                    "score_breakdown": [
                        {"label": "MX records", "points": 1 if results["has_mx"] else 0, "max_points": 1},
                        {"label": "SPF record", "points": 1 if results["has_spf"] else 0, "max_points": 1},
                        {"label": "DMARC record", "points": 2 if results["has_dmarc"] else 0, "max_points": 2},
                    ],
                },
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
                summary=f"DNS check failed: {str(e)[:120]}",
                details={},
            )
