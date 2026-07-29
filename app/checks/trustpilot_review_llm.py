"""Mini-LLM analysis of negative Trustpilot review texts."""

from __future__ import annotations

import json
from typing import Any, Optional

import config
from app.checks.llm_content_check import (
    OllamaConnectionError,
    OllamaJsonParseError,
    _call_llm_backend,
    _parse_json_response,
)

_PROMPT = """You are a Swiss fraud analyst reviewing negative Trustpilot reviews for legitimacy signals.

Company: {business_name}

Analyze ONLY the review texts below. Do not invent facts. Distinguish:
- "high": credible fraud/scam allegations (money lost, deception, impersonation, no payout)
- "low": legitimate customer complaints (slow support, fees, product dislike) without fraud pattern
- "none": no meaningful negative content or too vague

Reviews (JSON):
{reviews_json}

Rules:
- fraud_signal=true only when the review credibly alleges scam, fraud, theft, or deliberate deception
- evidence_quote must be an exact substring from the review text (min 10 chars) when fraud_signal=true
- overall_severity = highest severity across reviews

Respond ONLY with JSON:
{{
  "reviews": [
    {{"index": 1, "severity": "none|low|high", "fraud_signal": false, "summary": "<max 12 words DE>", "evidence_quote": ""}}
  ],
  "overall_severity": "none|low|high",
  "summary": "<one German sentence for analysts>"
}}

JSON only."""


def _normalize_analysis(raw: dict, review_count: int) -> dict[str, Any]:
    reviews_out: list[dict] = []
    for entry in raw.get("reviews") or []:
        if not isinstance(entry, dict):
            continue
        severity = str(entry.get("severity", "none")).lower()
        if severity not in ("none", "low", "high"):
            severity = "none"
        reviews_out.append({
            "index": entry.get("index"),
            "severity": severity,
            "fraud_signal": bool(entry.get("fraud_signal")),
            "summary": str(entry.get("summary", ""))[:120],
            "evidence_quote": str(entry.get("evidence_quote", ""))[:200],
        })

    overall = str(raw.get("overall_severity", "none")).lower()
    if overall not in ("none", "low", "high"):
        overall = "none"
    if reviews_out:
        rank = {"none": 0, "low": 1, "high": 2}
        max_from_reviews = max(rank.get(r["severity"], 0) for r in reviews_out)
        if rank.get(overall, 0) < max_from_reviews:
            overall = {0: "none", 1: "low", 2: "high"}[max_from_reviews]

    return {
        "reviews": reviews_out,
        "overall_severity": overall,
        "summary": str(raw.get("summary", ""))[:300],
        "review_count": review_count,
        "fraud_signal_count": sum(1 for r in reviews_out if r.get("fraud_signal")),
        "skipped": False,
    }


async def analyze_negative_reviews(
    reviews: list[dict[str, Any]],
    *,
    business_name: str,
) -> Optional[dict[str, Any]]:
    """Return structured LLM assessment or None when no reviews to analyze."""
    if not reviews:
        return None

    use_claude = bool(config.ANTHROPIC_API_KEY)
    use_ollama = bool(config.OLLAMA_BASE_URL)
    if not use_claude and not use_ollama:
        return {
            "skipped": True,
            "reason": "no_llm_configured",
            "review_count": len(reviews),
        }

    prompt = _PROMPT.format(
        business_name=business_name or "unbekannt",
        reviews_json=json.dumps(reviews, ensure_ascii=False, indent=2),
    )

    try:
        raw = await _call_llm_backend(prompt, use_claude)
        if not isinstance(raw, dict):
            raise ValueError("invalid LLM response")
        return _normalize_analysis(raw, len(reviews))
    except (OllamaConnectionError, OllamaJsonParseError, ValueError, TypeError) as exc:
        return {
            "skipped": True,
            "reason": str(exc)[:160],
            "review_count": len(reviews),
        }
