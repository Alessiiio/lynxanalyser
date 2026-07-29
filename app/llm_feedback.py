"""Apply human feedback overrides to AI fraud analysis results."""

from __future__ import annotations

from datetime import datetime, timezone

from app.database import save_analyst_feedback
from app.models import CheckResult, CheckStatus, FullReport
from app.scoring import calculate_score

_DISMISSIBLE_CATEGORIES = {
    "investment_fraud",
    "precious_metals_fraud",
    "loan_fraud",
    "phishing_impersonation",
    "fake_shop",
    "romance_scam_support",
    "pyramid_mlm",
    "general_suspicious",
}


async def apply_llm_user_feedback(
    checks: list[CheckResult],
    url: str,
    domain: str,
    feedback_text: str,
    analyst_id: str = "unknown",
) -> FullReport:
    """Mark AI fraud classification as false positive and recalculate the report."""
    updated_checks: list[CheckResult] = []
    feedback_text = feedback_text.strip()
    original_category: str | None = None

    for check in checks:
        if check.name != "llm_content":
            updated_checks.append(check)
            continue

        details = dict(check.details)
        if details.get("user_dismissed"):
            updated_checks.append(check)
            continue

        original_category = details.get("fraud_category", "unclear")
        original_score = check.score

        details["original_fraud_category"] = original_category
        details["original_score"] = original_score
        details["fraud_category"] = "none_detected"
        details["user_dismissed"] = True
        details["user_feedback"] = feedback_text
        details["user_dismissed_at"] = datetime.now(timezone.utc).isoformat()

        summary = "Vom Benutzer als Fehlalarm markiert"
        if original_category in _DISMISSIBLE_CATEGORIES:
            summary += f" — Kategorie «{original_category}» zurückgewiesen"
        if feedback_text:
            summary += f" — {feedback_text[:120]}"

        updated_checks.append(
            check.model_copy(
                update={
                    "status": CheckStatus.PASSED,
                    "score": 16,
                    "summary": summary,
                    "details": details,
                }
            )
        )

    await save_analyst_feedback(
        domain=domain,
        url=url,
        feedback_text=feedback_text,
        action="dismiss_category",
        original_fraud_category=original_category,
        analyst_id=analyst_id,
    )

    return calculate_score(updated_checks, domain, url)
