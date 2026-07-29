"""Social media presence check — links from site + lightweight profile evaluation."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from app.checks.base import BaseCheck
from app.checks.browser_render import fetch_rendered_html
from app.models import CheckResult, CheckStatus

_MAX_SCORE = 8
_MAX_PROFILES_TO_PROBE = 4

_PLATFORM_META = {
    "linkedin": {"label": "LinkedIn", "icon": "in"},
    "instagram": {"label": "Instagram", "icon": "ig"},
    "facebook": {"label": "Facebook", "icon": "fb"},
    "twitter": {"label": "X/Twitter", "icon": "x"},
    "youtube": {"label": "YouTube", "icon": "yt"},
    "tiktok": {"label": "TikTok", "icon": "tk"},
}

_LINK_PATTERNS: dict[str, re.Pattern[str]] = {
    "linkedin": re.compile(
        r"https?://(?:[\w-]+\.)?linkedin\.com/(?:company|showcase|in)/[\w\-_%]+/?",
        re.I,
    ),
    "instagram": re.compile(r"https?://(?:www\.)?instagram\.com/[\w\.\-_]+/?", re.I),
    "facebook": re.compile(r"https?://(?:www\.)?facebook\.com/[\w\.\-_]+/?", re.I),
    "twitter": re.compile(r"https?://(?:www\.)?(?:twitter|x)\.com/[\w_]+/?", re.I),
    "youtube": re.compile(
        r"https?://(?:www\.)?youtube\.com/(?:@[\w\.\-_]+|channel/[\w\-]+|c/[\w\-]+)/?",
        re.I,
    ),
    "tiktok": re.compile(r"https?://(?:www\.)?tiktok\.com/@[\w\.\-_]+/?", re.I),
}

_SKIP_URL_PARTS = (
    "/share", "/sharer", "/intent/", "/plugins/", "share.php", "linkedin.com/share",
    "facebook.com/tr", "twitter.com/intent", "/privacy", "/help", "/about",
)

_HREF_RE = re.compile(r"""href=["']([^"'#]+)["']""", re.I)
_BARE_SOCIAL_RE = re.compile(
    r"(?:https?:)?//?(?:[\w-]+\.)?"
    r"(?:linkedin\.com/(?:company|showcase|in)/[\w\-_%]+|"
    r"instagram\.com/[\w\.\-_]+|"
    r"facebook\.com/[\w\.\-_]+|"
    r"(?:twitter|x)\.com/[\w_]+|"
    r"youtube\.com/(?:@[\w\.\-_]+|channel/[\w\-]+|c/[\w\-]+)|"
    r"tiktok\.com/@[\w\.\-_]+)"
    r"/?",
    re.I,
)
_OG_DESC_RE = re.compile(
    r'<meta\s+(?:property|name)=["\']og:description["\']\s+content=["\']([^"\']+)["\']',
    re.I,
)
_FOLLOWER_RE = re.compile(
    r"(\d[\d,\.]*)\s*(?:K|M)?\s*(?:Followers?|follower|Abonnenten|subscribers?)",
    re.I,
)


def _clean_url(url: str) -> str:
    url = url.strip().split("?")[0].split("#")[0].rstrip("/")
    return url


def _is_skippable_social_url(url: str) -> bool:
    lower = url.lower()
    return any(part in lower for part in _SKIP_URL_PARTS)


def _normalize_social_url(raw: str) -> str:
    raw = raw.strip().split("?")[0].split("#")[0].rstrip("/")
    if raw.startswith("//"):
        raw = "https:" + raw
    elif not raw.startswith("http"):
        raw = "https://" + raw.lstrip("/")
    return _clean_url(raw)


def _extract_social_links(html: str, base_url: str) -> dict[str, str]:
    found: dict[str, str] = {}
    candidates: set[str] = set(_HREF_RE.findall(html))
    candidates.update(_BARE_SOCIAL_RE.findall(html))

    for href in candidates:
        full = _normalize_social_url(href)
        if _is_skippable_social_url(full):
            continue
        for platform, pattern in _LINK_PATTERNS.items():
            if platform in found:
                continue
            if pattern.search(full):
                found[platform] = full
    return found


def _parse_follower_count(text: str) -> int | None:
    if not text:
        return None
    match = _FOLLOWER_RE.search(text)
    if not match:
        return None
    raw = match.group(1).replace(",", "").replace(".", "")
    try:
        value = float(raw)
    except ValueError:
        return None
    if "K" in match.group(0).upper():
        value *= 1000
    if "M" in match.group(0).upper():
        value *= 1_000_000
    return int(value)


async def _probe_profile(platform: str, url: str) -> dict[str, Any]:
    meta = _PLATFORM_META[platform]
    result: dict[str, Any] = {
        "platform": platform,
        "label": meta["label"],
        "url": url,
        "reachable": False,
        "followers": None,
        "status": "unknown",
        "note": "",
    }

    try:
        render = await fetch_rendered_html(url, timeout_ms=20000)
    except Exception as e:
        result["status"] = "error"
        result["note"] = str(e)[:80]
        return result

    if not render.get("success"):
        result["status"] = "unreachable"
        result["note"] = render.get("error") or "Profil nicht erreichbar"
        return result

    html = render.get("html", "")
    if len(html) < 500:
        result["status"] = "empty"
        result["note"] = "Sehr wenig Inhalt — Profil evtl. blockiert oder leer"
        return result

    result["reachable"] = True
    og_match = _OG_DESC_RE.search(html)
    og_desc = og_match.group(1) if og_match else ""
    followers = _parse_follower_count(og_desc) or _parse_follower_count(html[:80_000])

    result["followers"] = followers
    result["og_description"] = og_desc[:160] if og_desc else None

    lower = html.lower()
    if any(x in lower for x in ("login", "sign in", "anmelden")) and followers is None:
        result["status"] = "login_wall"
        result["note"] = "Profil hinter Login — Metriken nicht lesbar"
    elif followers is None:
        result["status"] = "active_unknown"
        result["note"] = "Profil erreichbar, Follower-Zahl nicht extrahierbar"
    elif followers >= 500:
        result["status"] = "established"
        result["note"] = f"Etabliertes Profil (~{followers:,} Follower)"
    elif followers >= 50:
        result["status"] = "moderate"
        result["note"] = f"Aktives Profil (~{followers:,} Follower)"
    elif followers >= 5:
        result["status"] = "small"
        result["note"] = f"Kleines Profil (~{followers:,} Follower)"
    else:
        result["status"] = "minimal"
        result["note"] = "Sehr wenige Follower — evtl. neues/leeres Profil"

    return result


def _calculate_score(profiles: list[dict[str, Any]]) -> tuple[int, list[dict], list[str]]:
    score = _MAX_SCORE
    breakdown: list[dict] = []
    warnings: list[str] = []

    def deduct(points: int, label: str) -> None:
        nonlocal score
        score = max(0, score - points)
        breakdown.append({"label": label, "points": 0, "max_points": points})

    reachable = [p for p in profiles if p.get("reachable")]
    broken = [p for p in profiles if p.get("status") in ("unreachable", "error", "empty")]
    minimal = [p for p in profiles if p.get("status") == "minimal"]
    established = [p for p in profiles if p.get("status") in ("established", "moderate")]

    if not profiles:
        return 0, [], []

    if len(broken) == len(profiles):
        deduct(6, "Alle Social-Media-Links ungültig oder leer")
        warnings.append("Social-Media-Links führen zu keinem erreichbaren Profil")
    elif broken:
        deduct(min(3, len(broken)), f"{len(broken)} defekte/r/leere Profile")
        warnings.append("Einige Social-Media-Links sind ungültig oder leer")

    if minimal and not established:
        deduct(min(2, len(minimal)), f"{len(minimal)} Profil(e) mit sehr wenigen Followern")
        warnings.append("Social-Media-Profile wirken neu oder unbefüllt")

    if established:
        breakdown.append({
            "label": f"{len(established)} etablierte Profile",
            "points": min(2, len(established)),
            "max_points": 2,
        })

    if len(reachable) >= 2 and not broken:
        score = min(_MAX_SCORE, score + 1)

    if score >= 7:
        pass
    elif score >= 4:
        pass

    return score, breakdown, warnings


class SocialMediaCheck(BaseCheck):
    name = "social_media"
    display_name = "Social Media"
    max_score = _MAX_SCORE
    tier = 2

    async def run(self, domain: str, url: str = "", **kwargs) -> CheckResult:
        target_url = url if url.startswith("http") else f"https://{domain}"

        try:
            render = await fetch_rendered_html(target_url, timeout_ms=20000)
        except Exception as e:
            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=CheckStatus.ERROR,
                score=0,
                max_score=self.max_score,
                summary=f"Social-Media-Check fehlgeschlagen: {str(e)[:120]}",
                details={},
            )

        if not render.get("success"):
            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=CheckStatus.ERROR,
                score=0,
                max_score=self.max_score,
                summary=render.get("error") or "Startseite nicht ladbar",
                details={},
            )

        html = render.get("html", "")
        base_url = render.get("final_url") or target_url
        links = _extract_social_links(html, base_url)

        if not links:
            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=CheckStatus.NA,
                score=0,
                max_score=self.max_score,
                summary="Keine Social-Media-Links auf der Startseite gefunden",
                details={"links_found": False, "profiles": []},
            )

        to_probe = list(links.items())[:_MAX_PROFILES_TO_PROBE]
        profiles = await asyncio.gather(
            *[_probe_profile(platform, profile_url) for platform, profile_url in to_probe]
        )
        profiles_list = list(profiles)

        score, score_breakdown, warnings = _calculate_score(profiles_list)

        if score >= 6:
            status = CheckStatus.PASSED
        elif score >= 3:
            status = CheckStatus.WARNING
        else:
            status = CheckStatus.FAILED

        platform_names = ", ".join(_PLATFORM_META[p]["label"] for p in links)
        summary = f"{len(links)} Kanäle verlinkt ({platform_names}) — Score {score}/{self.max_score}"
        if warnings:
            summary += f" — {warnings[0]}"

        return CheckResult(
            name=self.name,
            display_name=self.display_name,
            status=status,
            score=score,
            max_score=self.max_score,
            summary=summary,
            details={
                "links_found": True,
                "links": links,
                "profiles": profiles_list,
                "warning_flags": warnings,
                "score_breakdown": score_breakdown,
            },
        )
