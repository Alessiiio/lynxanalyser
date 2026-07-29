"""Swiss bank clearing / IID lookup from local SIX Bank Master snapshot."""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "swiss_bank_master.json"


@lru_cache(maxsize=1)
def load_bank_master() -> dict[str, Any]:
    if not _DATA_PATH.exists():
        logger.warning("Swiss bank master missing: %s — run scripts/fetch_swiss_bank_master.py", _DATA_PATH)
        return {"count": 0, "banks": [], "valid_on": None, "source": None}
    try:
        return json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error("Failed to load bank master: %s", e)
        return {"count": 0, "banks": [], "valid_on": None, "source": None}


def _digits(s: str) -> str:
    return "".join(c for c in str(s or "") if c.isdigit())


def normalize_clearing_query(raw: str) -> str:
    """Extract clearing/IID digits from raw input or CH IBAN."""
    s = str(raw or "").replace(" ", "").upper()
    alnum = "".join(c for c in s if c.isalnum())
    if alnum.startswith("CH") and len(alnum) >= 9:
        return _digits(alnum[4:9])
    return _digits(s)[:6]


def _score_bank(bank: dict[str, Any], q: str) -> int:
    """Higher = better match. Prefer HEADQUARTERS."""
    iid = str(bank.get("iid") or "")
    sic = _digits(bank.get("sic_iid") or "")
    iid5 = iid.zfill(5) if iid.isdigit() else ""
    score = 0
    if q == iid or q == iid5 or q == sic:
        score += 100
    elif sic.startswith(q) or iid5.startswith(q.zfill(min(5, len(q)))) or q.startswith(iid):
        score += 40
    elif q in sic or (iid5 and q in iid5):
        score += 20
    else:
        return 0
    if (bank.get("type") or "") == "HEADQUARTERS":
        score += 10
    if (bank.get("type") or "") == "QR_IID":
        score -= 5
    return score


def lookup_clearing(raw: str, *, limit: int = 8) -> dict[str, Any]:
    q = normalize_clearing_query(raw)
    meta = load_bank_master()
    banks = meta.get("banks") or []
    if not q or not banks:
        return {
            "query": q,
            "match": None,
            "candidates": [],
            "valid_on": meta.get("valid_on"),
            "source": meta.get("source"),
            "count": meta.get("count") or 0,
        }

    scored: list[tuple[int, dict[str, Any]]] = []
    for b in banks:
        sc = _score_bank(b, q)
        if sc > 0:
            scored.append((sc, b))
    scored.sort(key=lambda x: (-x[0], str(x[1].get("name") or "")))
    candidates = [
        {
            "iid": b.get("iid"),
            "sic_iid": b.get("sic_iid"),
            "type": b.get("type"),
            "name": b.get("name"),
            "town": b.get("town"),
            "bic": b.get("bic"),
            "score": sc,
        }
        for sc, b in scored[:limit]
    ]
    best = candidates[0] if candidates else None
    return {
        "query": q,
        "match": best,
        "candidates": candidates,
        "valid_on": meta.get("valid_on"),
        "source": meta.get("source"),
        "count": meta.get("count") or 0,
    }


def search_banks(q: str, *, limit: int = 15) -> dict[str, Any]:
    meta = load_bank_master()
    banks = meta.get("banks") or []
    needle = str(q or "").strip().lower()
    if len(needle) < 2:
        return {"results": [], "valid_on": meta.get("valid_on"), "count": meta.get("count") or 0}
    digits = _digits(needle)
    results = []
    for b in banks:
        name = (b.get("name") or "").lower()
        town = (b.get("town") or "").lower()
        bic = (b.get("bic") or "").lower()
        sic = _digits(b.get("sic_iid") or "")
        iid = str(b.get("iid") or "")
        hit = needle in name or needle in town or needle in bic
        if digits and (digits in sic or digits == iid or digits == iid.zfill(5)):
            hit = True
        if not hit:
            continue
        if (b.get("type") or "") == "QR_IID":
            continue
        results.append({
            "iid": b.get("iid"),
            "sic_iid": b.get("sic_iid"),
            "type": b.get("type"),
            "name": b.get("name"),
            "town": b.get("town"),
            "bic": b.get("bic"),
        })
        if len(results) >= limit:
            break
    return {"results": results, "valid_on": meta.get("valid_on"), "count": meta.get("count") or 0}
