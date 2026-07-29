"""Google Business / Maps reviews — facade detection via Places API."""

from __future__ import annotations

import httpx

import config
from app.checks.base import BaseCheck
from app.checks.review_facade import (
    NormalizedReview,
    analyze_review_patterns,
    calculate_facade_score,
    parse_iso_datetime,
)
from app.models import CheckResult, CheckStatus

_MAX_SCORE = 12
_PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
_RISK_TYPES = frozenset({
    "finance",
    "financial_service",
    "investment_service",
    "cryptocurrency_exchange",
    "insurance_agency",
})


def _domain_matches_place(domain: str, website_uri: str | None) -> bool:
    if not website_uri:
        return False
    try:
        host = urlparse(website_uri).netloc.lower().removeprefix("www.")
    except Exception:
        return False
    target = domain.lower().removeprefix("www.")
    return host == target or host.endswith(f".{target}") or target in host


def _is_risk_category(types: list[str] | None) -> bool:
    if not types:
        return False
    normalized = {t.lower() for t in types}
    return bool(normalized & _RISK_TYPES) or any(
        "crypto" in t or "invest" in t for t in normalized
    )


def _normalize_google_reviews(raw_reviews: list[dict]) -> list[NormalizedReview]:
    reviews: list[NormalizedReview] = []
    for item in raw_reviews:
        if not isinstance(item, dict):
            continue
        text_obj = item.get("text") or {}
        text = text_obj.get("text") if isinstance(text_obj, dict) else None
        author = (item.get("authorAttribution") or {}).get("displayName")
        reviews.append(
            NormalizedReview(
                rating=item.get("rating"),
                author=author,
                author_review_count=None,
                verified=True,
                published=parse_iso_datetime(item.get("publishTime")),
                text=(text or "")[:200] or None,
            )
        )
    return reviews


def _review_sample(reviews: list[NormalizedReview], limit: int = 10) -> list[dict]:
    sample: list[dict] = []
    for rev in reviews[:limit]:
        sample.append({
            "rating": rev.rating,
            "author": rev.author,
            "verified": rev.verified,
            "published": rev.published.isoformat() if rev.published else None,
            "text": rev.text,
        })
    return sample


async def _places_search(client: httpx.AsyncClient, query: str, api_key: str) -> list[dict]:
    resp = await client.post(
        _PLACES_SEARCH_URL,
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": (
                "places.id,places.displayName,places.rating,places.userRatingCount,"
                "places.googleMapsUri,places.websiteUri,places.types"
            ),
        },
        json={"textQuery": query, "languageCode": "de", "regionCode": "CH"},
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Places search HTTP {resp.status_code}: {resp.text[:160]}")
    return resp.json().get("places") or []


async def _place_details(client: httpx.AsyncClient, place_id: str, api_key: str) -> dict:
    resource = place_id if place_id.startswith("places/") else f"places/{place_id}"
    resp = await client.get(
        f"https://places.googleapis.com/v1/{resource}",
        headers={
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": (
                "id,displayName,rating,userRatingCount,googleMapsUri,websiteUri,types,reviews"
            ),
        },
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Place details HTTP {resp.status_code}: {resp.text[:160]}")
    return resp.json()


def _pick_place(places: list[dict], domain: str) -> dict | None:
    if not places:
        return None
    for place in places:
        if _domain_matches_place(domain, place.get("websiteUri")):
            return place
    return places[0]


def _build_summary(
    score: int,
    rating: float | None,
    total_reviews: int,
    warnings: list[str],
    display_name: str | None,
) -> str:
    name = display_name or "Unternehmen"
    rs = f"{rating}/5" if rating is not None else "n/a"
    base = f"Google {rs}, {total_reviews} Bewertungen ({name}) — Score {score}/{_MAX_SCORE}"
    if warnings:
        return f"{base} — {warnings[0]}"
    if score >= 9:
        return f"{base} — Profil wirkt glaubwürdig"
    if score >= 5:
        return f"{base} — einzelne Warnsignale"
    return f"{base} — mögliche Review-Fassade"


