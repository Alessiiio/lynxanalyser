"""Cross-company person lookup via ZefixREST shab/search.

Scans a cantonal SHAB index once and matches many persons in the same pass.
"""

from __future__ import annotations

import asyncio
import calendar
import datetime as dt
import hashlib
import json
import logging
import os
import re
import time
from typing import Any

import config
from app.checks.zefix_check import _format_uid
from app.hr_network.shab_parser import iter_named_persons_in_message
from app.hr_network.zefix_rest import zefix_rest_post

logger = logging.getLogger(__name__)

# Canton → registryOfCommerceId (ZefixREST registryOffices).
# Source: ZefixREST /community.json (registryOfficeId per Gemeinde), Jul 2026.
# Old IDs (AG=1, GE=6600, …) now 404 and silently produced empty person searches.
_CANTON_REGISTRY: dict[str, int] = {
    "AG": 400, "AI": 310, "AR": 300, "BE": 36, "BL": 280, "BS": 270,
    "FR": 217, "GE": 660, "GL": 160, "GR": 350, "JU": 670, "LU": 100,
    "NE": 645, "NW": 150, "OW": 140, "SG": 320, "SH": 290, "SO": 241,
    "SZ": 130, "TG": 440, "TI": 501, "UR": 120, "VD": 550, "VS": 600,
    "ZG": 170, "ZH": 20,
}

# Extra registry offices in the same canton (VS has multiple offices).
_CANTON_REGISTRY_EXTRA: dict[str, list[int]] = {
    "VS": [621, 626],
}

_SHAB_CACHE_TTL_SEC = 24 * 3600
_SHAB_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(getattr(config, "DATABASE_PATH", "./fraud_checks.db"))),
    "data",
    "shab_month_cache",
)


def parse_person_query(name: str) -> dict[str, str | list[str]]:
    raw = (name or "").strip()
    if not raw:
        raise ValueError("Personenname angeben")

    last = ""
    first_parts: list[str] = []
    if "," in raw:
        last, _, rest = raw.partition(",")
        last = last.strip()
        first_parts = [p for p in re.split(r"\s+", rest.strip()) if p]
    else:
        parts = [p for p in re.split(r"\s+", raw) if p]
        if len(parts) >= 2:
            last = parts[-1]
            first_parts = parts[:-1]
        elif parts:
            last = parts[0]

    return {
        "raw": raw,
        "last_name": last,
        "first_parts": first_parts,
        "tokens": [t for t in [last, *first_parts] if t],
    }


def _person_label_matches(label: str, query: dict[str, Any]) -> bool:
    """
    Strict match against Swiss HR name form «Nachname, Vorname …».

    Requires the surname at the start of the label and the first given name as
    the first token after the comma. Avoids false positives where Nachname and
    Vorname appear in different person entries of the same SHAB text (e.g. banks).
    """
    label = (label or "").strip()
    if not label or "," not in label:
        return False

    last = str(query.get("last_name") or "").strip().lower()
    first_parts = [p.lower() for p in (query.get("first_parts") or [])]
    if not last:
        return False

    lab_last, _, rest = label.partition(",")
    if lab_last.strip().lower() != last:
        return False

    given = [t.lower().rstrip(".") for t in re.split(r"\s+", rest.strip()) if t]
    if not first_parts:
        return True
    if not given:
        return False
    return given[0] == first_parts[0]


def _find_matching_persons(message: str, query: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        person
        for person in iter_named_persons_in_message(message)
        if _person_label_matches(person.get("name") or "", query)
    ]


def _message_matches_person(message: str, query: dict[str, Any]) -> bool:
    return bool(_find_matching_persons(message, query))


def _extract_person_snippet(message: str, query: dict[str, Any], limit: int = 200) -> str | None:
    for person in _find_matching_persons(message, query):
        raw = (person.get("raw_segment") or person.get("name") or "").strip()
        if raw:
            return raw[:limit]
    return None


def _month_bounds(year: int, month: int) -> tuple[str, str]:
    last_day = calendar.monthrange(year, month)[1]
    return (
        dt.date(year, month, 1).isoformat(),
        dt.date(year, month, last_day).isoformat(),
    )


