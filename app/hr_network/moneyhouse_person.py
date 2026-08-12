"""Person→Mandate fill-in via Moneyhouse search (secondary to Zefix/SHAB).

IMPORTANT: This module is ONLY for person mandate *enrichment* after Zefix/SHAB.
Company identity (UID, ehraid, status, canton) must always be resolved via Zefix.
Do not use Moneyhouse for firm name search or company identity.
Moneyhouse must never be the primary authority for firm analysis or L3/L4 expansion.

Person disambiguation: when the seed firm (Zefix/SHAB organ source) is already
known, optionally require/prefer that firm in Moneyhouse ``relatedCompanies``
before accepting a person profile. This is a soft gate (boost + selection), not
a hard reject — MH listings can lag Zefix. Soft-gate is disambiguation only,
never primary discovery of persons in place of Zefix.
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

_MH_BASE = "https://www.moneyhouse.ch"
_MH_SEARCH = f"{_MH_BASE}/jx/search"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; lynx-person-mandate/1.0)",
    "Accept": "application/json",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": f"{_MH_BASE}/de/search",
}

# Below this → no person match. Exact last name + partial first typically ≥ 4.
_MIN_NAME_SCORE = 2.0
# Soft-accept without seed in profile only when name match is strong (full first name).
_SOFT_ACCEPT_MIN_SCORE = 4.0
# Boost when seed firm appears in relatedCompanies (disambiguates common names).
_SEED_MATCH_BOOST = 10.0

_LEGAL_FORM = re.compile(
    r"\b("
    r"gmbh|ag|sa|s\.a\.|sarl|sàrl|s\.à\.r\.l\.|"
    r"llc|ltd|kg|co\.?\s*kg|genossenschaft|stiftung|"
    r"einzelunternehmen|klg|kommanditgesellschaft"
    r")\b\.?",
    re.I,
)
_LIQUIDATION = re.compile(r"\s+in\s+liqui\w*", re.I)
_NON_ALNUM = re.compile(r"[^\w\s]", re.UNICODE)


def moneyhouse_person_search_enabled() -> bool:
    return bool(getattr(config, "MONEYHOUSE_PERSON_SEARCH", True))


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def uid_digits(uid: str | None) -> str:
    return re.sub(r"\D", "", uid or "")


def firm_core_name(name: str | None) -> str:
    """Normalize firm name for seed↔MH relatedCompany matching (liquidated OK)."""
    s = _norm(name)
    if not s:
        return ""
    s = _LIQUIDATION.sub("", s)
    s = _LEGAL_FORM.sub("", s)
    s = _NON_ALNUM.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def firm_names_match(a: str | None, b: str | None) -> bool:
    """True when two company names refer to the same firm (loose but safe)."""
    ca, cb = firm_core_name(a), firm_core_name(b)
    if not ca or not cb:
        return False
    if ca == cb:
        return True
    # Contained core (e.g. «fbp bau» vs «fbp bau zug») — require ≥4 chars for short sides.
    shorter, longer = (ca, cb) if len(ca) <= len(cb) else (cb, ca)
    if len(shorter) >= 4 and shorter in longer:
        return True
    return False


def firm_matches_seed(
    company_name: str | None,
    *,
    seed_name: str | None = None,
    seed_uid: str | None = None,
    company_uid: str | None = None,
) -> bool:
    """Match a Moneyhouse related company to the Zefix seed firm (name and/or UID)."""
    su = uid_digits(seed_uid)
    cu = uid_digits(company_uid)
    if su and cu and su == cu:
        return True
    if seed_name and company_name and firm_names_match(seed_name, company_name):
        return True
    return False


def _related_companies(hit: dict[str, Any]) -> list[dict[str, Any]]:
    companies: list[dict[str, Any]] = []
    for rel in hit.get("relatedCompanies") or []:
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
                "uid": rel.get("uid") or rel.get("companyUid"),
                "source": "moneyhouse",
            }
        )
    return companies


def hit_lists_seed_firm(
    hit: dict[str, Any],
    *,
    seed_name: str | None,
    seed_uid: str | None,
) -> bool:
    if not seed_name and not seed_uid:
        return False
    for c in _related_companies(hit):
        if firm_matches_seed(
            c.get("name"),
            seed_name=seed_name,
            seed_uid=seed_uid,
            company_uid=c.get("uid") if isinstance(c.get("uid"), str) else None,
        ):
            return True
    return False


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


def mh_person_key(hit: dict[str, Any] | None) -> str:
    """Stable Moneyhouse person id (id preferred, else uri)."""
    if not isinstance(hit, dict):
        return ""
    return str(hit.get("id") or hit.get("uri") or "").strip()


def mh_profile_url(uri: str | None) -> str | None:
    """Absolute Moneyhouse profile URL from MH ``uri``, or None if unknown.

    Prefer absolute URLs Moneyhouse already returns. Relative paths
    (e.g. ``/de/person/…``) are joined to the official host. Bare ids are
    not invented into paths.
    """
    u = (uri or "").strip()
    if not u:
        return None
    if u.startswith("https://") or u.startswith("http://"):
        parsed = urllib.parse.urlparse(u)
        host = (parsed.hostname or "").lower()
        if host not in ("www.moneyhouse.ch", "moneyhouse.ch"):
            return None
        path = parsed.path or "/"
        query = f"?{parsed.query}" if parsed.query else ""
        fragment = f"#{parsed.fragment}" if parsed.fragment else ""
        return f"{_MH_BASE}{path}{query}{fragment}"
    if u.startswith("/") and not u.startswith("//"):
        return f"{_MH_BASE}{u}"
    return None


def _hit_display_name(hit: dict[str, Any]) -> str:
    cur = hit.get("currentName") or {}
    name = (cur.get("name") or "").strip()
    if name:
        return name
    parts = [cur.get("firstName") or "", cur.get("lastName") or ""]
    return " ".join(p for p in parts if p).strip() or "?"


def choice_from_hit(
    hit: dict[str, Any],
    *,
    name_score: float = 0.0,
    seed_name: str | None = None,
    seed_uid: str | None = None,
    seed_listed: bool | None = None,
) -> dict[str, Any]:
    """Structured candidate for UI identity confirmation."""
    cur = hit.get("currentName") or {}
    domicile = hit.get("currentDomicile") or {}
    companies = _related_companies(hit)
    if seed_listed is None:
        seed_listed = hit_lists_seed_firm(hit, seed_name=seed_name, seed_uid=seed_uid)
    key = mh_person_key(hit)
    uri = hit.get("uri")
    return {
        "person_key": key,
        "uri": uri,
        "profile_url": mh_profile_url(str(uri) if uri is not None else None),
        "name": _hit_display_name(hit),
        "first_name": cur.get("firstName"),
        "last_name": cur.get("lastName"),
        "city": (domicile.get("city") or None),
        "active_mandate": bool(hit.get("activeMandate")),
        "name_score": round(float(name_score or 0.0), 2),
        "seed_listed": bool(seed_listed),
        "related_companies": [c.get("name") for c in companies[:8] if c.get("name")],
        "related_companies_count": len(companies),
    }


def _choices_from_scored(
    scored: list[tuple[float, float, bool, dict[str, Any]]],
    *,
    seed_name: str | None,
    seed_uid: str | None,
    limit: int = 6,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for _total, name_score, seed_ok, hit in scored[:limit]:
        out.append(
            choice_from_hit(
                hit,
                name_score=name_score,
                seed_name=seed_name,
                seed_uid=seed_uid,
                seed_listed=seed_ok,
            )
        )
    return out


def _firm_label(seed_name: str | None, seed_uid: str | None) -> str:
    return (seed_name or seed_uid or "die analysierte Firma").strip()


def human_identity_note(
    status: str,
    *,
    person_name: str | None = None,
    seed_name: str | None = None,
    seed_uid: str | None = None,
    viable_count: int = 0,
) -> str:
    """Plain-language German (no Seed/MH/Disambiguierung jargon)."""
    firm = _firm_label(seed_name, seed_uid)
    who = (person_name or "").strip() or "dieser Person"
    if status == "soft":
        return (
            f"Moneyhouse-Profil passt nur dem Namen nach; Firma «{firm}» steht dort "
            f"nicht. Übernahmen sind unsicher."
        )
    if status == "ambiguous" or (status == "none" and viable_count > 1):
        n = viable_count if viable_count > 0 else 2
        return (
            f"Mehrere Personen mit dem Namen {who} auf Moneyhouse — keiner führt die "
            f"analysierte Firma «{firm}». Bitte selbst zuordnen oder ignorieren."
            if n != 1
            else (
                f"Mehrere ähnliche Treffer zu {who} auf Moneyhouse — keiner führt die "
                f"analysierte Firma «{firm}». Bitte selbst zuordnen oder ignorieren."
            )
        )
    if status == "none":
        return f"Keine passende Person zu {who} auf Moneyhouse gefunden."
    if status == "forced":
        return f"Zuordnung für {who} manuell bestätigt."
    return ""


def select_person_hit(
    hits: list[dict[str, Any]],
    *,
    query: dict[str, Any],
    residence: str | None = None,
    seed_name: str | None = None,
    seed_uid: str | None = None,
    force_mh_person_key: str | None = None,
    display_name: str | None = None,
) -> dict[str, Any]:
    """
    Rank Moneyhouse person hits with optional seed-firm confirmation.

    Returns keys: matched_hit | None, name_score, total_score, seed_confirmed,
    identity_status (confirmed|soft|none|forced), note, identity_choices.
    When ``force_mh_person_key`` is set, that profile is locked (identity forced).
    """
    scored: list[tuple[float, float, bool, dict[str, Any]]] = []
    force_key = (force_mh_person_key or "").strip()
    for row in hits:
        name_score = _person_score(row, query, residence)
        # Forced picks bypass the name floor so users can lock a listed candidate.
        if name_score < _MIN_NAME_SCORE and not force_key:
            continue
        if name_score < _MIN_NAME_SCORE and force_key:
            # Still allow force when key matches even if re-score is low.
            key = mh_person_key(row)
            if key != force_key and str(row.get("uri") or "") != force_key:
                continue
            if name_score <= 0:
                name_score = _MIN_NAME_SCORE
        seed_ok = hit_lists_seed_firm(row, seed_name=seed_name, seed_uid=seed_uid)
        total = name_score + (_SEED_MATCH_BOOST if seed_ok else 0.0)
        scored.append((total, name_score, seed_ok, row))

    # Force path: match by id or uri among raw hits if score filter dropped it.
    if force_key:
        forced_hit: dict[str, Any] | None = None
        forced_name_score = 0.0
        for total, name_score, seed_ok, row in scored:
            key = mh_person_key(row)
            if key == force_key or str(row.get("uri") or "") == force_key:
                forced_hit = row
                forced_name_score = name_score
                break
        if forced_hit is None:
            for row in hits:
                key = mh_person_key(row)
                if key == force_key or str(row.get("uri") or "") == force_key:
                    forced_hit = row
                    forced_name_score = max(
                        _person_score(row, query, residence), _MIN_NAME_SCORE
                    )
                    break
        if forced_hit is not None:
            seed_ok = hit_lists_seed_firm(
                forced_hit, seed_name=seed_name, seed_uid=seed_uid
            )
            choices = _choices_from_scored(
                scored
                or [
                    (
                        forced_name_score + (_SEED_MATCH_BOOST if seed_ok else 0),
                        forced_name_score,
                        seed_ok,
                        forced_hit,
                    )
                ],
                seed_name=seed_name,
                seed_uid=seed_uid,
            )
            return {
                "matched_hit": forced_hit,
                "name_score": forced_name_score,
                "total_score": forced_name_score
                + (_SEED_MATCH_BOOST if seed_ok else 0.0),
                "seed_confirmed": seed_ok,
                "identity_status": "forced",
                "note": human_identity_note(
                    "forced",
                    person_name=display_name,
                    seed_name=seed_name,
                    seed_uid=seed_uid,
                ),
                "viable_count": max(len(scored), 1),
                "seed_confirmed_candidates": 1 if seed_ok else 0,
                "identity_choices": choices,
            }

    if not scored:
        return {
            "matched_hit": None,
            "name_score": 0.0,
            "total_score": 0.0,
            "seed_confirmed": False,
            "identity_status": "none",
            "note": human_identity_note(
                "none",
                person_name=display_name,
                seed_name=seed_name,
                seed_uid=seed_uid,
            ),
            "viable_count": 0,
            "identity_choices": [],
        }

    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    choices = _choices_from_scored(scored, seed_name=seed_name, seed_uid=seed_uid)
    with_seed = [t for t in scored if t[2]]
    if with_seed:
        total, name_score, _, hit = with_seed[0]
        return {
            "matched_hit": hit,
            "name_score": name_score,
            "total_score": total,
            "seed_confirmed": True,
            "identity_status": "confirmed",
            "note": None,
            "viable_count": len(scored),
            "seed_confirmed_candidates": len(with_seed),
            "identity_choices": choices,
        }

    # No seed in any MH profile — soft-accept only if unique strong name match.
    if len(scored) == 1 and scored[0][1] >= _SOFT_ACCEPT_MIN_SCORE:
        total, name_score, _, hit = scored[0]
        note = human_identity_note(
            "soft",
            person_name=display_name or _hit_display_name(hit),
            seed_name=seed_name,
            seed_uid=seed_uid,
        )
        return {
            "matched_hit": hit,
            "name_score": name_score,
            "total_score": total,
            "seed_confirmed": False,
            "identity_status": "soft",
            "note": note,
            "viable_count": 1,
            "seed_confirmed_candidates": 0,
            "identity_choices": choices,
        }

    # Ambiguous without seed gate → refuse MH identity (caller may use SHAB).
    note = human_identity_note(
        "ambiguous",
        person_name=display_name,
        seed_name=seed_name,
        seed_uid=seed_uid,
        viable_count=len(scored),
    )
    # Keep a technical detail line for collapsed UI (names list).
    top_names = [_hit_display_name(row) for *_, row in scored[:5]]
    tech = (
        f"Moneyhouse: {len(scored)} Namens-Kandidaten, keiner listet «{_firm_label(seed_name, seed_uid)}»."
    )
    if top_names:
        tech += " Kandidaten: " + ", ".join(top_names)
    return {
        "matched_hit": None,
        "name_score": scored[0][1],
        "total_score": scored[0][0],
        "seed_confirmed": False,
        "identity_status": "none",
        "note": note,
        "note_technical": tech,
        "viable_count": len(scored),
        "seed_confirmed_candidates": 0,
        "identity_choices": choices,
    }


def search_person_mandates(
    display_name: str,
    *,
    residence: str | None = None,
    seed_company: str | None = None,
    seed_uid: str | None = None,
    force_mh_person_key: str | None = None,
) -> dict[str, Any]:
    """
    Find HR mandates for a person via Moneyhouse person search.

    When ``seed_company`` / ``seed_uid`` are set (Zefix organ source firm), profiles
    that list that firm in ``relatedCompanies`` are preferred (identity confirm).
    Without a seed listing: soft-accept only a single strong name match; otherwise reject.
    ``force_mh_person_key`` locks a specific Moneyhouse person (id or uri).

    Returns company *names* (and Moneyhouse URIs). Callers must resolve firms via Zefix.
    """
    empty_base = {
        "enabled": True,
        "matched_person": None,
        "companies": [],
        "candidates": 0,
        "identity_status": "none",
        "seed_confirmed": False,
        "method": "moneyhouse_person_search",
        "identity_choices": [],
    }
    if not moneyhouse_person_search_enabled():
        return {
            **empty_base,
            "enabled": False,
            "note": "Moneyhouse-Personensuche deaktiviert",
        }

    try:
        query = parse_person_query(display_name)
    except ValueError as e:
        return {**empty_base, "error": str(e)}

    seen_ids: set[str] = set()
    all_hits: list[dict[str, Any]] = []
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
            all_hits.append(row)

    selection = select_person_hit(
        all_hits,
        query=query,
        residence=residence,
        seed_name=seed_company,
        seed_uid=seed_uid,
        force_mh_person_key=force_mh_person_key,
        display_name=display_name,
    )
    choices = list(selection.get("identity_choices") or [])
    best = selection.get("matched_hit")
    if not best:
        return {
            "enabled": True,
            "matched_person": None,
            "companies": [],
            "candidates": candidates,
            "identity_status": selection.get("identity_status") or "none",
            "seed_confirmed": False,
            "method": "moneyhouse_person_search",
            "note": selection.get("note")
            or human_identity_note(
                "none",
                person_name=display_name,
                seed_name=seed_company,
                seed_uid=seed_uid,
            ),
            "note_technical": selection.get("note_technical"),
            "viable_count": selection.get("viable_count") or 0,
            "identity_choices": choices,
            "seed_company": seed_company,
            "seed_uid": seed_uid,
        }

    cur = best.get("currentName") or {}
    domicile = best.get("currentDomicile") or {}
    companies = _related_companies(best)
    identity_status = selection.get("identity_status") or "none"
    seed_confirmed = bool(selection.get("seed_confirmed"))
    person_key = mh_person_key(best)

    return {
        "enabled": True,
        "matched_person": {
            "name": cur.get("name") or display_name,
            "first_name": cur.get("firstName"),
            "last_name": cur.get("lastName"),
            "uri": best.get("uri"),
            "profile_url": mh_profile_url(
                str(best.get("uri")) if best.get("uri") is not None else None
            ),
            "person_key": person_key,
            "residence": domicile.get("city"),
            "active_mandate": bool(best.get("activeMandate")),
            "score": selection.get("total_score") or selection.get("name_score"),
            "name_score": selection.get("name_score"),
            "seed_confirmed": seed_confirmed,
            "identity_status": identity_status,
        },
        "companies": companies,
        "candidates": candidates,
        "identity_status": identity_status,
        "seed_confirmed": seed_confirmed,
        "method": "moneyhouse_person_search",
        "note": selection.get("note"),
        "note_technical": selection.get("note_technical"),
        "viable_count": selection.get("viable_count"),
        "identity_choices": choices,
        "seed_company": seed_company,
        "seed_uid": seed_uid,
    }
