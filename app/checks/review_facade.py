"""Shared review-pattern analysis for reputation checks (Trustpilot, Google, …)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import mean
from typing import Any


@dataclass
class NormalizedReview:
    rating: int | None
    author: str | None = None
    author_review_count: int | None = None
    verified: bool | None = None
    published: datetime | None = None
    text: str | None = None


def parse_iso_datetime(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def analyze_review_patterns(reviews: list[NormalizedReview]) -> dict[str, Any]:
    if not reviews:
        return {
            "sample_size": 0,
            "single_review_account_count": 0,
            "single_review_account_pct": 0.0,
            "verified_count": 0,
            "verified_pct": 0.0,
            "unverified_count": 0,
            "avg_days_between_reviews": None,
            "min_days_between": None,
            "max_burst_14d": 0,
            "review_span_days": None,
            "sample_avg_rating": None,
            "sample_all_five_star": False,
        }

    single_count = 0
    single_known = 0
    verified_count = 0
    verified_known = 0
    ratings: list[int] = []
    published: list[datetime] = []

    for review in reviews:
        if review.author_review_count is not None:
            single_known += 1
            if review.author_review_count <= 1:
                single_count += 1

        if review.verified is not None:
            verified_known += 1
            if review.verified:
                verified_count += 1

        if review.rating is not None:
            ratings.append(int(review.rating))

        if review.published:
            published.append(review.published)

    sample_size = len(reviews)
    published.sort()

    gaps: list[float] = []
    for i in range(1, len(published)):
        gaps.append((published[i] - published[i - 1]).total_seconds() / 86400)

    max_burst_14d = 0
    for i, start in enumerate(published):
        burst = 1
        for j in range(i + 1, len(published)):
            if (published[j] - start).total_seconds() / 86400 <= 14:
                burst += 1
            else:
                break
        max_burst_14d = max(max_burst_14d, burst)

    span_days = None
    if len(published) >= 2:
        span_days = int((published[-1] - published[0]).total_seconds() / 86400)

    single_pct = (
        round(single_count / single_known * 100, 1) if single_known >= 3 else 0.0
    )
    verified_pct = (
        round(verified_count / verified_known * 100, 1) if verified_known >= 3 else 0.0
    )

    return {
        "sample_size": sample_size,
        "single_review_account_count": single_count,
        "single_review_account_known": single_known,
        "single_review_account_pct": single_pct,
        "verified_count": verified_count,
        "verified_pct": verified_pct,
        "verified_known": verified_known,
        "unverified_count": verified_known - verified_count if verified_known else 0,
        "avg_days_between_reviews": round(mean(gaps), 1) if gaps else None,
        "min_days_between": round(min(gaps), 1) if gaps else None,
        "max_burst_14d": max_burst_14d,
        "review_span_days": span_days,
        "sample_avg_rating": round(mean(ratings), 2) if ratings else None,
        "sample_all_five_star": bool(ratings) and all(r == 5 for r in ratings),
    }


def calculate_facade_score(
    *,
    max_score: int,
    avg_rating: float | None,
    total_reviews: int,
    analysis: dict[str, Any],
    is_collecting_reviews: bool | None = None,
    is_bad_category: bool = False,
    platform_label: str = "Reviews",
) -> tuple[int, list[dict], list[str]]:
    """Consolidated legitimacy score — detects inflated / facade review patterns."""
    score = max_score
    breakdown: list[dict] = []
    warnings: list[str] = []

    def deduct(points: int, label: str) -> None:
        nonlocal score
        score = max(0, score - points)
        breakdown.append({"label": label, "points": -points, "max_points": points})

    def bonus(points: int, label: str) -> None:
        nonlocal score
        before = score
        score = min(max_score, score + points)
        gained = score - before
        if gained:
            breakdown.append({"label": label, "points": gained, "max_points": points})

    breakdown.append({"label": "Ausgangswert", "points": max_score, "max_points": max_score})

    if total_reviews >= 100:
        bonus(0, "Viele Bewertungen (≥100) — etabliertes Profil")
    elif total_reviews >= 20:
        deduct(1, f"Moderate Bewertungszahl ({total_reviews})")
    elif total_reviews >= 5:
        deduct(3, f"Wenige Bewertungen ({total_reviews})")
    else:
        deduct(5, f"Sehr wenige Bewertungen ({total_reviews})")

    if avg_rating is not None:
        if avg_rating >= 4.5 and total_reviews < 10:
            deduct(3, f"Sehr hoher Score ({avg_rating}) bei <10 Bewertungen")
            warnings.append(f"Verdächtig hoher {platform_label}-Score bei sehr wenigen Bewertungen")
        elif avg_rating >= 4.2 and total_reviews < 15:
            deduct(2, f"Hoher Score ({avg_rating}) bei wenigen Bewertungen")
            warnings.append(f"Hoher {platform_label}-Score trotz geringer Bewertungsbasis")

    if is_collecting_reviews is False and total_reviews < 50:
        deduct(1, "Keine aktiven Bewertungseinladungen")
        warnings.append("Keine aktiven Bewertungseinladungen erkennbar")

    sample_size = analysis["sample_size"]
    if sample_size >= 3 and total_reviews < 100:
        single_known = analysis.get("single_review_account_known", 0)
        if single_known >= 3:
            single_pct = analysis["single_review_account_pct"]
            if single_pct > 70:
                deduct(2, f"{single_pct:.0f}% Einmal-Reviewer in Stichprobe")
                warnings.append("Überwiegend Einmal-Reviewer in der Stichprobe")
            elif single_pct > 50:
                deduct(1, f"{single_pct:.0f}% Einmal-Reviewer")

        verified_known = analysis.get("verified_known", 0)
        if verified_known >= 3:
            verified_pct = analysis["verified_pct"]
            if verified_pct < 40:
                deduct(2, f"Nur {verified_pct:.0f}% verifizierte Bewertungen")
                warnings.append("Viele unverifizierte Bewertungen")
            elif verified_pct < 70:
                deduct(1, f"{verified_pct:.0f}% verifiziert")
            elif verified_pct >= 80:
                bonus(1, f"{verified_pct:.0f}% verifizierte Bewertungen")

        if analysis["max_burst_14d"] >= 3:
            deduct(1, f"Bewertungs-Cluster: {analysis['max_burst_14d']} in 14 Tagen")
            warnings.append("Mehrere Bewertungen in kurzem Zeitraum")

        span = analysis.get("review_span_days")
        if span is not None and span < 60 and total_reviews < 20:
            deduct(2, f"Stichprobe innerhalb {span} Tagen konzentriert")
            warnings.append("Bewertungen konzentriert auf kurzen Zeitraum")

        if analysis.get("sample_all_five_star") and total_reviews < 25:
            deduct(1, "Stichprobe: ausschliesslich 5-Sterne-Bewertungen")

    if total_reviews >= 10 and analysis.get("review_span_days") is not None:
        if analysis["review_span_days"] >= 365:
            bonus(1, "Bewertungen über längeren Zeitraum verteilt")

    if is_bad_category and total_reviews < 30 and avg_rating is not None and avg_rating >= 4.0:
        deduct(1, "Risikobranche + hoher Score + wenig Reviews")
        warnings.append("Risikobranche mit wenigen sehr positiven Bewertungen")

    ui_breakdown: list[dict] = []
    for item in breakdown[1:]:
        if item["points"] < 0:
            ui_breakdown.append({
                "label": item["label"],
                "points": 0,
                "max_points": abs(item["points"]),
            })
        else:
            ui_breakdown.append({
                "label": item["label"],
                "points": item["points"],
                "max_points": item["max_points"],
            })

    return score, ui_breakdown, warnings


def facade_status(score: int, max_score: int) -> str:
    if score >= max_score * 0.75:
        return "passed"
    if score >= max_score * 0.42:
        return "warning"
    return "failed"
