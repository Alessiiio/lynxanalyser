"""FINMA public warning list lookup via sitemap (full list, cached)."""

from __future__ import annotations

import re
import time
from typing import Optional

import httpx

_SITEMAP_URL = "https://www.finma.ch/de/sitemap.xml"
_WARNLIST_BASE = "https://www.finma.ch/de/finma-public/warnungen/warnliste/"
_CACHE_TTL = 24 * 3600

_cached_slug_index: dict[str, str] | None = None  # slug -> canonical URL
_cached_at: float = 0.0

_SITEMAP_WARNLIST_RE = re.compile(
    r"<loc>(https?://[^<]*/warnliste/([^/<]+)/?)</loc>",
    re.IGNORECASE,
)


_GENERIC_COMPANY_TOKENS = frozenset({
    "private", "bank", "finance", "capital", "invest", "investment", "investments",
    "group", "holding", "trust", "fund", "funds", "wealth", "asset", "assets",
    "management", "services", "solutions", "global", "international", "swiss",
    "schweiz", "schweizer", "partner", "partners", "advisory", "beratung", "anlage", "trading",
    "markets", "credit", "insurance", "equity", "alternatives", "online", "digital",
    "financial", "securities", "broker", "consulting", "company", "limited", "ltd",
})


def _distinctive_tokens(text: str) -> set[str]:
    tokens = {
        t for t in re.split(r"[\s_\-]+", text.lower())
        if len(t) >= 5 and t not in _GENERIC_COMPANY_TOKENS
    }
    return tokens


def _company_matches_slug(company_lower: str, slug: str) -> bool:
    """Avoid false positives from generic finance words or tagline overlap."""
    if len(company_lower) < 4:
        return False

    slug_text = slug.replace("-", " ").replace("_", " ").strip().lower()
    company_compact = re.sub(r"\s+", "", company_lower)
    slug_compact = re.sub(r"\s+", "", slug_text)

    if company_compact == slug_compact or company_lower == slug_text:
        return True

    company_tokens = _distinctive_tokens(company_lower)
    slug_tokens = _distinctive_tokens(slug_text)
    overlap = company_tokens & slug_tokens
    if len(overlap) >= 2:
        return True
    if len(overlap) == 1:
        token = next(iter(overlap))
        # Single-word overlap only for short names — avoids "Schweizer …" vs SRF-style false hits.
        if (
            len(token) >= 6
            and len(company_lower.split()) <= 3
            and len(slug_text.split()) <= 4
        ):
            return True

    # Single-token slug that equals a distinctive company token (e.g. "migros").
    if len(slug_tokens) == 1 and slug_tokens <= company_tokens:
        return True

    return False


def _domain_to_finma_slug(domain: str) -> str:
    """FINMA URL slugs use underscores instead of dots (nvb-beratung.de → nvb-beratung_de)."""
    return domain.lower().removeprefix("www.").replace(".", "_")


def _finma_slug_to_domain(slug: str) -> str | None:
    """Best-effort reverse: nvb-beratung_de → nvb-beratung.de."""
    if "_" not in slug:
        return None
    host, tld = slug.rsplit("_", 1)
    if not tld.isalpha() or len(tld) < 2:
        return None
    return f"{host}.{tld}"


def _slug_display_name(slug: str) -> str:
    domain = _finma_slug_to_domain(slug)
    if domain:
        return domain
    return slug.replace("-", " ").replace("_", " ")


async def _fetch_slug_index() -> dict[str, str]:
    """Load all warnlist entry slugs from the FINMA DE sitemap."""
    global _cached_slug_index, _cached_at
    if _cached_slug_index is not None and (time.time() - _cached_at) < _CACHE_TTL:
        return _cached_slug_index

    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        resp = await client.get(
            _SITEMAP_URL,
            headers={"User-Agent": "Mozilla/5.0 (compatible; Lynx/1.0)"},
        )
        resp.raise_for_status()
        html = resp.text

    index: dict[str, str] = {}
    for m in _SITEMAP_WARNLIST_RE.finditer(html):
        url, slug = m.group(1).strip(), m.group(2).strip().lower()
        index[slug] = url

    _cached_slug_index = index
    _cached_at = time.time()
    return index


async def _lookup_detail_page(domain: str) -> Optional[dict]:
    """Direct detail-page probe when slug is known (handles fresh entries before sitemap refresh)."""
    slug = _domain_to_finma_slug(domain)
    url = f"{_WARNLIST_BASE}{slug}/"
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        resp = await client.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; Lynx/1.0)"},
        )
    if resp.status_code != 200:
        return None
    if "page-not-found" in str(resp.url).lower():
        return None
    body = resp.text.lower()
    if domain.lower() in body or slug.replace("_", ".") in body:
        return {
            "match_type": "domain",
            "name": domain,
            "url": str(resp.url),
            "slug": slug,
        }
    return None


async def find_finma_warning(domain: str, company_name: str | None = None) -> Optional[dict]:
    domain = domain.lower().removeprefix("www.")
    company_lower = (company_name or "").strip().lower()
    target_slug = _domain_to_finma_slug(domain)

    try:
        index = await _fetch_slug_index()
    except Exception:
        index = {}

    if target_slug in index:
        return {
            "match_type": "domain",
            "name": _slug_display_name(target_slug),
            "url": index[target_slug],
            "slug": target_slug,
        }

    for slug, url in index.items():
        mapped = _finma_slug_to_domain(slug)
        if mapped and mapped == domain:
            return {
                "match_type": "domain",
                "name": _slug_display_name(slug),
                "url": url,
                "slug": slug,
            }

        if company_lower and _company_matches_slug(company_lower, slug):
            return {
                "match_type": "company",
                "name": _slug_display_name(slug),
                "url": url,
                "slug": slug,
            }

    try:
        direct = await _lookup_detail_page(domain)
        if direct:
            return direct
    except Exception:
        pass

    return None
