import asyncio
import time

import httpx
import config
from app.models import CheckResult, CheckStatus
from app.checks.base import BaseCheck

_POLL_INTERVAL = 3.0
_POLL_BUDGET_SECONDS = 22.0


class URLScanCheck(BaseCheck):
    name = "urlscan"
    display_name = "URLScan.io"
    max_score = 1
    tier = 3

    async def run(self, domain: str, url: str = None, **kwargs) -> CheckResult:
        if not config.URLSCAN_API_KEY:
            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=CheckStatus.SKIPPED,
                score=0,
                max_score=self.max_score,
                summary="API key not configured",
                details={
                    "skipped": True,
                    "setup_url": "https://urlscan.io/user/signup",
                    "note": "URLScan.io now requires an API key for scan submission. Sign up free at urlscan.io",
                },
            )

        scan_url = url or f"https://{domain}"
        headers = {"Content-Type": "application/json", "API-Key": config.URLSCAN_API_KEY}
        submit_payload = {"url": scan_url, "visibility": "public"}

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                try:
                    resp = await client.post(
                        "https://urlscan.io/api/v1/scan/",
                        json=submit_payload,
                        headers=headers,
                    )
                except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as e:
                    return CheckResult(
                        name=self.name,
                        display_name=self.display_name,
                        status=CheckStatus.ERROR,
                        score=0,
                        max_score=self.max_score,
                        summary=f"URLScan submit failed: {str(e)[:100]}",
                        details={},
                    )

                if resp.status_code == 429:
                    return CheckResult(
                        name=self.name,
                        display_name=self.display_name,
                        status=CheckStatus.ERROR,
                        score=0,
                        max_score=self.max_score,
                        summary="Rate limited — try again in a minute",
                        details={},
                    )

                if resp.status_code not in (200, 201):
                    return CheckResult(
                        name=self.name,
                        display_name=self.display_name,
                        status=CheckStatus.ERROR,
                        score=0,
                        max_score=self.max_score,
                        summary=f"Scan submission failed (HTTP {resp.status_code})",
                        details={},
                    )

                uuid = resp.json().get("uuid")
                if not uuid:
                    return CheckResult(
                        name=self.name,
                        display_name=self.display_name,
                        status=CheckStatus.ERROR,
                        score=0,
                        max_score=self.max_score,
                        summary="URLScan submit failed: no UUID in response",
                        details={},
                    )

                result_url = f"https://urlscan.io/api/v1/result/{uuid}/"
                result_data = None
                deadline = time.monotonic() + _POLL_BUDGET_SECONDS

                while time.monotonic() < deadline:
                    await asyncio.sleep(_POLL_INTERVAL)
                    try:
                        poll = await client.get(result_url)
                    except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError):
                        continue
                    if poll.status_code == 200:
                        result_data = poll.json()
                        break

            if not result_data:
                return CheckResult(
                    name=self.name,
                    display_name=self.display_name,
                    status=CheckStatus.ERROR,
                    score=0,
                    max_score=self.max_score,
                    summary="URLScan timeout after 30s — result may be available later",
                    details={"scan_uuid": uuid},
                )

            verdict = result_data.get("verdicts", {}).get("overall", {})
            is_malicious = verdict.get("malicious", False)
            verdict_score = verdict.get("score", 0)
            screenshot = result_data.get("task", {}).get("screenshotURL", "")

            tech_list = [
                t.get("app", "")
                for t in result_data.get("meta", {})
                .get("processors", {})
                .get("wappa", {})
                .get("data", [])
                if t.get("app")
            ][:8]

            score = 0 if is_malicious else 1
            status = CheckStatus.FAILED if is_malicious else CheckStatus.PASSED
            summary = (
                f"Flagged as malicious (score: {verdict_score})"
                if is_malicious
                else f"Clean (verdict score: {verdict_score})"
            )

            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=status,
                score=score,
                max_score=self.max_score,
                summary=summary,
                details={
                    "malicious": is_malicious,
                    "verdict_score": verdict_score,
                    "screenshot_url": screenshot,
                    "technologies": tech_list,
                    "result_url": f"https://urlscan.io/result/{uuid}/",
                },
            )
        except Exception as e:
            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=CheckStatus.ERROR,
                score=0,
                max_score=self.max_score,
                summary=f"URLScan check failed: {str(e)[:120]}",
                details={},
            )
