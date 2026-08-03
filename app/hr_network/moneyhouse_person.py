"""Person→Mandate lookup via Moneyhouse search (alternative to SHAB month dumps).

IMPORTANT: This module is ONLY for person mandate discovery.
Company identity (UID, ehraid, status, canton) must always be resolved via Zefix.
Do not use Moneyhouse for firm search or company analysis.
"""

from __future__ import annotations

import logging
import re
import urllib.parse
import urllib.request
from typing import Any

import config
from app.hr_network.person_search import parse_person_query

logger = logging.getLogger(__name__)

_MH_SEARCH = "https://www.moneyhouse.ch/jx/search"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; lynx-person-mandate/1.0)",
    "Accept": "application/json",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://www.moneyhouse.ch/de/search",
}


def moneyhouse_person_search_enabled() -> bool:
    return bool(getattr(config, "MONEYHOUSE_PERSON_SEARCH", True))


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _query_variants(display_name: str) -> list[str]:
    """Moneyhouse matches better on «Vorname Nachname» than «Nachname, Vorname»."""
    q = parse_person_query(display_name)
    last = str(q.get("last_name") or "").strip()
    first_parts = [str(p).strip() for p in (q.get("first_parts") or []) if str(p).strip()]
    first = " ".join(first_parts)
    variants: list[str] = []
    if first and last:
        variants.append(f"{first} {last}")
        variants.append(f"{last} {first}")
        variants.append(f"{last}, {first}")
        if first_parts:
            variants.append(f"{first_parts[0]} {last}")
    raw = str(q.get("raw") or display_name).strip()
    if raw:
        variants.append(raw)
    if last:
        variants.append(last)
    # de-dupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for v in variants:
        key = _norm(v)
        if key and key not in seen:
            seen.add(key)
            out.append(v)
    return out


def _person_score(hit: dict[str, Any], query: dict[str, Any], residence: str | None) -> float:
    cur = hit.get("currentName") or {}
    hit_last = _norm(cur.get("lastName"))
    hit_first = _norm(cur.get("firstName"))
    want_last = _norm(str(query.get("last_name") or ""))
    want_firsts = [_norm(p) for p in (query.get("first_parts") or [])]
    if not want_last or hit_last != want_last:
        return 0.0
    score = 2.0
    if want_firsts:
        if hit_first == " ".join(want_firsts):
            score += 3.0
        elif want_firsts[0] and (
            hit_first.startswith(want_firsts[0]) or want_firsts[0] in hit_first.split()
        ):
            score += 2.0
        else:
            return 0.0
    if residence:
        city = _norm((hit.get("currentDomicile") or {}).get("city"))
        res = _norm(residence)
        if city and (city in res or res in city):
            score += 1.5
    if hit.get("activeMandate"):
        score += 0.25
    return score


def _mh_get_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=25) as resp:
        import json

        return json.loads(resp.read().decode())


def _search_persons_once(q: str, *, limit: int = 20) -> list[dict[str, Any]]:
    url = (
        _MH_SEARCH
        + "?"
        + urllib.parse.urlencode(
            {
                "q": q,
                "tab": "persons",
                "page": 0,
                "limit": limit,
                "fuzzySearch": 1,
                "status": 1,
            }
        )
    )
    data = _mh_get_json(url)
    entities = ((data.get("data") or {}).get("entities") or {})
    rows = entities.get("data") or []
    return [r for r in rows if isinstance(r, dict)]


def search_person_mandates(
    display_name: str,
    *,
    residence: str | None = None,
) -> dict[str, Any]:
    """
    Find HR mandates for a person via Moneyhouse person search.

    Returns company *names* (and Moneyhouse URIs). Callers must resolve firms via Zefix.
    """
    if not moneyhouse_person_search_enabled():
        return {
            "enabled": False,
            "matched_person": None,
            "companies": [],
            "candidates": 0,
            "method": "moneyhouse_person_search",
            "note": "Moneyhouse-Personensuche deaktiviert",
        }

    try:
        query = parse_person_query(display_name)
    except ValueError as e:
        return {
            "enabled": True,
            "matched_person": None,
            "companies": [],
            "candidates": 0,
            "method": "moneyhouse_person_search",
            "error": str(e),
        }

    best: dict[str, Any] | None = None
    best_score = 0.0
    seen_ids: set[str] = set()
    candidates = 0

    for variant in _query_variants(display_name):
        try:
            rows = _search_persons_once(variant)
        except Exception as e:
            logger.warning("Moneyhouse person search failed for %r: %s", variant, e)
            continue
        for row in rows:
            pid = str(row.get("id") or row.get("uri") or "")
            if pid and pid in seen_ids:
                continue
            if pid:
                seen_ids.add(pid)
            candidates += 1
            score = _person_score(row, query, residence)
            if score > best_score:
                best_score = score
                best = row

    if not best or best_score < 2.0:
        return {
            "enabled": True,
            "matched_person": None,
            "companies": [],
            "candidates": candidates,
            "method": "moneyhouse_person_search",
            "note": "Keine passende Person in Moneyhouse",
        }

    cur = best.get("currentName") or {}
    domicile = best.get("currentDomicile") or {}
    companies: list[dict[str, Any]] = []
    for rel in best.get("relatedCompanies") or []:
        if not isinstance(rel, dict):
            continue
        name = (rel.get("name") or "").strip()
        if not name:
            continue
        companies.append(
            {
                "name": name,
                "moneyhouse_uri": rel.get("uri"),
                "from": rel.get("from"),
                "source": "moneyhouse",
            }
        )

    return {
        "enabled": True,
        "matched_person": {
            "name": cur.get("name") or display_name,
            "first_name": cur.get("firstName"),
            "last_name": cur.get("lastName"),
            "uri": best.get("uri"),
            "residence": domicile.get("city"),
            "active_mandate": bool(best.get("activeMandate")),
            "score": best_score,
        },
        "companies": companies,
        "candidates": candidates,
        "method": "moneyhouse_person_search",
        "note": None,
    }
