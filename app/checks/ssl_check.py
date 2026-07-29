import asyncio
import ssl
import socket
from datetime import datetime, timezone
from app.models import CheckResult, CheckStatus
from app.checks.base import BaseCheck

_SOCKET_TIMEOUT = 7
_CONNECT_TIMEOUT = 10


class SSLCheck(BaseCheck):
    name = "ssl"
    display_name = "SSL Certificate"
    max_score = 4
    tier = 3

    async def run(self, domain: str, **kwargs) -> CheckResult:
        loop = asyncio.get_event_loop()

        def _get_cert(verify: bool):
            ctx = ssl.create_default_context()
            if not verify:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            with socket.create_connection((domain, 443), timeout=_SOCKET_TIMEOUT) as sock:
                with ctx.wrap_socket(sock, server_hostname=domain) as ssock:
                    return ssock.getpeercert(binary_form=True), verify

        cert_der = None
        cert_valid = True

        try:
            cert_der, _ = await asyncio.wait_for(
                loop.run_in_executor(None, _get_cert, True),
                timeout=_CONNECT_TIMEOUT,
            )
        except ssl.SSLError:
            # Only retry without validation when the cert chain is untrusted — not on timeouts.
            cert_valid = False
            try:
                cert_der, _ = await asyncio.wait_for(
                    loop.run_in_executor(None, _get_cert, False),
                    timeout=_CONNECT_TIMEOUT,
                )
            except Exception:
                return CheckResult(
                    name=self.name,
                    display_name=self.display_name,
                    status=CheckStatus.ERROR,
                    score=0,
                    max_score=self.max_score,
                    summary="Could not retrieve certificate (validation and fallback failed)",
                    details={},
                )
        except (socket.timeout, TimeoutError, OSError) as e:
            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=CheckStatus.ERROR,
                score=0,
                max_score=self.max_score,
                summary=f"Could not connect: {str(e)[:120]}",
                details={},
            )
        except Exception as e:
            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=CheckStatus.ERROR,
                score=0,
                max_score=self.max_score,
                summary=f"Could not connect: {str(e)[:120]}",
                details={},
            )

        if not cert_der:
            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=CheckStatus.FAILED,
                score=0,
                max_score=self.max_score,
                summary="No SSL certificate found",
                details={"valid": False},
            )

        try:
            from cryptography import x509

            cert = x509.load_der_x509_certificate(cert_der)

            try:
                expires = cert.not_valid_after_utc
            except AttributeError:
                expires = cert.not_valid_after.replace(tzinfo=timezone.utc)

            now = datetime.now(timezone.utc)
            days_until_expiry = (expires - now).days

            org = None
            try:
                org = cert.subject.get_attributes_for_oid(
                    x509.oid.NameOID.ORGANIZATION_NAME
                )[0].value
            except (IndexError, Exception):
                pass

            issuer_cn = None
            try:
                issuer_cn = cert.issuer.get_attributes_for_oid(
                    x509.oid.NameOID.COMMON_NAME
                )[0].value
            except (IndexError, Exception):
                pass

            if not cert_valid:
                score = 0
                status = CheckStatus.FAILED
                summary = "Certificate validation failed (untrusted/self-signed)"
            elif days_until_expiry <= 0:
                score = 0
                status = CheckStatus.FAILED
                summary = f"Certificate expired {abs(days_until_expiry)} days ago"
            elif days_until_expiry <= 30:
                score = 2
                status = CheckStatus.WARNING
                summary = f"Valid, expiring soon ({days_until_expiry} days)"
            else:
                score = 4
                status = CheckStatus.PASSED
                summary = f"Valid, expires in {days_until_expiry} days"

            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=status,
                score=score,
                max_score=self.max_score,
                summary=summary,
                details={
                    "valid": cert_valid and days_until_expiry > 0,
                    "days_until_expiry": days_until_expiry,
                    "expires": expires.isoformat(),
                    "issuer": issuer_cn,
                    "organization": org,
                },
            )
        except Exception as e:
            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=CheckStatus.ERROR,
                score=0,
                max_score=self.max_score,
                summary=f"Certificate parse error: {str(e)[:120]}",
                details={},
            )