def _iter_months(years_back: int) -> list[tuple[int, int]]:
    today = dt.date.today()
    try:
        start = today.replace(year=today.year - years_back)
    except ValueError:
        # Feb 29 → Feb 28
        start = today.replace(year=today.year - years_back, day=28)
    months: list[tuple[int, int]] = []
    year, month = start.year, start.month
    while (year, month) <= (today.year, today.month):
        months.append((year, month))
        month += 1
        if month > 12:
            month = 1
            year += 1
    return list(reversed(months))


def _cache_path(payload: dict[str, Any]) -> str:
    key = hashlib.sha1(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return os.path.join(_SHAB_CACHE_DIR, f"{key}.json")


def _zefix_rest_post_cached(payload: dict[str, Any]) -> dict:
    """Cache raw SHAB month pages for 24h so re-analyses stay fast."""
    path = _cache_path(payload)
    try:
        if os.path.isfile(path) and time.time() - os.path.getmtime(path) < _SHAB_CACHE_TTL_SEC:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    except (OSError, json.JSONDecodeError):
        pass

    data = zefix_rest_post("/shab/search.json", payload)
    try:
        os.makedirs(_SHAB_CACHE_DIR, exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        pass
    return data


def _hit_from_item(
    item: dict[str, Any],
    matched_pub: dict[str, Any],
    matched_message: str,
    query: dict[str, Any],
    matched_person: dict[str, Any],
) -> dict[str, Any]:
    uid_raw = str(item.get("uid") or "")
    return {
        "name": item.get("name"),
        "uid": item.get("uidFormatted") or (_format_uid(uid_raw) if uid_raw else None),
        "ehraid": item.get("ehraid"),
        "legal_seat": item.get("legalSeat"),
        "status": item.get("status"),
        "sogc_date": matched_pub.get("shabDate"),
        "role_hint": ", ".join(matched_person.get("roles") or []) or None,
        "person_name": matched_person.get("name"),
        "snippet": _extract_person_snippet(matched_message, query),
    }


def _scan_shab_month_for_queries(
    year: int,
    month: int,
    queries: list[dict[str, Any]],
    *,
    registry_offices: list[int] | None,
    exclude_digits_by_qid: dict[str, str],
) -> dict[str, list[dict[str, Any]]]:
    """Scan one month once; return hits keyed by query id (query['raw'])."""
    pub_start, pub_end = _month_bounds(year, month)
    offset = 0
    hits: dict[str, list[dict[str, Any]]] = {str(q["raw"]): [] for q in queries}
    seen: dict[str, set[str]] = {str(q["raw"]): set() for q in queries}
    last_needles = {
        str(q["raw"]): str(q.get("last_name") or "").lower()
        for q in queries
        if q.get("last_name")
    }
    if not last_needles:
        return hits

    while True:
        payload: dict[str, Any] = {
            "maxEntries": 5000,
            "offset": offset,
            "publicationDate": pub_start,
            "publicationDateEnd": pub_end,
        }
        if registry_offices:
            payload["registryOffices"] = registry_offices

        data = _zefix_rest_post_cached(payload)
        for item in data.get("list") or []:
            if not isinstance(item, dict):
                continue
            uid_raw = str(item.get("uid") or "")
            uid_digits = re.sub(r"\D", "", uid_raw)
            pubs = item.get("shabPub") or []
            if not pubs:
                continue

            # Cheap prefilter: only dig into messages that contain a surname.
            blob = " ".join(
                (pub.get("message") or "") if isinstance(pub, dict) else ""
                for pub in pubs
            ).lower()
            candidate_qids = [qid for qid, needle in last_needles.items() if needle in blob]
            if not candidate_qids:
                continue

            qmap = {str(q["raw"]): q for q in queries}
            for qid in candidate_qids:
                if exclude_digits_by_qid.get(qid) and uid_digits == exclude_digits_by_qid[qid]:
                    continue
                query = qmap[qid]
                matched_pub = None
                matched_message = ""
                matched_person = None
                for pub in pubs:
                    if not isinstance(pub, dict):
                        continue
                    message = pub.get("message") or ""
                    persons = _find_matching_persons(message, query)
                    if persons:
                        matched_pub = pub
                        matched_message = message
                        matched_person = persons[0]
                        break
                if not matched_pub or not matched_person:
                    continue

                key = uid_digits or str(item.get("ehraid") or item.get("name"))
                if key in seen[qid]:
                    continue
                seen[qid].add(key)
                hits[qid].append(
                    _hit_from_item(item, matched_pub, matched_message, query, matched_person)
                )

        if not data.get("hasMoreResults"):
            break
        offset = data.get("maxOffset", offset + 5000)

    return hits


def _all_registry_office_ids() -> list[int]:
    ids: set[int] = set(_CANTON_REGISTRY.values())
    for extras in _CANTON_REGISTRY_EXTRA.values():
        ids.update(extras)
    return sorted(ids)


def _resolve_registry(
    registry_office_id: int | None,
    canton: str | None,
) -> list[int] | None:
    if registry_office_id:
        return [int(registry_office_id)]
    if canton:
        code = canton.strip().upper()
        primary = _CANTON_REGISTRY.get(code)
        if not primary:
            return None
        extras = _CANTON_REGISTRY_EXTRA.get(code) or []
        return [primary, *extras]
    return None


def _merge_month_hits(
    hits_by_person: dict[str, dict[str, dict[str, Any]]],
    month_hits: dict[str, list[dict[str, Any]]],
) -> None:
    for qid, items in month_hits.items():
        bucket = hits_by_person.setdefault(qid, {})
        for hit in items:
            uid_key = re.sub(r"\D", "", str(hit.get("uid") or ""))
            key = uid_key or str(hit.get("ehraid") or hit.get("name"))
            prev = bucket.get(key)
            if not prev or (hit.get("sogc_date") or "") > (prev.get("sogc_date") or ""):
                bucket[key] = hit


async def search_persons_batch(
    person_names: list[str],
    *,
    exclude_uids: dict[str, str] | None = None,
    registry_office_id: int | None = None,
    canton: str | None = None,
    all_cantons: bool = False,
    years_back: int = 12,
    max_seconds: float = 75.0,
    deep: bool = False,
) -> dict[str, Any]:
    """
    Find companies mentioning any of the given persons in SHAB text.

    Prefer a cantonal registry filter (fast). For cross-canton coverage set
    ``all_cantons=True`` (scans every valid registry office — not one huge
    unfiltered nationwide dump, which overruns timeouts).
    """
    exclude_uids = exclude_uids or {}
    queries: list[dict[str, Any]] = []
    seen_raw: set[str] = set()
    for name in person_names:
        try:
            q = parse_person_query(name)
        except ValueError:
            continue
        raw = str(q["raw"])
        if raw in seen_raw:
            continue
        seen_raw.add(raw)
        queries.append(q)

    if not queries:
        return {
            "by_person": {},
            "match_count": 0,
            "scanned_months": 0,
            "total_months": 0,
            "search_complete": True,
            "elapsed_seconds": 0,
            "years_back": years_back,
            "method": "zefix_rest_shab_search_batch",
            "note": "Keine gültigen Personennamen",
        }

    if deep:
        years_back = max(years_back, 20)
        max_seconds = max(max_seconds, 120.0)

    if all_cantons:
        registry_offices = _all_registry_office_ids()
        scope = "ganze Schweiz (alle Kantone)"
        # One month at a time; within the month registries are chunked.
        month_batch_size = 1
        registry_chunk = 8
    else:
        registry_offices = _resolve_registry(registry_office_id, canton)
        if registry_offices:
            scope = (
                f"Handelsregister {registry_offices[0]}"
                if len(registry_offices) == 1
                else f"Handelsregister {', '.join(str(x) for x in registry_offices)}"
            )
            # Small batches so max_seconds is honored (large gather overruns badly).
            month_batch_size = 2
            registry_chunk = 0
        else:
            # Unfiltered nationwide dump — slow; keep batches tiny so max_seconds holds.
            scope = "ganze Schweiz"
            month_batch_size = 1
            registry_chunk = 0

    exclude_digits_by_qid = {
        str(q["raw"]): re.sub(r"\D", "", exclude_uids.get(str(q["raw"]), "") or "")
        for q in queries
    }

    months = _iter_months(years_back)
    started = time.monotonic()
    hits_by_person: dict[str, dict[str, dict[str, Any]]] = {
        str(q["raw"]): {} for q in queries
    }
    scanned_months = 0
    complete = True

    for batch_start in range(0, len(months), month_batch_size):
        if time.monotonic() - started >= max_seconds:
            complete = False
            break

        batch = months[batch_start : batch_start + month_batch_size]

        if all_cantons and registry_offices:
            for year, month in batch:
                if time.monotonic() - started >= max_seconds:
                    complete = False
                    break
                month_ok = True
                for chunk_start in range(0, len(registry_offices), registry_chunk):
                    if time.monotonic() - started >= max_seconds:
                        complete = False
                        month_ok = False
                        break
                    chunk = registry_offices[chunk_start : chunk_start + registry_chunk]
                    results = await asyncio.gather(
                        *[
                            asyncio.to_thread(
                                _scan_shab_month_for_queries,
                                year,
                                month,
                                queries,
                                registry_offices=[office_id],
                                exclude_digits_by_qid=exclude_digits_by_qid,
                            )
                            for office_id in chunk
                        ],
                        return_exceptions=True,
                    )
                    for result in results:
                        if isinstance(result, Exception):
                            logger.warning(
                                "SHAB month %04d-%02d registry scan failed: %s",
                                year,
                                month,
                                result,
                            )
                            continue
                        _merge_month_hits(hits_by_person, result)
                scanned_months += 1
                if not month_ok:
                    break
            if not complete:
                break
            continue

        results = await asyncio.gather(
            *[
                asyncio.to_thread(
                    _scan_shab_month_for_queries,
                    year,
                    month,
                    queries,
                    registry_offices=registry_offices,
                    exclude_digits_by_qid=exclude_digits_by_qid,
                )
                for year, month in batch
            ],
            return_exceptions=True,
        )

        for result in results:
            scanned_months += 1
            if isinstance(result, Exception):
                logger.warning("SHAB month scan failed: %s", result)
                continue
            _merge_month_hits(hits_by_person, result)

        if time.monotonic() - started >= max_seconds and batch_start + month_batch_size < len(months):
            complete = False
            break

    elapsed = round(time.monotonic() - started, 2)
    by_person: dict[str, Any] = {}
    total_matches = 0
    for q in queries:
        qid = str(q["raw"])
        hits = sorted(
            hits_by_person.get(qid, {}).values(),
            key=lambda h: h.get("sogc_date") or "",
            reverse=True,
        )
        total_matches += len(hits)
        by_person[qid] = {
            "person_query": qid,
            "matches": hits,
            "match_count": len(hits),
        }

    return {
        "by_person": by_person,
        "match_count": total_matches,
        "scanned_months": scanned_months,
        "total_months": len(months),
        "search_complete": complete,
        "deep": deep,
        "years_back": years_back,
        "registry_scope": scope,
        "all_cantons": all_cantons,
        "elapsed_seconds": elapsed,
        "method": "zefix_rest_shab_search_batch",
        "note": (
            None
            if complete
            else (
                f"Zeitlimit — nur {scanned_months}/{len(months)} Monate "
                f"({years_back} J. Ziel). Ältere Übernahmen können fehlen."
            )
        ),
    }


async def search_person_in_sogc(
    person_name: str,
    *,
    exclude_uid: str | None = None,
    registry_office_id: int | None = None,
    canton: str | None = None,
    all_cantons: bool = False,
    years_back: int = 12,
    max_seconds: float = 75.0,
    deep: bool = False,
) -> dict[str, Any]:
    """Find companies mentioning a person in SHAB text (single-person wrapper)."""
    query = parse_person_query(person_name)
    batch = await search_persons_batch(
        [person_name],
        exclude_uids={str(query["raw"]): exclude_uid or ""},
        registry_office_id=registry_office_id,
        canton=canton,
        all_cantons=all_cantons,
        years_back=years_back,
        max_seconds=max_seconds,
        deep=deep,
    )
    person_block = batch.get("by_person", {}).get(str(query["raw"]), {})
    return {
        "person_query": query["raw"],
        "matches": person_block.get("matches") or [],
        "match_count": person_block.get("match_count") or 0,
        "scanned_months": batch.get("scanned_months"),
        "total_months": batch.get("total_months"),
        "search_complete": batch.get("search_complete"),
        "deep": deep,
        "years_back": batch.get("years_back"),
        "registry_scope": batch.get("registry_scope"),
        "all_cantons": batch.get("all_cantons"),
        "elapsed_seconds": batch.get("elapsed_seconds"),
        "method": batch.get("method"),
        "note": batch.get("note"),
    }
