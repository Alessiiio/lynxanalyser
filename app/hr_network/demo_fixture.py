"""Offline demo firm fixture (no Zefix / Moneyhouse).

Load DEMO-FRAUD GmbH / CHE-000.000.001 for UI demos and tests.
Fixture lives under app/hr_network/fixtures/ (not docker volume /app/data).
"""

from __future__ import annotations

import copy
import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.hr_network.fraud_network import LEVEL_LABELS

logger = logging.getLogger(__name__)

# Packaged with the Python module so Docker's lynx_data:/app/data volume cannot hide it.
_PACKAGE_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "demo_fraud_firm.json"
# Legacy/local fallback (repo checkout before move, or manual copy into data/).
_DATA_FIXTURE = Path(__file__).resolve().parents[2] / "data" / "demo_fraud_firm.json"

_UID_DIGITS = re.compile(r"\D+")
_ALNUM = re.compile(r"[0-9A-Za-zÄÖÜäöüÀ-ÿ]")


class DemoFixtureError(RuntimeError):
    """Fixture missing or invalid — callers should map to HTTP 503 JSON."""


def resolve_fixture_path() -> Path:
    """Prefer packaged fixture; fall back to data/ for older checkouts."""
    if _PACKAGE_FIXTURE.is_file():
        return _PACKAGE_FIXTURE
    if _DATA_FIXTURE.is_file():
        return _DATA_FIXTURE
    raise DemoFixtureError(
        f"Demo-Fixture fehlt (erwartet {_PACKAGE_FIXTURE} oder {_DATA_FIXTURE}). "
        "Bei Docker: Image neu bauen — die Datei liegt unter app/, nicht im Volume /app/data."
    )


# Tests may reassign this; resolve_fixture_path() is used when unset/missing.
_FIXTURE_PATH: Path | None = None


def _norm_uid(uid: str | None) -> str:
    if not uid:
        return ""
    return _UID_DIGITS.sub("", str(uid)).lstrip("0") or "0"


def _norm_name(name: str | None) -> str:
    if not name:
        return ""
    return re.sub(r"\s+", " ", str(name).strip().lower())


def usable_company_query(*, company: str | None = None, uid: str | None = None) -> bool:
    """False for empty / punctuation-only names like «?» that must not hit Zefix."""
    if (uid or "").strip():
        return True
    name = (company or "").strip()
    if not name:
        return False
    return len(_ALNUM.findall(name)) >= 2


@lru_cache(maxsize=1)
def _raw_fixture() -> dict[str, Any]:
    path = _FIXTURE_PATH if (_FIXTURE_PATH and Path(_FIXTURE_PATH).is_file()) else resolve_fixture_path()
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise DemoFixtureError(str(e)) from e
    except OSError as e:
        raise DemoFixtureError(f"Demo-Fixture nicht lesbar: {e}") from e
    except json.JSONDecodeError as e:
        raise DemoFixtureError(f"Demo-Fixture ist kein gültiges JSON: {e}") from e
    if not isinstance(data, dict) or not data.get("demo_only"):
        raise DemoFixtureError(f"Ungültige Demo-Fixture bei {path}")
    return data


def reload_fixture() -> None:
    """Clear cached fixture (tests)."""
    _raw_fixture.cache_clear()


def demo_meta() -> dict[str, Any]:
    raw = _raw_fixture()
    match = raw.get("match") or {}
    return {
        "demo_only": True,
        "name": (raw.get("company") or {}).get("name") or "DEMO-FRAUD GmbH",
        "uid": (raw.get("company") or {}).get("uid") or "CHE-000.000.001",
        "demo_keys": list(match.get("demo_keys") or ["fraud"]),
    }


def is_demo_request(
    *,
    name: str | None = None,
    uid: str | None = None,
    demo: str | None = None,
) -> bool:
    """True when name/uid/demo key refers to the seeded offline firm."""
    raw = _raw_fixture()
    match = raw.get("match") or {}
    demo_key = (demo or "").strip().lower().replace("_", "-")
    if demo_key and demo_key in {str(k).lower() for k in (match.get("demo_keys") or [])}:
        return True

    uid_n = _norm_uid(uid)
    if uid_n:
        for u in match.get("uids") or []:
            if _norm_uid(u) == uid_n:
                return True

    name_n = _norm_name(name)
    if not name_n:
        return False
    for n in match.get("names") or []:
        if _norm_name(n) == name_n:
            return True
    # Typed shorthand still OK: «DEMO-FRAUD», «demo fraud gmbh»
    compact = re.sub(r"[^a-z0-9]+", "", name_n)
    if compact in {"demofraud", "demofraudgmbh"}:
        return True
    return False


