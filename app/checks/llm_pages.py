"""Gather multi-page text for LLM fraud analysis."""

from __future__ import annotations

import re
from urllib.parse import urljoin

from app.checks.browser_render import fetch_rendered_html
from app.checks.utils import strip_html_for_text

_LEGAL_LINK_PATTERN = re.compile(
    r"impressum|imprint|mentions\s+l[eé]gales|contatti|datenschutz|privacy|"
    r"nutzungsbedingungen|terms\s+of\s+(service|use)|legal\s+notice|"
    r"widerruf|agb|allgemeine\s+gesch",
    re.IGNORECASE,
)

_SUBPAGE_PATTERN = re.compile(
    r"kontakt|contact|impressum|imprint|about-us|über-uns|mentions-legales|contatti|"
    r"datenschutz|privacy|agb|terms|legal|widerruf",
    re.IGNORECASE,
)

_MAX_SECTION_CHARS = 2800
_MAX_TOTAL_CHARS = 9000


def _extract_visible_text(html: str, max_chars: int) -> str:
    text = strip_html_for_text(html)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def _same_site(base_url: str, candidate_url: str) -> bool:
    from urllib.parse import urlparse

    base_host = urlparse(base_url).netloc.lower().removeprefix("www.")
    cand_host = urlparse(candidate_url).netloc.lower().removeprefix("www.")
    return base_host == cand_host or cand_host.endswith(f".{base_host}")


def _discover_subpage_urls(html: str, base_url: str, limit: int = 2) -> list[tuple[str, str]]:
    """Return [(label, url), ...] for legal/contact subpages."""
    seen: set[str] = set()
    found: list[tuple[str, str]] = []

    for m in re.finditer(
        r"""<a\b[^>]*href=["']([^"']+)["'][^>]*>([^<]*)</a>""",
        html,
        re.IGNORECASE,
    ):
        href, link_text = m.group(1).strip(), m.group(2).strip()
        if href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        combined = f"{link_text} {href}"
        if not _SUBPAGE_PATTERN.search(combined):
            continue
        full_url = urljoin(base_url, href)
        if not full_url.startswith(("http://", "https://")):
            continue
        if not _same_site(base_url, full_url):
            continue
        normalized = full_url.split("#")[0].rstrip("/")
        if normalized in seen:
            continue
        seen.add(normalized)
        label = link_text.strip() or href.split("/")[-1] or "subpage"
        found.append((label[:40], normalized))
        if len(found) >= limit:
            break
    return found


async def gather_llm_content(target_url: str, timeout_ms: int = 15000) -> dict:
    """
    Fetch homepage plus up to 4 legal/contact subpages.
    Returns render metadata and a structured text bundle for the LLM.
    """
    homepage = await fetch_rendered_html(target_url, timeout_ms=timeout_ms)
    if not homepage.get("success"):
        return {
            "success": False,
            "error": homepage.get("error") or "Homepage not loadable",
            "html": homepage.get("html", ""),
            "status_code": homepage.get("status_code"),
            "final_url": homepage.get("final_url") or target_url,
            "sections": [],
            "corpus": "",
        }

    html = homepage["html"]
    final_url = homepage.get("final_url") or target_url
    sections: list[dict] = [{
        "label": "Startseite",
        "url": final_url,
        "text": _extract_visible_text(html, _MAX_SECTION_CHARS),
    }]

    for label, sub_url in _discover_subpage_urls(html, final_url):
        sub = await fetch_rendered_html(sub_url, timeout_ms=min(timeout_ms, 12000))
        if not sub.get("success"):
            continue
        text = _extract_visible_text(sub.get("html", ""), _MAX_SECTION_CHARS)
        if len(text) < 60:
            continue
        sections.append({"label": label, "url": sub_url, "text": text})

    parts: list[str] = []
    total = 0
    for section in sections:
        block = f"=== {section['label']} ({section['url']}) ===\n{section['text']}"
        if total + len(block) > _MAX_TOTAL_CHARS:
            remaining = _MAX_TOTAL_CHARS - total
            if remaining > 200:
                parts.append(block[:remaining])
            break
        parts.append(block)
        total += len(block)

    corpus = "\n\n".join(parts)

    return {
        "success": True,
        "error": None,
        "html": html,
        "status_code": homepage.get("status_code"),
        "final_url": final_url,
        "sections": sections,
        "corpus": corpus,
        "section_count": len(sections),
    }
