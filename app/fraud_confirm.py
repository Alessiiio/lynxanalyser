"""Confirm a domain as fraud and persist for blocklist + analyst feedback."""

from __future__ import annotations

from typing import Any

from app.blocklist import confirm_fraud, is_blocklisted
from app.cache import invalidate_domain_cache
from app.database import save_analyst_feedback
from app.models import CheckResult, CheckStatus, FullReport
from app.scoring import calculate_score


def _extract_llm_answers(checks: list[CheckResult]) -> list[dict] | None:
    for check in checks:
        if check.name != "llm_content":
            continue
        answers = check.details.get("answers")
        if isinstance(answers, list):
            return answers
    return None


async def apply_fraud_confirmation(
    *,
    domain: str,
    url: str,
    fraud_category: str,
    feedback_text: str,
    checks: list[CheckResult] | None = None,
    analyst_id: str = "unknown",
) -> dict[str, Any]:
    """Add domain to blocklist and store analyst feedback."""
    llm_answers = _extract_llm_answers(checks or [])
    entry = confirm_fraud(
        domain,
        url=url,
        fraud_category=fraud_category,
        note=feedback_text,
        analyst_id=analyst_id,
        llm_answers=llm_answers,
    )
    await save_analyst_feedback(
        domain=domain,
        url=url,
        feedback_text=feedback_text,
        action="confirm_fraud",
        original_fraud_category=fraud_category,
        analyst_id=analyst_id,
    )
    invalidate_domain_cache(domain.lower().strip().removeprefix("www."))
    return entry


def build_blocklist_report(
    completed: list[CheckResult],
    domain: str,
    url: str,
    entry: dict[str, Any],
) -> FullReport:
    """Force Critical Risk for internally confirmed fraud domains."""
    category = entry.get("fraud_category", "general_suspicious")
    note = (entry.get("note") or "").strip()
    flag = f"Interne Blocklist: als Betrug bestätigt ({category})"
    if note:
        flag += f" — {note[:120]}"

    report = calculate_score(completed, domain=domain, url=url)
    return report.model_copy(
        update={
            "total_score": min(report.total_score, 10),
            "verdict": "Critical Risk",
            "verdict_color": "red",
            "critical_flags": [flag, *report.critical_flags],
            "blocklist_match": True,
        }
    )


def blocklist_skip_result(check: CheckResult) -> CheckResult:
    return check.model_copy(
        update={
            "status": CheckStatus.SKIPPED,
            "score": 0,
            "summary": "Übersprungen — Domain auf interner Blocklist (bestätigter Betrug)",
            "details": {**check.details, "blocklist_skip": True},
        }
    )