def demo_search_hits(q: str, *, limit: int = 12) -> list[dict[str, Any]]:
    """Autocomplete hits when query looks like the demo firm."""
    needle = _norm_name(q)
    if len(needle) < 2:
        return []
    raw = _raw_fixture()
    match = raw.get("match") or {}
    needles = [_norm_name(n) for n in (match.get("search_needles") or [])]
    names = [_norm_name(n) for n in (match.get("names") or [])]
    uid_n = _norm_uid(q)
    hit = False
    if uid_n and any(_norm_uid(u) == uid_n or uid_n in _norm_uid(u) for u in (match.get("uids") or [])):
        hit = True
    if not hit:
        for n in needles + names:
            if needle in n or n in needle:
                hit = True
                break
    if not hit and "demo" in needle and "fraud" in needle:
        hit = True
    if not hit:
        return []
    preview = copy.deepcopy(raw.get("search_preview") or {})
    preview["demo_only"] = True
    return [preview][:limit]


def _filter_mandates(mandates: list[dict] | None, level: int, seed_uid: str) -> list[dict]:
    items = list(mandates or [])
    seed_digits = _norm_uid(seed_uid)
    if level < 3:
        return [m for m in items if _norm_uid(m.get("uid")) == seed_digits]
    if level < 4:
        # L3 expands current organs only — keep non-seed mandates that appear at min_level<=3
        # via company nodes; for simplicity keep all for current, seed-only for former handled
        # by caller.
        return items
    return items


def _persons_for_level(
    persons: list[dict[str, Any]],
    level: int,
    seed_uid: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in persons:
        status = (p.get("status") or "").lower()
        if status == "former" and level < 2:
            continue
        entry = copy.deepcopy(p)
        mandates = list(entry.get("mandates") or [])
        if level < 3:
            entry["mandates"] = _filter_mandates(mandates, level, seed_uid)
        elif level < 4 and status == "former":
            # Former mandate expansion starts at L4
            entry["mandates"] = _filter_mandates(mandates, 2, seed_uid)
        else:
            entry["mandates"] = mandates
        out.append(entry)
    return out


def _graph_for_level(raw: dict[str, Any], level: int) -> tuple[list[dict], list[dict]]:
    level = max(1, min(5, int(level)))
    nodes = [
        copy.deepcopy(n)
        for n in (raw.get("nodes") or [])
        if int(n.get("min_level") or 1) <= level
    ]
    node_ids = {n["id"] for n in nodes if n.get("id")}
    edges = []
    for e in raw.get("edges") or []:
        if int(e.get("min_level") or 1) > level:
            continue
        if e.get("from") not in node_ids or e.get("to") not in node_ids:
            continue
        edges.append(copy.deepcopy(e))
    return nodes, edges


def _person_search_stats(level: int) -> dict[str, Any]:
    if level < 3:
        return {
            "searched": 0,
            "matches": 0,
            "skipped": 0,
            "years_back": 0,
            "elapsed_seconds": 0,
            "search_complete": True,
            "note": "Demo-Fixture (keine Personensuche)",
            "moneyhouse_enabled": False,
            "moneyhouse_persons": 0,
            "moneyhouse_matches": 0,
            "moneyhouse_seed_confirmed": 0,
            "moneyhouse_identity_soft": 0,
            "moneyhouse_identity_rejected": 0,
            "shab_matches": 0,
            "method": "demo-fixture",
            "identity_warnings": [],
            "identity_choices": [],
        }
    # Pre-baked L3+ looks like a completed offline expansion
    matches = 2 if level == 3 else (4 if level == 4 else 6)
    return {
        "searched": 3 if level >= 4 else 1,
        "matches": matches,
        "skipped": 0,
        "years_back": 12,
        "elapsed_seconds": 0.1,
        "search_complete": True,
        "note": "Demo-Fixture — vorgefertigtes Netzwerk ohne Zefix/Moneyhouse",
        "moneyhouse_enabled": False,
        "moneyhouse_persons": 0,
        "moneyhouse_matches": 0,
        "moneyhouse_seed_confirmed": 0,
        "moneyhouse_identity_soft": 0,
        "moneyhouse_identity_rejected": 0,
        "shab_matches": matches,
        "method": "demo-fixture",
        "identity_warnings": [],
        "identity_choices": [],
    }


def build_demo_fraud_network(level: int = 2) -> dict[str, Any]:
    """Fraud-network shaped payload for the requested depth (no external APIs)."""
    raw = _raw_fixture()
    level = max(1, min(5, int(level)))
    nodes, edges = _graph_for_level(raw, level)
    seed_uid = (raw.get("company") or {}).get("uid") or "CHE-000.000.001"
    persons = _persons_for_level(list(raw.get("persons_table") or []), level, seed_uid)
    seed_companies = copy.deepcopy(raw.get("seed_companies") or [])
    # Attach publications onto seed for deep UI that reads seed_companies[0]
    if seed_companies:
        seed_companies[0]["recent_publications"] = copy.deepcopy(
            raw.get("recent_publications") or []
        )
        seed_companies[0]["publication_count"] = raw.get("publication_count", 0)

    current_n = sum(1 for p in persons if (p.get("status") or "").lower() == "current")
    former_n = sum(1 for p in persons if (p.get("status") or "").lower() == "former")
    return {
        "level": level,
        "level_label": LEVEL_LABELS.get(level, ""),
        "level_labels": LEVEL_LABELS,
        "seed_companies": seed_companies,
        "errors": [],
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "seed_count": len(seed_companies),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "current_persons": current_n,
            "former_persons": former_n,
            "person_search": _person_search_stats(level),
            "demo_only": True,
        },
        "persons_table": persons,
        "demo_only": True,
        "cached": False,
        "cached_at": None,
    }


