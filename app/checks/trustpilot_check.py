"""Trustpilot reputation check — detects facade / inflated review patterns."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from statistics import mean
from typing import Any, Optional

from app.checks.base import BaseCheck
from app.checks.browser_render import fetch_rendered_html
from app.checks.trustpilot_review_llm import analyze_negative_reviews
from app.models import CheckResult, CheckStatus

_MAX_SCORE = 12
_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
    re.DOTALL,
)

_TRUSTPILOT_LOCALES = ("de", "www")


def _parse_next_data(html: str) -> dict[str, Any] | None:
    match = _NEXT_DATA_RE.search(html)
    if not match:
        return None
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    page_props = payload.get("props", {}).get("pageProps")
    return page_props if isinstance(page_props, dict) else None


def _parse_review_date(review: dict) -> datetime | None:
    dates = review.get("dates") or {}
    raw = dates.get("publishedDate") or dates.get("experiencedDate")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _analyze_reviews(reviews: list[dict]) -> dict[str, Any]:
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
    verified_count = 0
    ratings: list[int] = []
    published: list[datetime] = []

    for review in reviews:
        consumer = review.get("consumer") or {}
        if consumer.get("numberOfReviews", 0) <= 1:
            single_count += 1

        verification = (review.get("labels") or {}).get("verification") or {}
        if verification.get("isVerified"):
            verified_count += 1

        rating = review.get("rating")
        if isinstance(rating, (int, float)):
            ratings.append(int(rating))

        dt = _parse_review_date(review)
        if dt:
            published.append(dt)

    sample_size = len(reviews)
    published.sort()

    gaps: list[float] = []
    for i in range(1, len(published)):
        gap = (published[i] - published[i - 1]).total_seconds() / 86400
        gaps.append(gap)

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

    return {
        "sample_size": sample_size,
        "single_review_account_count": single_count,
        "single_review_account_pct": round(single_count / sample_size * 100, 1),
        "verified_count": verified_count,
        "verified_pct": round(verified_count / sample_size * 100, 1),
        "unverified_count": sample_size - verified_count,
        "avg_days_between_reviews": round(mean(gaps), 1) if gaps else None,
        "min_days_between": round(min(gaps), 1) if gaps else None,
        "max_burst_14d": max_burst_14d,
        "review_span_days": span_days,
        "sample_avg_rating": round(mean(ratings), 2) if ratings else None,
        "sample_all_five_star": bool(ratings) and all(r == 5 for r in ratings),
    }


def _review_entries(reviews: list[dict], limit: int = 10) -> list[dict]:
    entries: list[dict] = []
    for review in reviews[:limit]:
        consumer = review.get("consumer") or {}
        verification = (review.get("labels") or {}).get("verification") or {}
        dates = review.get("dates") or {}
        reply = review.get("reply") or {}
        entries.append({
            "rating": review.get("rating"),
            "author": consumer.get("displayName"),
            "country": consumer.get("countryCode"),
            "author_total_reviews": consumer.get("numberOfReviews"),
            "verified": bool(verification.get("isVerified")),
            "verification_source": verification.get("verificationSource"),
            "published": dates.get("publishedDate"),
            "title": (review.get("title") or "")[:80],
            "text": (review.get("text") or "")[:400],
            "company_reply": (reply.get("message") or "")[:200] if isinstance(reply, dict) else "",
        })
    return entries


def _negative_review_payload(reviews: list[dict], limit: int = 6) -> list[dict]:
    """Compact payload for LLM analysis."""
    payload: list[dict] = []
    for idx, review in enumerate(reviews[:limit], 1):
        consumer = review.get("consumer") or {}
        reply = review.get("reply") or {}
        dates = review.get("dates") or {}
        payload.append({
            "index": idx,
            "rating": review.get("rating"),
            "title": (review.get("title") or "")[:120],
            "text": (review.get("text") or "")[:800],
            "author": consumer.get("displayName"),
            "verified": bool((review.get("labels") or {}).get("verification", {}).get("isVerified")),
            "published": dates.get("publishedDate"),
            "company_reply": (reply.get("message") or "")[:300] if isinstance(reply, dict) else "",
        })
    return payload


async def _fetch_page_props(domain: str, locale: str, stars: Optional[int] = None) -> Optional[dict]:
    suffix = f"?stars={stars}" if stars else ""
    url = f"https://{locale}.trustpilot.com/review/{domain}{suffix}"
    try:
        render = await fetch_rendered_html(url, timeout_ms=22000)
    except Exception:
        return None
    if not render.get("success"):
        return None
    return _parse_next_data(render.get("html", ""))


def _merge_negative_reviews(*review_lists: list[dict]) -> list[dict]:
    seen: set[str] = set()
    merged: list[dict] = []
    for reviews in review_lists:
        for review in reviews:
            if not isinstance(review, dict):
                continue
            rating = review.get("rating")
            if not isinstance(rating, (int, float)) or int(rating) > 2:
                continue
            rid = str(review.get("id") or "")
            if rid and rid in seen:
                continue
            if rid:
                seen.add(rid)
            merged.append(review)
    return merged[:6]


async def _fetch_negative_reviews(domain: str, locale: str) -> list[dict]:
    props_one, props_two = await asyncio.gather(
        _fetch_page_props(domain, locale, 1),
        _fetch_page_props(domain, locale, 2),
    )
    lists: list[list[dict]] = []
    for props in (props_one, props_two):
        if not props:
            continue
        raw = props.get("reviews") or []
        if isinstance(raw, list):
            lists.append(raw)
    return _merge_negative_reviews(*lists) if lists else []


def _is_bad_category(page_props: dict) -> bool:
    categories = (
        page_props.get("sidebarData", {})
        .get("infoBusinessUnitBox", {})
        .get("categories", [])
    )
    return any(isinstance(c, dict) and c.get("isBadCategory") for c in categories)


def _calculate_consolidated_score(
    trust_score: float | None,
    total_reviews: int,
    is_collecting_reviews: bool,
    is_bad_category: bool,
    analysis: dict[str, Any],
    negative_analysis: Optional[dict[str, Any]] = None,
) -> tuple[int, list[dict], list[str]]:
    """Return (score, score_breakdown, warning_flags)."""
    score = _MAX_SCORE
    breakdown: list[dict] = []
    warnings: list[str] = []

    def deduct(points: int, label: str) -> None:
        nonlocal score
        score = max(0, score - points)
        breakdown.append({"label": label, "points": -points, "max_points": points})

    def bonus(points: int, label: str) -> None:
        nonlocal score
        before = score
        score = min(_MAX_SCORE, score + points)
        gained = score - before
        if gained:
            breakdown.append({"label": label, "points": gained, "max_points": points})

    breakdown.append({"label": "Ausgangswert", "points": _MAX_SCORE, "max_points": _MAX_SCORE})

    if total_reviews >= 100:
        bonus(0, "Viele Bewertungen (≥100) — etabliertes Profil")
    elif total_reviews >= 20:
        deduct(1, f"Moderate Bewertungszahl ({total_reviews})")
    elif total_reviews >= 5:
        deduct(3, f"Wenige Bewertungen ({total_reviews})")
    else:
        deduct(5, f"Sehr wenige Bewertungen ({total_reviews})")

    if trust_score is not None:
        if trust_score >= 4.5 and total_reviews < 10:
            deduct(3, f"Sehr hoher Score ({trust_score}) bei <10 Bewertungen")
            warnings.append("Verdächtig hoher TrustScore bei sehr wenigen Bewertungen")
        elif trust_score >= 4.2 and total_reviews < 15:
            deduct(2, f"Hoher Score ({trust_score}) bei wenigen Bewertungen")
            warnings.append("Hoher TrustScore trotz geringer Bewertungsbasis")

    sample_size = analysis["sample_size"]
    if not is_collecting_reviews and total_reviews < 50:
        deduct(1, "Keine aktiven Bewertungseinladungen (Trustpilot)")
        warnings.append("Unternehmen lädt laut Trustpilot nicht aktiv zu Bewertungen ein")

    if sample_size >= 3 and total_reviews < 100:
        single_pct = analysis["single_review_account_pct"]
        if single_pct > 70:
            deduct(2, f"{single_pct:.0f}% der Stichprobe: Accounts mit nur 1 Bewertung")
            warnings.append("Überwiegend Einmal-Reviewer in der Stichprobe")
        elif single_pct > 50:
            deduct(1, f"{single_pct:.0f}% Einmal-Reviewer")

        verified_pct = analysis["verified_pct"]
        if verified_pct < 40:
            deduct(2, f"Nur {verified_pct:.0f}% verifizierte Bewertungen (Stichprobe)")
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
            deduct(2, f"Alle Stichproben-Bewertungen innerhalb {span} Tagen")
            warnings.append("Bewertungen konzentriert auf kurzen Zeitraum")

        if analysis.get("sample_all_five_star") and total_reviews < 25:
            deduct(1, "Stichprobe: ausschliesslich 5-Sterne-Bewertungen")

    if total_reviews >= 10 and analysis.get("review_span_days") is not None:
        if analysis["review_span_days"] >= 365:
            bonus(1, "Bewertungen über längeren Zeitraum verteilt")

    if is_bad_category and total_reviews < 30 and trust_score is not None and trust_score >= 4.0:
        deduct(1, "Risikokategorie (z.B. Krypto) + hoher Score + wenig Reviews")
        warnings.append("Trustpilot-Risikokategorie bei wenigen positiven Bewertungen")

    if negative_analysis and not negative_analysis.get("skipped"):
        overall = negative_analysis.get("overall_severity", "none")
        fraud_count = int(negative_analysis.get("fraud_signal_count") or 0)
        neg_summary = (negative_analysis.get("summary") or "").strip()

        if overall == "high":
            deduct(2, "KI: Negative Reviews mit Betrugsverdacht")
            warnings.append(neg_summary or "Negative Reviews: Betrugs-/Betrugsverdacht laut KI-Analyse")
            if fraud_count >= 2:
                deduct(1, f"KI: {fraud_count} unabhängige Betrugs-Beschwerden in Reviews")
                warnings.append(f"{fraud_count} negative Reviews mit glaubwürdigen Betrugsvorwürfen")
        elif overall == "low":
            deduct(1, "KI: Negative Reviews mit berechtigter Kritik")
            if neg_summary:
                warnings.append(neg_summary)

    # Normalize breakdown for UI (positive display format)
    ui_breakdown = []
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


def _build_summary(
    score: int,
    trust_score: float | None,
    total_reviews: int,
    warnings: list[str],
) -> str:
    ts = f"{trust_score}/5" if trust_score is not None else "n/a"
    base = f"Trustpilot {ts}, {total_reviews} Bewertungen — Legitimitätsscore {score}/{_MAX_SCORE}"
    if warnings:
        return f"{base} — {warnings[0]}"
    if score >= 9:
        return f"{base} — Profil wirkt glaubwürdig"
    if score >= 5:
        return f"{base} — einzelne Warnsignale"
    return f"{base} — mögliche Review-Fassade"


class TrustpilotCheck(BaseCheck):
    name = "trustpilot"
    display_name = "Trustpilot"
    max_score = _MAX_SCORE
    tier = 2

    async def run(self, domain: str, **kwargs) -> CheckResult:
        last_error: str | None = None

        for locale in _TRUSTPILOT_LOCALES:
            try:
                page_props = await _fetch_page_props(domain, locale)
            except Exception as e:
                last_error = str(e)[:120]
                continue

            if not page_props:
                last_error = "Trustpilot-Daten nicht parsebar"
                continue

            business = page_props.get("businessUnit")
            if not isinstance(business, dict):
                return CheckResult(
                    name=self.name,
                    display_name=self.display_name,
                    status=CheckStatus.NA,
                    score=0,
                    max_score=self.max_score,
                    summary="Kein Trustpilot-Profil für diese Domain gefunden",
                    details={"profile_found": False, "domain": domain},
                )

            trust_score = business.get("trustScore")
            if isinstance(trust_score, (int, float)):
                trust_score = float(trust_score)
            else:
                trust_score = None

            total_reviews = int(business.get("numberOfReviews") or 0)
            reviews_last_12m = int(business.get("numberOfReviewsLast12Months") or 0)
            is_collecting = bool(business.get("isCollectingReviews"))
            is_claimed = bool(business.get("isClaimed"))
            display_name = business.get("displayName") or domain
            bad_category = _is_bad_category(page_props)

            raw_reviews = page_props.get("reviews") or []
            if not isinstance(raw_reviews, list):
                raw_reviews = []

            negative_raw = await _fetch_negative_reviews(domain, locale)
            negative_payload = _negative_review_payload(negative_raw)
            negative_analysis = await analyze_negative_reviews(
                negative_payload,
                business_name=display_name,
            )

            analysis = _analyze_reviews(raw_reviews)
            score, score_breakdown, warnings = _calculate_consolidated_score(
                trust_score=trust_score,
                total_reviews=total_reviews,
                is_collecting_reviews=is_collecting,
                is_bad_category=bad_category,
                analysis=analysis,
                negative_analysis=negative_analysis,
            )

            if score >= 9:
                status = CheckStatus.PASSED
            elif score >= 5:
                status = CheckStatus.WARNING
            else:
                status = CheckStatus.FAILED

            profile_url = f"https://www.trustpilot.com/review/{domain}"

            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=status,
                score=score,
                max_score=self.max_score,
                summary=_build_summary(score, trust_score, total_reviews, warnings),
                details={
                    "profile_found": True,
                    "profile_url": profile_url,
                    "display_name": display_name,
                    "trust_score": trust_score,
                    "stars": business.get("stars"),
                    "total_reviews": total_reviews,
                    "reviews_last_12_months": reviews_last_12m,
                    "is_claimed": is_claimed,
                    "is_collecting_reviews": is_collecting,
                    "is_bad_category": bad_category,
                    "consolidated_score": score,
                    "analysis": analysis,
                    "review_sample": _review_entries(raw_reviews),
                    "negative_reviews": _review_entries(negative_raw, limit=6),
                    "negative_review_analysis": negative_analysis,
                    "warning_flags": warnings,
                    "score_breakdown": score_breakdown,
                    "locale_used": locale,
                },
            )

        if last_error:
            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=CheckStatus.ERROR,
                score=0,
                max_score=self.max_score,
                summary=f"Trustpilot-Check fehlgeschlagen: {last_error}",
                details={},
            )

        return CheckResult(
            name=self.name,
            display_name=self.display_name,
            status=CheckStatus.NA,
            score=0,
            max_score=self.max_score,
            summary="Kein Trustpilot-Profil für diese Domain gefunden",
            details={"profile_found": False, "domain": domain},
        )