class GoogleReviewsCheck(BaseCheck):
    name = "google_reviews"
    display_name = "Google Bewertungen"
    max_score = _MAX_SCORE
    tier = 2

    async def run(self, domain: str, company: str | None = None, **kwargs) -> CheckResult:
        api_key = config.GOOGLE_PLACES_API_KEY
        if not api_key:
            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=CheckStatus.SKIPPED,
                score=0,
                max_score=self.max_score,
                summary="Google Places API key not configured",
                details={
                    "skipped": True,
                    "setup_url": "https://console.cloud.google.com/google/maps-apis/credentials",
                    "note": "Enable Places API (New) and set GOOGLE_PLACES_API_KEY in .env",
                },
            )

        queries = [q for q in [company, domain, domain.split(".")[0]] if q]
        last_error: str | None = None

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                place: dict | None = None
                for query in queries:
                    try:
                        results = await _places_search(client, query, api_key)
                    except Exception as e:
                        last_error = str(e)[:120]
                        continue
                    place = _pick_place(results, domain)
                    if place:
                        break

                if not place:
                    return CheckResult(
                        name=self.name,
                        display_name=self.display_name,
                        status=CheckStatus.NA,
                        score=0,
                        max_score=self.max_score,
                        summary="Kein Google-Unternehmensprofil gefunden",
                        details={"profile_found": False, "domain": domain},
                    )

                place_id = place.get("id")
                if not place_id:
                    return CheckResult(
                        name=self.name,
                        display_name=self.display_name,
                        status=CheckStatus.ERROR,
                        score=0,
                        max_score=self.max_score,
                        summary="Google Places: Eintrag ohne ID",
                        details={},
                    )

                details_data = await _place_details(client, place_id, api_key)

        except Exception as e:
            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=CheckStatus.ERROR,
                score=0,
                max_score=self.max_score,
                summary=f"Google-Bewertungen fehlgeschlagen: {str(e)[:120]}",
                details={},
            )

        rating = details_data.get("rating")
        if isinstance(rating, (int, float)):
            rating = float(rating)
        else:
            rating = None

        total_reviews = int(details_data.get("userRatingCount") or 0)
        display_name = (details_data.get("displayName") or {}).get("text") or place.get("displayName", {}).get("text")
        types = details_data.get("types") or place.get("types") or []
        bad_category = _is_risk_category(types if isinstance(types, list) else [])

        raw_reviews = details_data.get("reviews") or []
        if not isinstance(raw_reviews, list):
            raw_reviews = []

        normalized = _normalize_google_reviews(raw_reviews)
        analysis = analyze_review_patterns(normalized)
        score, score_breakdown, warnings = calculate_facade_score(
            max_score=self.max_score,
            avg_rating=rating,
            total_reviews=total_reviews,
            analysis=analysis,
            is_collecting_reviews=None,
            is_bad_category=bad_category,
            platform_label="Google-Rating",
        )

        if score >= 9:
            status = CheckStatus.PASSED
        elif score >= 5:
            status = CheckStatus.WARNING
        else:
            status = CheckStatus.FAILED

        maps_uri = details_data.get("googleMapsUri") or place.get("googleMapsUri")

        return CheckResult(
            name=self.name,
            display_name=self.display_name,
            status=status,
            score=score,
            max_score=self.max_score,
            summary=_build_summary(score, rating, total_reviews, warnings, display_name),
            details={
                "profile_found": True,
                "profile_url": maps_uri,
                "display_name": display_name,
                "rating": rating,
                "total_reviews": total_reviews,
                "is_bad_category": bad_category,
                "place_types": types[:8] if isinstance(types, list) else [],
                "consolidated_score": score,
                "analysis": analysis,
                "review_sample": _review_sample(normalized),
                "warning_flags": warnings,
                "score_breakdown": score_breakdown,
                "reviews_fetched": len(normalized),
            },
        )