def build_demo_hr_network(*, company: str | None = None, uid: str | None = None) -> dict[str, Any]:
    """HR-network / first-load shape (level 2) from the same fixture."""
    data = build_demo_fraud_network(level=2)
    seed = (data.get("seed_companies") or [None])[0] or {}
    company_info = copy.deepcopy(_raw_fixture().get("company") or {})
    persons = []
    by_id = {
        n["id"].removeprefix("person:"): n
        for n in data.get("nodes") or []
        if n.get("type") == "person"
    }
    for p in data.get("persons_table") or []:
        pid = p.get("person_id") or p.get("id")
        row = {
            "id": pid,
            "name": p.get("name"),
            "roles": p.get("roles") or [],
            "residence": p.get("residence"),
            "nationality": p.get("nationality"),
            "heimatort": p.get("heimatort"),
            "gender": p.get("gender"),
            "status": p.get("status"),
            "source_date": None,
            "seed_company": p.get("seed_company"),
            "on_watchlist": p.get("on_watchlist"),
            "case_involved": p.get("case_involved"),
            "mandates": p.get("mandates") or [],
        }
        node = by_id.get(pid or "")
        if node:
            row["roles"] = node.get("roles") or row["roles"]
            row["residence"] = node.get("residence") or row["residence"]
            row["nationality"] = node.get("nationality") or row["nationality"]
            row["heimatort"] = node.get("heimatort") or row["heimatort"]
            row["gender"] = node.get("gender") or row["gender"]
            row["source_date"] = node.get("first_seen") or node.get("last_seen")
            row["on_watchlist"] = node.get("on_watchlist") or row["on_watchlist"]
            row["case_involved"] = node.get("case_involved") or row["case_involved"]
        persons.append(row)

    raw = _raw_fixture()
    return {
        "query": (company or uid or company_info.get("name") or company_info.get("uid")),
        "company": company_info,
        "persons": persons,
        "nodes": data.get("nodes") or [],
        "edges": data.get("edges") or [],
        "warnings": list(raw.get("warnings") or seed.get("warnings") or []),
        "mutation_analysis": raw.get("mutation_analysis") or seed.get("mutation_analysis"),
        "publication_count": raw.get("publication_count", 0),
        "recent_publications": copy.deepcopy(raw.get("recent_publications") or []),
        "search_matches": None,
        "level": data.get("level"),
        "stats": data.get("stats"),
        "persons_table": data.get("persons_table"),
        "seed_companies": data.get("seed_companies"),
        "demo_only": True,
    }
